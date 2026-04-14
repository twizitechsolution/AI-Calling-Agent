import os
import json
import logging
import certifi
import pytz
import re
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import Annotated

# Fix for SSL certificate verification on all platforms
os.environ["SSL_CERT_FILE"] = certifi.where()

# ── Sentry error tracking ──────────────────────────────────────────────────────
import sentry_sdk
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        integrations=[AsyncioIntegration()],
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("deepgram").setLevel(logging.WARNING)
logging.getLogger("elevenlabs").setLevel(logging.WARNING)

load_dotenv()
logger = logging.getLogger("inbound-agent")
logging.basicConfig(level=logging.INFO)

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.plugins import openai, silero

CONFIG_FILE = "config.json"

# ── Rate limiting ──────────────────────────────────────────────────────────────
_call_timestamps: dict = defaultdict(list)
RATE_LIMIT_CALLS  = 5
RATE_LIMIT_WINDOW = 3600  # 1 hour


def is_rate_limited(phone: str) -> bool:
    if phone in ("unknown", "demo"):
        return False
    now = time.time()
    _call_timestamps[phone] = [t for t in _call_timestamps[phone] if now - t < RATE_LIMIT_WINDOW]
    if len(_call_timestamps[phone]) >= RATE_LIMIT_CALLS:
        return True
    _call_timestamps[phone].append(now)
    return False


# ── Config loader ──────────────────────────────────────────────────────────────
def get_live_config(phone_number: str | None = None) -> dict:
    """Load config — tries per-client file first, then default, then config.json."""
    config = {}
    paths = []
    if phone_number and phone_number != "unknown":
        clean = phone_number.replace("+", "").replace(" ", "")
        paths.append(f"configs/{clean}.json")
    paths += ["configs/default.json", CONFIG_FILE]

    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                    logger.info(f"[CONFIG] Loaded: {path}")
                    break
            except Exception as e:
                logger.error(f"[CONFIG] Failed to read {path}: {e}")

    return {
        "agent_instructions":           config.get("agent_instructions", ""),
        "stt_min_endpointing_delay":    config.get("stt_min_endpointing_delay", 0.2),
        "llm_model":                    config.get("llm_model", "gpt-4o-mini"),
        "llm_provider":                 config.get("llm_provider", "openai"),
        "tts_provider":                 config.get("tts_provider", "elevenlabs"),
        "elevenlabs_voice_id":          config.get("elevenlabs_voice_id", os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")),
        "stt_provider":                 config.get("stt_provider", "deepgram"),
        "stt_language":                 config.get("stt_language", "multi"),
        "lang_preset":                  config.get("lang_preset", "multilingual"),
        "max_turns":                    config.get("max_turns", 25),
        "max_call_duration_seconds":    config.get("max_call_duration_seconds", int(os.getenv("MAX_CALL_DURATION_SECONDS", "600"))),
        # ── Silero VAD tuning params (configurable from config.json) ─────────
        "vad_threshold":                config.get("vad_threshold", 0.5),
        "vad_min_silence_ms":           config.get("vad_min_silence_ms", 550),
        "vad_min_speech_ms":            config.get("vad_min_speech_ms", 100),
        "vad_prefix_padding_ms":        config.get("vad_prefix_padding_ms", 300),
        **config,
    }


# ── Token counter ──────────────────────────────────────────────────────────────
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


# ── IST time context ───────────────────────────────────────────────────────────
def get_ist_time_context() -> str:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    today_str = now.strftime("%A, %B %d, %Y")
    time_str  = now.strftime("%I:%M %p")
    days_lines = []
    for i in range(7):
        day   = now + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime("%A"))
        days_lines.append(f"  {label}: {day.strftime('%A %d %B %Y')} → ISO {day.strftime('%Y-%m-%d')}")
    days_block = "\n".join(days_lines)
    return (
        f"\n\n[SYSTEM CONTEXT]\n"
        f"Current date & time: {today_str} at {time_str} IST\n"
        f"Resolve ALL relative day references using this table:\n{days_block}\n"
        f"Always use ISO dates when calling save_booking_intent. Appointments in IST (+05:30).]"
    )


# ── Language presets ───────────────────────────────────────────────────────────
LANGUAGE_PRESETS = {
    "hinglish":    {"label": "Hinglish (Hindi+English)", "instruction": "Speak in natural Hinglish — mix Hindi and English like educated Indians do. Default to Hindi but use English words when more natural."},
    "hindi":       {"label": "Hindi",                   "instruction": "Speak only in pure Hindi. Avoid English words wherever a Hindi equivalent exists."},
    "english":     {"label": "English (India)",         "instruction": "Speak only in Indian English with a warm, professional tone."},
    "tamil":       {"label": "Tamil",                   "instruction": "Speak only in Tamil. Use standard spoken Tamil for a professional context."},
    "telugu":      {"label": "Telugu",                  "instruction": "Speak only in Telugu. Use clear, polite spoken Telugu."},
    "gujarati":    {"label": "Gujarati",                "instruction": "Speak only in Gujarati. Use polite, professional Gujarati."},
    "bengali":     {"label": "Bengali",                 "instruction": "Speak only in Bengali (Bangla). Use standard, polite spoken Bengali."},
    "marathi":     {"label": "Marathi",                 "instruction": "Speak only in Marathi. Use polite, standard spoken Marathi."},
    "kannada":     {"label": "Kannada",                 "instruction": "Speak only in Kannada. Use clear, professional spoken Kannada."},
    "malayalam":   {"label": "Malayalam",               "instruction": "Speak only in Malayalam. Use polite, professional spoken Malayalam."},
    "multilingual":{"label": "Multilingual (Auto)",     "instruction": "Detect the caller's language from their first message and reply in that SAME language for the entire call. Supported: Hindi, Hinglish, English, Tamil, Telugu, Gujarati, Bengali, Marathi, Kannada, Malayalam. Switch if caller switches."},
}


