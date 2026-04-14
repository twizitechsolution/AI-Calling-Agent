import os
import logging
import requests
import httpx
from datetime import datetime

logger = logging.getLogger("calendar-tools")

CAL_BASE = "https://api.cal.com/v1"


def get_cal_creds() -> dict:
    return {
        "api_key":  os.environ.get("CAL_API_KEY", ""),
        "event_id": int(os.environ.get("CAL_EVENT_TYPE_ID", "0") or "0"),
    }


# ─── Cal.com: Get available slots ─────────────────────────────────────────────

def get_available_slots(date_str: str) -> list:
    """
    Fetch open slots for a given date from Cal.com OR Google Calendar,
    depending on which is configured.
    date_str: "YYYY-MM-DD"
    """
    # Try Google Calendar first if configured (#36)
    gcal_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    gcal_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json")
    if gcal_id and os.path.exists(gcal_creds):
        try:
            return _get_slots_gcal(date_str, gcal_id, gcal_creds)
        except Exception as e:
            logger.warning(f"[GCAL] Falling back to Cal.com: {e}")

    # Default: Cal.com
    return _get_slots_calcom(date_str)


def _get_slots_calcom(date_str: str) -> list:
    """
    Fetch available slots — three-tier strategy:
      1. Cal.com v2 /slots/available
      2. Cal.com v1 /slots (fallback for legacy event type IDs)
      3. Synthetic business-hours slots (10:00–18:30 IST, every 30 min)
    """
    creds = get_cal_creds()
    api_key  = creds["api_key"]
    event_id = creds["event_id"]

    if not api_key or not event_id:
        logger.warning("[CAL] Missing CAL_API_KEY or CAL_EVENT_TYPE_ID — using synthetic slots")
        return _synthetic_slots(date_str)

    # ── Strategy 1: Cal.com v2 ────────────────────────────────────────────
    try:
        resp = requests.get(
            "https://api.cal.com/v2/slots/available",
            headers={
                "Authorization":   f"Bearer {api_key}",
                "cal-api-version": "2024-09-04",
                "Content-Type":    "application/json",
            },
            params={
                "eventId":     event_id,
                "start":       f"{date_str}T00:00:00.000Z",
                "end":         f"{date_str}T23:59:59.000Z",
            },
            timeout=8,
        )
        if not resp.ok:
            logger.warning(f"[CAL-v2] HTTP {resp.status_code}: {resp.text[:200]} — trying v1 fallback")
        else:
            raw_slots = (
                resp.json().get("data", {}).get("slots", {}).get(date_str, [])
            )
            if raw_slots is not None:   # empty list is valid (no slots that day)
                slots = _parse_slots(raw_slots)
                logger.info(f"[CAL-v2] {len(slots)} slots for {date_str}")
                return slots
    except Exception as e:
        logger.warning(f"[CAL-v2] Failed ({e}) — trying v1 fallback")

    # ── Strategy 2: Cal.com v1 (legacy event type IDs) ───────────────────
    try:
        resp = requests.get(
            f"{CAL_BASE}/slots",
            headers={"Content-Type": "application/json"},
            params={
                "apiKey":      api_key,
                "eventTypeId": event_id,
                "startTime":   f"{date_str}T00:00:00.000Z",
                "endTime":     f"{date_str}T23:59:59.000Z",
            },
            timeout=8,
        )
        if not resp.ok:
            logger.warning(f"[CAL-v1] HTTP {resp.status_code}: {resp.text[:200]} — using synthetic slots")
        else:
            raw_slots = resp.json().get("data", {}).get("slots", {}).get(date_str, [])
            if raw_slots is not None:
                slots = _parse_slots(raw_slots)
                logger.info(f"[CAL-v1] {len(slots)} slots for {date_str}")
                return slots
    except Exception as e:
        logger.warning(f"[CAL-v1] Failed ({e}) — using synthetic slots")

    # ── Strategy 3: Synthetic slots (business hours fallback) ─────────────
    logger.info(f"[CAL] Using synthetic business-hours slots for {date_str}")
    return _synthetic_slots(date_str)