def get_language_instruction(lang_preset: str) -> str:
    preset = LANGUAGE_PRESETS.get(lang_preset, LANGUAGE_PRESETS["multilingual"])
    return f"\n\n[LANGUAGE DIRECTIVE]\n{preset['instruction']}"


# ── External imports ───────────────────────────────────────────────────────────
import db
from calendar_tools import get_available_slots, create_booking, cancel_booking
from notify import (
    notify_booking_confirmed,
    notify_booking_cancelled,
    notify_call_no_booking,
    notify_agent_error,
)


# ══════════════════════════════════════════════════════════════════════════════
# TOOL CONTEXT — All AI-callable functions
# ══════════════════════════════════════════════════════════════════════════════

class AgentTools(llm.ToolContext):

    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone        = caller_phone
        self.caller_name         = caller_name
        self.booking_intent: dict | None = None
        self.sip_domain          = os.getenv("VOBIZ_SIP_DOMAIN")
        self.ctx_api             = None
        self.room_name           = None
        self._sip_identity       = None

    # ── Tool: Transfer to Human ───────────────────────────────────────────
    @llm.function_tool(description="Transfer this call to a human agent. Use if: caller asks for human, is angry, or query is outside scope.")
    async def transfer_call(self, reason: Annotated[str, "Reason for transfer (optional)"] = "Not specified") -> str:
        logger.info("[TOOL] transfer_call triggered")
        destination = os.getenv("DEFAULT_TRANSFER_NUMBER")
        if destination and self.sip_domain and "@" not in destination:
            clean_dest  = destination.replace("tel:", "").replace("sip:", "")
            destination = f"sip:{clean_dest}@{self.sip_domain}"
        if destination and not destination.startswith("sip:"):
            destination = f"sip:{destination}"
        try:
            if self.ctx_api and self.room_name and destination and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to=destination,
                        play_dialtone=False,
                    )
                )
                return "Transfer initiated successfully."
            return "Unable to transfer right now."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return "Unable to transfer right now."

    # ── Tool: End Call ────────────────────────────────────────────────────
    @llm.function_tool(description="End the call. Use ONLY when caller says bye/goodbye or after booking is fully confirmed.")
    async def end_call(self, reason: Annotated[str, "Reason for ending the call (optional)"] = "Not specified") -> str:
        logger.info("[TOOL] end_call triggered — hanging up.")
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to="tel:+00000000",
                        play_dialtone=False,
                    )
                )
        except Exception as e:
            logger.warning(f"[END-CALL] SIP hangup failed: {e}")
        return "Call ended."

    # ── Tool: Save Booking Intent ─────────────────────────────────────────
    @llm.function_tool(description="Save booking intent after caller confirms appointment. Call this ONCE after you have name, phone, email, date, time.")
    async def save_booking_intent(
        self,
        start_time:  Annotated[str,  "ISO 8601 datetime with 'T' separator e.g. '2026-03-01T10:00:00+05:30'"],
        caller_name: Annotated[str,  "Full name. Must be in English. Use 'unknown' if not provided."] = "unknown",
        caller_phone:Annotated[str,  "Phone number. Must be in English. Use 'unknown' if not provided."] = "unknown",
        notes:       Annotated[str,  "Any notes, email, or special requests. MUST be in English. NEVER use Chinese (e.g. no 未知). Use 'none' if none."] = "none",
    ) -> str:
        logger.info(f"[TOOL] save_booking_intent: {caller_name} at {start_time}")
        try:
            self.booking_intent = {
                "start_time":   start_time,
                "caller_name":  caller_name,
                "caller_phone": caller_phone,
                "notes":        notes,
            }
            self.caller_name = caller_name
            return f"Booking intent saved for {caller_name} at {start_time}. I'll confirm after the call."
        except Exception as e:
            logger.error(f"[TOOL] save_booking_intent failed: {e}")
            return "I had trouble saving the booking. Please try again."

    # ── Tool: Check Availability ──────────────────────────────────────────
    @llm.function_tool(description="Check available appointment slots for a given date. Call this when user asks about availability.")
    async def check_availability(
        self,
        date: Annotated[str, "Date to check in YYYY-MM-DD format e.g. '2026-03-01'"],
    ) -> str:
        logger.info(f"[TOOL] check_availability: date={date}")
        try:
            loop = asyncio.get_event_loop()
            slots = await loop.run_in_executor(None, get_available_slots, date)
            if not slots:
                return f"No available slots on {date}. Would you like to check another date?"
            slot_strings = [s.get("label", s.get("time", str(s))) for s in slots[:6]]
            return f"Available slots on {date}: {', '.join(slot_strings)} IST."
        except Exception as e:
            logger.error(f"[TOOL] check_availability failed: {e}")
            return "I'm having trouble checking the calendar right now."

    # ── Tool: Business Hours ──────────────────────────────────────────────
    @llm.function_tool(description="Check if the business is currently open and what the operating hours are.")
    async def get_business_hours(self, unused: Annotated[str, "Ignored, do not use"] = "None") -> str:
        ist  = pytz.timezone("Asia/Kolkata")
        now  = datetime.now(ist)
        hours = {
            0: ("Monday",    "10:00", "19:00"),
            1: ("Tuesday",   "10:00", "19:00"),
            2: ("Wednesday", "10:00", "19:00"),
            3: ("Thursday",  "10:00", "19:00"),
            4: ("Friday",    "10:00", "19:00"),
            5: ("Saturday",  "10:00", "17:00"),
            6: ("Sunday",    None,    None),
        }
        day_name, open_t, close_t = hours[now.weekday()]
        current_time = now.strftime("%H:%M")
        if open_t is None:
            return "We are closed on Sundays. Next opening: Monday 10:00 AM IST."
        if open_t <= current_time <= close_t:
            return f"We are OPEN. Today ({day_name}): {open_t}–{close_t} IST."
        return f"We are CLOSED. Today ({day_name}): {open_t}–{close_t} IST."


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ══════════════════════════════════════════════════════════════════════════════