def _parse_slots(raw_slots: list) -> list:
    """Convert raw Cal.com slot dicts to {time, label} dicts."""
    result = []
    for s in raw_slots:
        try:
            dt = datetime.fromisoformat(s["time"])
            hour = dt.strftime("%I").lstrip("0") or "12"
            label = f"{hour}:{dt.strftime('%M %p')}"
            result.append({"time": s["time"], "label": label})
        except Exception:
            pass
    return result


def _synthetic_slots(date_str: str) -> list:
    """
    Generate synthetic 30-minute slots between 10:00 and 18:30 IST
    for use when Cal.com APIs are unavailable.
    """
    import pytz
    from datetime import timedelta
    ist = pytz.timezone("Asia/Kolkata")
    try:
        day_start = ist.localize(datetime.strptime(f"{date_str} 10:00", "%Y-%m-%d %H:%M"))
        day_end   = ist.localize(datetime.strptime(f"{date_str} 19:00", "%Y-%m-%d %H:%M"))
    except ValueError:
        return []

    slots = []
    slot = day_start
    while slot < day_end:
        hour = slot.strftime("%I").lstrip("0") or "12"
        label = f"{hour}:{slot.strftime('%M %p')}"
        slots.append({"time": slot.isoformat(), "label": label})
        slot += timedelta(minutes=30)
    return slots


def _get_slots_gcal(date_str: str, calendar_id: str, creds_file: str) -> list:
    """
    Fetch busy slots from Google Calendar and compute free windows (#36).
    Requires: google-api-python-client, google-auth
    """
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        creds_file,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    service = build("calendar", "v3", credentials=creds)

    start = f"{date_str}T00:00:00+05:30"
    end   = f"{date_str}T23:59:59+05:30"

    result = service.freebusy().query(body={
        "timeMin": start,
        "timeMax": end,
        "items":   [{"id": calendar_id}],
    }).execute()

    busy_slots = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

    # Generate free 30-min slots between 10:00 and 19:00 IST
    import pytz
    from datetime import timedelta
    ist = pytz.timezone("Asia/Kolkata")
    day_start = ist.localize(datetime.strptime(f"{date_str} 10:00", "%Y-%m-%d %H:%M"))
    day_end   = ist.localize(datetime.strptime(f"{date_str} 19:00", "%Y-%m-%d %H:%M"))

    busy_ranges = []
    for b in busy_slots:
        bs = datetime.fromisoformat(b["start"]).astimezone(ist)
        be = datetime.fromisoformat(b["end"]).astimezone(ist)
        busy_ranges.append((bs, be))

    free_slots = []
    slot = day_start
    while slot < day_end:
        slot_end = slot + timedelta(minutes=30)
        is_busy = any(bs <= slot < be for bs, be in busy_ranges)
        if not is_busy:
            free_slots.append({
                "time":  slot.isoformat(),
                "label": f"{slot.strftime('%I').lstrip('0') or '12'}:{slot.strftime('%M %p')}",
            })
        slot = slot_end

    logger.info(f"[GCAL] {len(free_slots)} free slots for {date_str}")
    return free_slots


# ─── Create a booking ──────────────────────────────────────────────────────────

def create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """Synchronous wrapper — calls async_create_booking."""
    import asyncio
    try:
        return asyncio.get_event_loop().run_until_complete(
            async_create_booking(start_time, caller_name, caller_phone, notes)
        )
    except RuntimeError:
        return asyncio.run(async_create_booking(start_time, caller_name, caller_phone, notes))


async def async_create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """
    Book a slot — uses Google Calendar if configured, else Cal.com v2.
    start_time: ISO 8601 with IST offset e.g. "2026-02-24T10:00:00+05:30"
    Returns: {"success": bool, "booking_id": str|None, "message": str}
    """
    gcal_id    = os.environ.get("GOOGLE_CALENDAR_ID", "")
    gcal_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json")

    if gcal_id and os.path.exists(gcal_creds):
        return await _create_booking_gcal(start_time, caller_name, caller_phone, notes, gcal_id, gcal_creds)

    return await _create_booking_calcom(start_time, caller_name, caller_phone, notes)


def _extract_email(notes: str, caller_phone: str) -> str:
    """Pull email from notes if the agent collected one, else build a valid placeholder.
    Cal.com v2 rejects RFC-invalid domains like .placeholder.
    .invalid is an IANA-reserved TLD guaranteed never to exist (RFC 2606).
    """
    import re as _re
    m = _re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', notes or "")
    if m:
        return m.group()
    safe_phone = _re.sub(r'[^a-zA-Z0-9]', '', caller_phone)
    if not safe_phone:
        safe_phone = "unknown"
    return f"{safe_phone}@voiceagent.invalid"