class InboundAssistant(Agent):

    def __init__(self, agent_tools: AgentTools, live_config: dict | None = None, caller_name: str = ""):
        tools = llm.find_function_tools(agent_tools)
        self._live_config  = live_config or {}
        self._caller_name  = caller_name

        base_instructions = self._live_config.get("agent_instructions", "")
        ist_context       = get_ist_time_context()
        lang_preset       = self._live_config.get("lang_preset", "multilingual")
        lang_instruction  = get_language_instruction(lang_preset)
        final_instructions = base_instructions + ist_context + lang_instruction

        # Log token count — warn if > 600 tokens (voice latency risk)
        token_count = count_tokens(final_instructions)
        logger.info(f"[PROMPT] System prompt: {token_count} tokens")
        if token_count > 600:
            logger.warning(f"[PROMPT] Prompt exceeds 600 tokens — consider trimming for latency")

        super().__init__(instructions=final_instructions, tools=tools)

    async def on_enter(self):
        # Personalize greeting with caller name if SIP provided it
        default_greeting = self._live_config.get(
            "first_line",
            "Namaste! This is Aryan from Twizitech Solution — we help businesses automate with AI. "
            "Hmm, may I ask what kind of business you run?"
        )
        if self._caller_name and self._caller_name not in ("", "Caller", "Unknown"):
            # Personalized greeting — use caller's name from SIP caller ID
            greeting_instruction = (
                f"Greet the caller by name. Say something like: "
                f"'Namaste {self._caller_name}! This is Aryan from Twizitech Solution — "
                f"we help businesses automate with AI. May I ask what kind of business you run?'"
            )
        else:
            greeting_instruction = f"Say exactly this phrase: '{default_greeting}'"

        await self.session.generate_reply(instructions=greeting_instruction)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

agent_is_speaking = False