async def _create_booking_calcom(
    start_time: str, caller_name: str, caller_phone: str, notes: str
) -> dict:
    creds = get_cal_creds()
    if not creds["api_key"] or not creds["event_id"]:
        logger.error("[CAL] CAL_API_KEY or CAL_EVENT_TYPE_ID not set — cannot create booking")
        return {"success": False, "booking_id": None, "message": "Cal.com credentials not configured"}

    email = _extract_email(notes, caller_phone)
    full_note = notes or f"Booked via AI voice agent. Phone: {caller_phone}"

    payload = {
        "eventTypeId": creds["event_id"],
        "start": start_time.replace(" ", "T"),
        "attendee": {
            "name":        caller_name,
            "email":       email,
            "timeZone":    "Asia/Kolkata",
            "language":    "en",
        },
        # metadata is required by Cal.com v2 (causes 400 if absent on some event types)
        "metadata": {},
        # additionalNotes is the built-in Cal.com field; custom 'notes' fields may 404
        "bookingFieldsResponses": {
            "additionalNotes": full_note,
        },
    }
    logger.info(f"[CAL] Creating booking: event={creds['event_id']} start={start_time} email={email}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.cal.com/v2/bookings",
                headers={
                    "Authorization":   f"Bearer {creds['api_key']}",
                    "cal-api-version": "2024-08-13",
                    "Content-Type":    "application/json",
                },
                json=payload,
            )
            if resp.status_code not in (200, 201):
                logger.error(f"[CAL] Booking failed HTTP {resp.status_code}: {resp.text[:400]}")
                return {"success": False, "booking_id": None, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json().get("data", {})
            uid  = data.get("uid") or data.get("id", "unknown")
            logger.info(f"[CAL] Booking created: uid={uid}")
            return {"success": True, "booking_id": uid, "message": "Booking confirmed"}
    except httpx.TimeoutException:
        return {"success": False, "booking_id": None, "message": "Booking timed out after 10s."}
    except Exception as e:
        logger.error(f"[CAL] Booking error: {e}")
        return {"success": False, "booking_id": None, "message": str(e)}


async def _create_booking_gcal(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str,
    calendar_id: str,
    creds_file: str,
) -> dict:
    """Create a Google Calendar event (#36)."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        from datetime import timedelta

        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=creds)

        dt_start = datetime.fromisoformat(start_time)
        dt_end   = dt_start + timedelta(minutes=30)

        event = {
            "summary":     f"Appointment — {caller_name}",
            "description": f"Phone: {caller_phone}\nNotes: {notes}\nBooked via Twizitech Voice Agent",
            "start":       {"dateTime": dt_start.isoformat(), "timeZone": "Asia/Kolkata"},
            "end":         {"dateTime": dt_end.isoformat(),   "timeZone": "Asia/Kolkata"},
            "attendees":   [{"displayName": caller_name, "comment": caller_phone}],
        }

        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        event_id = created.get("id", "unknown")
        logger.info(f"[GCAL] Event created: id={event_id}")
        return {"success": True, "booking_id": event_id, "message": "Google Calendar event created"}
    except Exception as e:
        logger.error(f"[GCAL] Create booking failed: {e}")
        return {"success": False, "booking_id": None, "message": str(e)}


# ─── Cancel a booking ──────────────────────────────────────────────────────────

def cancel_booking(booking_id: str, reason: str = "Cancelled by caller") -> dict:
    """Cancel a Cal.com booking by UID."""
    creds = get_cal_creds()
    try:
        resp = requests.delete(
            f"{CAL_BASE}/bookings/{booking_id}/cancel?apiKey={creds['api_key']}",
            headers={"Content-Type": "application/json"},
            json={"reason": reason},
            timeout=8,
        )
        resp.raise_for_status()
        logger.info(f"[CAL] Booking cancelled: {booking_id}")
        return {"success": True, "message": "Cancelled successfully"}
    except Exception as e:
        logger.error(f"[CAL] cancel_booking error: {e}")
        return {"success": False, "message": str(e)}