async def entrypoint(ctx: JobContext):
    global agent_is_speaking

    # ── Connect ───────────────────────────────────────────────────────────
    await ctx.connect()
    logger.info(f"[ROOM] Connected: {ctx.room.name}")

    # ── Extract caller info from SIP ──────────────────────────────────────
    phone_number = None
    caller_name  = ""
    caller_phone = "unknown"

    # Try metadata first (outbound dispatch)
    metadata = ctx.job.metadata or ""
    if metadata:
        try:
            meta = json.loads(metadata)
            phone_number = meta.get("phone_number")
        except Exception:
            pass

    # Extract from SIP participants — TVOBIZ sends caller name + number in SIP headers
    for identity, participant in ctx.room.remote_participants.items():
        if participant.name and participant.name not in ("", "Caller", "Unknown"):
            caller_name = participant.name
            logger.info(f"[CALLER-ID] Name from SIP: {caller_name}")
        if not phone_number:
            attr = participant.attributes or {}
            phone_number = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if not phone_number and "+" in identity:
            m = re.search(r"\+\d{7,15}", identity)
            if m:
                phone_number = m.group()

    caller_phone = phone_number or "unknown"

    # ── Rate limiting ─────────────────────────────────────────────────────
    if is_rate_limited(caller_phone):
        logger.warning(f"[RATE-LIMIT] Blocked {caller_phone} — too many calls in 1h")
        return

    # ── Load config ───────────────────────────────────────────────────────
    live_config  = get_live_config(caller_phone)
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.2)
    llm_model     = live_config.get("llm_model", "gpt-4o-mini")
    llm_provider  = live_config.get("llm_provider", "openai")
    tts_provider  = live_config.get("tts_provider", "elevenlabs")
    stt_provider  = live_config.get("stt_provider", "deepgram")
    max_turns     = live_config.get("max_turns", 25)
    max_duration  = live_config.get("max_call_duration_seconds", int(os.getenv("MAX_CALL_DURATION_SECONDS", "600")))

    # Silero VAD tuning values from config
    vad_threshold       = float(live_config.get("vad_threshold", 0.5))
    vad_min_silence_ms  = int(live_config.get("vad_min_silence_ms", 550))
    vad_min_speech_ms   = int(live_config.get("vad_min_speech_ms", 100))
    vad_prefix_ms       = int(live_config.get("vad_prefix_padding_ms", 300))

    # Override OS env vars from UI config (for dashboard-controlled keys)
    for key in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY",
                "GROQ_API_KEY", "ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY",
                "ELEVENLABS_VOICE_ID", "SARVAM_API_KEY", "CAL_API_KEY", "CAL_EVENT_TYPE_ID",
                "TELEGRAM_BOT_TOKEN", "SUPABASE_URL", "SUPABASE_KEY"]:
        val = live_config.get(key.lower(), "")
        if val:
            os.environ[key] = val

    # ── Caller memory — load last call summary ─────────────────────────────
    async def get_caller_history(phone: str) -> str:
        if phone == "unknown":
            return ""
        try:
            sb = db.get_supabase()
            if not sb:
                return ""
            result = (sb.table("call_logs")
                        .select("summary, created_at")
                        .eq("phone_number", phone)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute())
            if result.data:
                last = result.data[0]
                return f"\n\n[CALLER HISTORY: Last call {last['created_at'][:10]}. Summary: {last['summary']}]"
        except Exception as e:
            logger.warning(f"[MEMORY] Could not load history: {e}")
        return ""

    caller_history = await get_caller_history(caller_phone)
    if caller_history:
        logger.info(f"[MEMORY] Loaded caller history for {caller_phone}")
        live_config["agent_instructions"] = (live_config.get("agent_instructions", "") + caller_history)

    # ── Instantiate tools ─────────────────────────────────────────────────
    agent_tools = AgentTools(caller_phone=caller_phone, caller_name=caller_name)
    agent_tools._sip_identity = (
        f"sip_{caller_phone.replace('+', '')}" if phone_number else "inbound_caller"
    )
    agent_tools.ctx_api   = ctx.api
    agent_tools.room_name = ctx.room.name

    # ══════════════════════════════════════════════════════════════════════
    # BUILD STT — Deepgram Nova-2 (streaming, multilingual)
    # ══════════════════════════════════════════════════════════════════════
    if stt_provider == "deepgram":
        try:
            from livekit.plugins import deepgram
            agent_stt = deepgram.STT(
                model="nova-2-general",
                # "multi" enables Deepgram's automatic language detection across
                # 30+ languages — critical for Indian multilingual callers
                language="multi",
                # interim_results=True sends partial transcripts as the caller speaks,
                # enabling the pipeline to start processing before the utterance ends
                interim_results=True,
                # smart_format adds punctuation, numbers, and capitalization automatically
                smart_format=True,
                # punctuate adds sentence-ending punctuation for cleaner LLM input
                punctuate=True,
                # filler_words=False strips "um", "uh", "hmm" before passing to LLM
                filler_words=False,
                # utterance_end_ms: ms of silence after which Deepgram marks the
                # utterance as complete and flushes the final transcript
                utterance_end_ms="1000",
            )
            logger.info("[STT] Using Deepgram Nova-2 — streaming, multilingual")
        except ImportError:
            logger.error("[STT] livekit-plugins-deepgram not installed! Run: pip install livekit-plugins-deepgram")
            raise
    else:
        # Fallback: Sarvam STT (for legacy compatibility)
        try:
            from livekit.plugins import sarvam
            agent_stt = sarvam.STT(
                language=live_config.get("stt_language", "unknown"),
                model="saaras:v3",
                mode="translate",
                flush_signal=True,
                sample_rate=16000,
            )
            logger.info("[STT] Fallback: Sarvam Saaras v3")
        except ImportError:
            logger.error("[STT] No STT plugin available. Install livekit-plugins-deepgram.")
            raise

    # ══════════════════════════════════════════════════════════════════════
    # BUILD LLM — OpenAI gpt-4o-mini (streaming)
    # ══════════════════════════════════════════════════════════════════════
    if llm_provider == "openai":
        agent_llm = openai.LLM(
            model=llm_model or "gpt-4o-mini",
            # max_completion_tokens caps response length — critical for voice:
            # shorter responses = lower latency + more natural conversation
            # 180 tokens ≈ 2-3 spoken sentences which is the voice sweet spot
            max_completion_tokens=180,
        )
        logger.info(f"[LLM] Using OpenAI: {llm_model} (streaming)")
    elif llm_provider == "groq":
        agent_llm = openai.LLM(
            model=llm_model or "llama-3.3-70b-versatile",
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ.get("GROQ_API_KEY", ""),
            max_completion_tokens=180,
        )
        logger.info(f"[LLM] Using Groq: {llm_model}")
    elif llm_provider == "claude":
        agent_llm = openai.LLM(
            model=llm_model or "claude-haiku-3-5-latest",
            base_url="https://api.anthropic.com/v1/",
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            max_completion_tokens=180,
        )
        logger.info(f"[LLM] Using Claude: {llm_model}")
    else:
        # Default fallback to OpenAI
        agent_llm = openai.LLM(model="gpt-4o-mini", max_completion_tokens=180)
        logger.info("[LLM] Fallback to OpenAI gpt-4o-mini")

    # ══════════════════════════════════════════════════════════════════════
    # BUILD TTS — ElevenLabs Turbo v2.5 (streaming)
    # ══════════════════════════════════════════════════════════════════════
    el_voice_id = live_config.get("elevenlabs_voice_id") or os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

    if tts_provider == "elevenlabs":
        try:
            from livekit.plugins import elevenlabs
            agent_tts = elevenlabs.TTS(
                # eleven_turbo_v2_5 is ElevenLabs' lowest-latency model
                # It starts generating audio within ~200ms of receiving text
                model="eleven_turbo_v2_5",
                voice_id=el_voice_id,
                # ElevenLabs streaming: audio chunks are sent to LiveKit as they
                # are generated, so the caller hears speech before the LLM finishes
                # responding — this is the key to natural-feeling conversation
            )
            logger.info(f"[TTS] Using ElevenLabs Turbo v2.5 — voice: {el_voice_id}")
        except ImportError:
            logger.error("[TTS] livekit-plugins-elevenlabs not installed! Run: pip install livekit-plugins-elevenlabs")
            raise
    elif tts_provider == "sarvam":
        try:
            from livekit.plugins import sarvam
            agent_tts = sarvam.TTS(
                target_language_code=live_config.get("tts_language", "hi-IN"),
                model="bulbul:v3",
                speaker=live_config.get("tts_voice", "kavya"),
                speech_sample_rate=24000,
            )
            logger.info(f"[TTS] Fallback: Sarvam Bulbul v3")
        except ImportError:
            logger.error("[TTS] No TTS plugin available.")
            raise
    else:
        # Default to ElevenLabs
        try:
            from livekit.plugins import elevenlabs
            agent_tts = elevenlabs.TTS(
                model="eleven_turbo_v2_5",
                voice_id=el_voice_id,
            )
            logger.info(f"[TTS] Default: ElevenLabs Turbo v2.5 — voice: {el_voice_id}")
        except ImportError:
            logger.error("[TTS] ElevenLabs not available as default TTS.")
            raise

    # ══════════════════════════════════════════════════════════════════════
    # BUILD VAD — Silero (fully tuned for telephony)
    # ══════════════════════════════════════════════════════════════════════
    #
    # These parameters directly control how the agent handles turn-taking.
    # Getting these right is the difference between a natural conversation
    # and one that constantly cuts the caller off or waits too long.
    #
    agent_vad = silero.VAD.load(
        # activation_threshold: probability score (0.0–1.0) above which audio
        # is classified as speech. Lower = more sensitive (catches quiet speech
        # but also more background noise). Higher = less sensitive.
        # TUNING: For noisy environments (street, speakerphone) try 0.6–0.7
        #         For quiet environments (office, headset) try 0.4–0.5
        activation_threshold=vad_threshold,

        # min_speech_duration: minimum continuous speech (in seconds) required
        # to register as a valid utterance. Filters out short coughs, clicks,
        # and background noise spikes that otherwise trigger false turns.
        # TUNING: 0.05–0.1s is ideal to catch even brief acknowledgments ("Ha")
        min_speech_duration=vad_min_speech_ms / 1000.0,

        # min_silence_duration: how long (in seconds) of silence after speech
        # before the agent considers the caller's turn complete and starts
        # processing. TOO LOW = cuts off caller mid-thought. TOO HIGH = awkward.
        # TUNING: 0.4s is aggressive (fast), 0.6s is polite, 0.8s is very patient.
        #         For Indian accents and pacing, 0.55s is a good baseline.
        min_silence_duration=vad_min_silence_ms / 1000.0,

        # prefix_padding_duration: audio (in seconds) to include BEFORE the
        # detected speech onset. Prevents clipping the first syllable of a word.
        # TUNING: 0.3s is the recommended default for telephony SIP calls.
        prefix_padding_duration=vad_prefix_ms / 1000.0,

        # max_buffered_speech: hard cap (in seconds) on the speech buffer.
        # Prevents runaway memory usage if VAD fails to detect silence.
        # This is a safety net — normal calls should never hit this.
        max_buffered_speech=60.0,
    )
    logger.info(
        f"[VAD] Silero configured: threshold={vad_threshold}, "
        f"min_silence={vad_min_silence_ms}ms, min_speech={vad_min_speech_ms}ms"
    )

    # ── Build agent ───────────────────────────────────────────────────────
    agent = InboundAssistant(
        agent_tools=agent_tools,
        live_config=live_config,
        caller_name=caller_name,
    )

    # ── Noise cancellation (BVC — Background Voice Cancellation) ──────────
    try:
        from livekit.agents import noise_cancellation as nc
        _noise_cancel = nc.BVC()
        logger.info("[AUDIO] BVC noise cancellation enabled")
    except Exception:
        _noise_cancel = None
        logger.info("[AUDIO] BVC not available — running without noise cancellation")

    room_input = RoomInputOptions(close_on_disconnect=False)
    if _noise_cancel:
        try:
            room_input = RoomInputOptions(close_on_disconnect=False, noise_cancellation=_noise_cancel)
        except Exception:
            room_input = RoomInputOptions(close_on_disconnect=False)

    # ══════════════════════════════════════════════════════════════════════
    # BUILD SESSION — End-to-end streaming pipeline
    # ══════════════════════════════════════════════════════════════════════
    #
    # Pipeline flow: Caller audio → Deepgram STT (streaming) → Silero VAD
    #  → OpenAI gpt-4o-mini (streaming tokens) → ElevenLabs TTS (streaming
    #  audio bytes) → LiveKit WebRTC → Caller's ear
    #
    # Each stage begins processing as soon as data arrives from the previous
    # stage — no stage waits for the previous to complete. This is what makes
    # the conversation feel instant and natural.
    #
    session = AgentSession(
        stt=agent_stt,
        llm=agent_llm,
        tts=agent_tts,
        vad=agent_vad,
        # VAD-based turn detection: Silero determines when the caller has
        # finished speaking, replacing the STT endpointing approach.
        # This is more accurate for diverse accents and noisy environments.
        turn_detection="vad",
        # min_endpointing_delay: additional silence (seconds) to wait after
        # VAD marks silence, before committing the transcript to LLM.
        # Acts as a final debounce to prevent cutting off trailing words.
        min_endpointing_delay=float(delay_setting),
        # allow_interruptions: if True, when the caller speaks while the agent
        # is talking, the agent stops immediately and listens.
        # This is CRITICAL for natural conversation — never set to False.
        allow_interruptions=True,
        # interrupt_min_words: caller must say at least this many words before
        # the agent stops mid-speech. Prevents single-word noises ("Hmm", "Ha")
        # from cutting off the agent unnecessarily.
        # TUNING: 1 = very sensitive (stops on any word), 2-3 = balanced
        interrupt_min_words=2,
        # interrupt_speech_duration: caller must speak for this many seconds
        # continuously before the interrupt is triggered. Works with min_words.
        # TUNING: 0.3s = sensitive, 0.5s = balanced, 0.8s = conservative
        interrupt_speech_duration=0.5,
    )

    await session.start(room=ctx.room, agent=agent, room_input_options=room_input)

    # ── TTS pre-warm (reduces first-response latency) ─────────────────────
    try:
        import inspect
        _prewarm = session.tts.prewarm()
        if inspect.isawaitable(_prewarm):
            await _prewarm
        logger.info("[TTS] Pre-warmed successfully")
    except Exception as e:
        logger.debug(f"[TTS] Pre-warm skipped: {e}")

    logger.info("[AGENT] Session live — waiting for caller audio.")
    call_start_time = datetime.now()

    # ── Call recording → Supabase S3 ──────────────────────────────────────
    egress_id = None
    try:
        rec_api = api.LiveKitAPI(
            url=os.environ["LIVEKIT_URL"],
            api_key=os.environ["LIVEKIT_API_KEY"],
            api_secret=os.environ["LIVEKIT_API_SECRET"],
        )
        egress_resp = await rec_api.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=f"recordings/{ctx.room.name}.ogg",
                    s3=api.S3Upload(
                        access_key=os.environ["SUPABASE_S3_ACCESS_KEY"],
                        secret=os.environ["SUPABASE_S3_SECRET_KEY"],
                        bucket="call-recordings",
                        region=os.environ.get("SUPABASE_S3_REGION", "ap-south-1"),
                        endpoint=os.environ["SUPABASE_S3_ENDPOINT"],
                        force_path_style=True,
                    )
                )]
            )
        )
        egress_id = egress_resp.egress_id
        await rec_api.aclose()
        logger.info(f"[RECORDING] Started egress: {egress_id}")
    except Exception as e:
        logger.warning(f"[RECORDING] Failed to start recording: {e}")

    # ── Upsert active_calls ────────────────────────────────────────────────
    async def upsert_active_call(status: str):
        try:
            sb = db.get_supabase()
            if sb:
                sb.table("active_calls").upsert({
                    "room_id":      ctx.room.name,
                    "phone":        caller_phone,
                    "caller_name":  caller_name,
                    "status":       status,
                    "last_updated": datetime.utcnow().isoformat(),
                }).execute()
        except Exception as e:
            logger.debug(f"[ACTIVE-CALL] {e}")

    await upsert_active_call("active")

    # ── Real-time transcript streaming to Supabase ─────────────────────────
    async def _log_transcript(role: str, content: str):
        try:
            sb = db.get_supabase()
            if sb:
                sb.table("call_transcripts").insert({
                    "call_room_id": ctx.room.name,
                    "phone":        caller_phone,
                    "role":         role,
                    "content":      content,
                }).execute()
        except Exception as e:
            logger.debug(f"[TRANSCRIPT-STREAM] {e}")

    # ── Turn counter + state ───────────────────────────────────────────────
    turn_count      = 0
    interrupt_count = 0

    # ══════════════════════════════════════════════════════════════════════
    # DURATION WATCHDOG — Cost protection
    # ══════════════════════════════════════════════════════════════════════
    #
    # Runs in the background. When MAX_CALL_DURATION_SECONDS is reached,
    # the agent politely wraps up and hangs up — preventing runaway API costs
    # from stuck calls, silent lines, or looping conversations.
    #
    async def duration_watchdog():
        logger.info(f"[WATCHDOG] Max call duration set to {max_duration}s")
        await asyncio.sleep(max_duration)
        logger.warning(f"[WATCHDOG] Max call duration {max_duration}s reached — wrapping up")
        try:
            await session.generate_reply(
                instructions=(
                    "The call has reached its maximum duration. Be warm and brief: "
                    "thank the caller, tell them to call back anytime if they need more help, "
                    "and say a friendly goodbye. Keep it to 1 sentence."
                )
            )
            # Wait for TTS to finish before disconnecting
            await asyncio.sleep(6)
            await agent_tools.end_call("Max duration reached")
        except Exception as e:
            logger.warning(f"[WATCHDOG] Wrap-up failed: {e}")

    watchdog_task = asyncio.create_task(duration_watchdog())

    # ── Session event handlers ─────────────────────────────────────────────
    @session.on("agent_speech_started")
    def _agent_speech_started(ev):
        global agent_is_speaking
        agent_is_speaking = True

    @session.on("agent_speech_finished")
    def _agent_speech_finished(ev):
        global agent_is_speaking
        agent_is_speaking = False

    # Barge-in / interruption tracking
    @session.on("agent_speech_interrupted")
    def _on_interrupted(ev):
        nonlocal interrupt_count
        global agent_is_speaking
        interrupt_count += 1
        agent_is_speaking = False
        logger.info(f"[BARGE-IN] Agent interrupted by caller. Total interruptions: {interrupt_count}")

    # Filler words to filter (don't trigger LLM on these)
    FILLER_WORDS = {
        "okay.", "okay", "ok", "uh", "hmm", "hm", "yeah", "yes",
        "no", "um", "ah", "oh", "right", "sure", "fine", "good",
        "haan", "han", "theek", "theek hai", "accha", "ji", "ha",
    }

    @session.on("user_speech_committed")
    def on_user_speech_committed(ev):
        nonlocal turn_count
        global agent_is_speaking

        transcript = ev.user_transcript.strip()
        transcript_lower = transcript.lower().rstrip(".")

        if agent_is_speaking:
            logger.debug(f"[FILTER-ECHO] Dropped: '{transcript}'")
            return
        if not transcript or len(transcript) < 3:
            return
        if transcript_lower in FILLER_WORDS:
            logger.debug(f"[FILTER-FILLER] Dropped: '{transcript}'")
            return

        # Stream transcript to Supabase in real-time
        asyncio.create_task(_log_transcript("user", transcript))

        # Turn counter + auto-close at limit
        turn_count += 1
        logger.info(f"[TRANSCRIPT] Turn {turn_count}/{max_turns}: '{transcript}'")
        if turn_count >= max_turns:
            logger.info(f"[LIMIT] Reached {max_turns} turns — wrapping up")
            asyncio.create_task(
                session.generate_reply(
                    instructions="Politely wrap up: thank the caller, say they can call back anytime, and say a warm goodbye."
                )
            )

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        global agent_is_speaking
        logger.info(f"[HANGUP] Participant disconnected: {participant.identity}")
        agent_is_speaking = False
        watchdog_task.cancel()
        asyncio.create_task(unified_shutdown_hook(ctx))

    # ══════════════════════════════════════════════════════════════════════
    # POST-CALL SHUTDOWN HOOK — Analytics, booking, notifications
    # ══════════════════════════════════════════════════════════════════════

    _shutdown_started = False

    async def unified_shutdown_hook(shutdown_ctx: JobContext):
        nonlocal _shutdown_started
        if _shutdown_started:
            logger.debug("[SHUTDOWN] Already running — skipping duplicate call.")
            return
        _shutdown_started = True
        logger.info("[SHUTDOWN] Sequence started.")

        # Cancel watchdog if still running
        if not watchdog_task.done():
            watchdog_task.cancel()

        duration = int((datetime.now() - call_start_time).total_seconds())

        # ── Process booking ───────────────────────────────────────────────
        booking_status_msg = "No booking"
        if agent_tools.booking_intent:
            from calendar_tools import async_create_booking
            intent = agent_tools.booking_intent
            result = await async_create_booking(
                start_time=intent["start_time"],
                caller_name=intent["caller_name"] or "Unknown Caller",
                caller_phone=intent["caller_phone"],
                notes=intent["notes"],
            )
            if result.get("success"):
                notify_booking_confirmed(
                    caller_name=intent["caller_name"],
                    caller_phone=intent["caller_phone"],
                    booking_time_iso=intent["start_time"],
                    booking_id=result.get("booking_id"),
                    notes=intent["notes"],
                    tts_voice=f"ElevenLabs/{el_voice_id}",
                    ai_summary="",
                )
                booking_status_msg = f"Booking Confirmed: {result.get('booking_id')}"
            else:
                booking_status_msg = f"Booking Failed: {result.get('message')}"
        else:
            notify_call_no_booking(
                caller_name=agent_tools.caller_name,
                caller_phone=agent_tools.caller_phone,
                call_summary="Caller did not schedule during this call.",
                tts_voice=f"ElevenLabs/{el_voice_id}",
                duration_seconds=duration,
            )

        # ── Build transcript ──────────────────────────────────────────────
        transcript_text = ""
        try:
            messages = agent.chat_ctx.messages
            if callable(messages):
                messages = messages()
            lines = []
            for msg in messages:
                if getattr(msg, "role", None) in ("user", "assistant"):
                    content = getattr(msg, "content", "")
                    if isinstance(content, list):
                        content = " ".join(str(c) for c in content if isinstance(c, str))
                    lines.append(f"[{msg.role.upper()}] {content}")
            transcript_text = "\n".join(lines)
        except Exception as e:
            logger.error(f"[SHUTDOWN] Transcript read failed: {e}")
            transcript_text = "unavailable"

        # ── Sentiment analysis via OpenAI ──────────────────────────────────
        sentiment = "unknown"
        if transcript_text and transcript_text != "unavailable":
            try:
                import openai as _oai
                _client = _oai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
                resp = await _client.chat.completions.create(
                    model="gpt-4o-mini",
                    max_tokens=5,
                    messages=[{"role": "user", "content":
                        f"Reply with exactly ONE word — positive, neutral, negative, or frustrated.\n\n{transcript_text[:800]}"}]
                )
                raw_sentiment = resp.choices[0].message.content.strip().lower()
                sentiment = raw_sentiment.split()[0].rstrip(".,!?") if raw_sentiment else "neutral"
                logger.info(f"[SENTIMENT] {sentiment}")
            except Exception as e:
                logger.warning(f"[SENTIMENT] Failed: {e}")

        # ── Cost estimation ───────────────────────────────────────────────
        def estimate_cost(dur: int, chars: int) -> float:
            # Deepgram Nova-2: $0.0043/min
            # OpenAI gpt-4o-mini: ~$0.001/1k tokens in+out
            # ElevenLabs Turbo v2.5: $0.0015/1k chars
            return round(
                (dur / 60) * 0.0043 +                    # STT
                (chars / 4000) * 0.001 +                  # LLM (approx tokens)
                (len(transcript_text) / 1000) * 0.0015,   # TTS
                5
            )

        estimated_cost = estimate_cost(duration, len(transcript_text))
        logger.info(f"[COST] Estimated: ${estimated_cost}")

        # ── Analytics timestamps ──────────────────────────────────────────
        ist = pytz.timezone("Asia/Kolkata")
        if call_start_time.tzinfo is None:
            call_dt = ist.localize(call_start_time)
        else:
            call_dt = call_start_time.astimezone(ist)

        # ── Stop call recording ───────────────────────────────────────────
        recording_url = (
            f"{os.environ.get('SUPABASE_URL', '')}/storage/v1/object/public/"
            f"call-recordings/recordings/{ctx.room.name}.ogg"
        ) if egress_id else ""

        if egress_id:
            stop_api = api.LiveKitAPI(
                url=os.environ["LIVEKIT_URL"],
                api_key=os.environ["LIVEKIT_API_KEY"],
                api_secret=os.environ["LIVEKIT_API_SECRET"],
            )
            try:
                await stop_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
                logger.info(f"[RECORDING] Stopped. URL: {recording_url}")
            except Exception as e:
                err_str = str(e)
                if "EGRESS_FAILED" in err_str or "failed_precondition" in err_str:
                    logger.info(f"[RECORDING] Egress already ended. URL preserved.")
                else:
                    logger.warning(f"[RECORDING] Stop failed: {e}")
                    recording_url = ""
            finally:
                await stop_api.aclose()

        # ── Update active_calls to completed ─────────────────────────────
        await upsert_active_call("completed")

        # ── n8n webhook ───────────────────────────────────────────────────
        _n8n_url = os.getenv("N8N_WEBHOOK_URL")
        if _n8n_url:
            try:
                import httpx
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: httpx.post(_n8n_url, json={
                        "event":            "call_completed",
                        "phone":            caller_phone,
                        "caller_name":      agent_tools.caller_name,
                        "duration":         duration,
                        "booked":           bool(agent_tools.booking_intent),
                        "sentiment":        sentiment,
                        "summary":          booking_status_msg,
                        "recording_url":    recording_url,
                        "interrupt_count":  interrupt_count,
                        "stack":            "deepgram+openai+elevenlabs",
                    }, timeout=5.0)
                )
                logger.info("[N8N] Webhook triggered")
            except Exception as e:
                logger.warning(f"[N8N] Webhook failed: {e}")

        # ── Save to Supabase ──────────────────────────────────────────────
        from db import save_call_log
        save_call_log(
            phone=caller_phone,
            duration=duration,
            transcript=transcript_text,
            summary=booking_status_msg,
            recording_url=recording_url,
            caller_name=agent_tools.caller_name or "",
            sentiment=sentiment,
            estimated_cost_usd=estimated_cost,
            call_date=call_dt.date().isoformat(),
            call_hour=call_dt.hour,
            call_day_of_week=call_dt.strftime("%A"),
            was_booked=bool(agent_tools.booking_intent),
            interrupt_count=interrupt_count,
        )

    ctx.add_shutdown_callback(unified_shutdown_hook)


# ══════════════════════════════════════════════════════════════════════════════
# WORKER ENTRY
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="inbound-caller",
    ))
