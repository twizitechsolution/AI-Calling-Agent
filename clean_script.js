 ──────────────────────────────────────────────────────────────
function goTo(pageId, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + pageId).classList.add('active');
  el.classList.add('active');
}

// ── Stats & Dashboard ───────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const [stats, logs] = await Promise.all([
      fetch('/api/stats').then(r => r.json()),
      fetch('/api/logs').then(r => r.json())
    ]);
    document.getElementById('stat-calls').textContent = stats.total_calls ?? '—';
    document.getElementById('stat-bookings').textContent = stats.total_bookings ?? '—';
    document.getElementById('stat-duration').textContent = stats.avg_duration ? stats.avg_duration + 's' : '—';
    document.getElementById('stat-rate').textContent = stats.booking_rate ? stats.booking_rate + '%' : '—';

    const tbody = document.getElementById('dash-table-body');
    if (!logs || logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">No calls yet. Make a test call!</td></tr>';
      return;
    }
    tbody.innerHTML = logs.slice(0, 10).map(log => `
      <tr>
        <td style="color:var(--muted)">${new Date(log.created_at).toLocaleString()}</td>
        <td style="font-weight:600">${log.phone_number || 'Unknown'}</td>
        <td>${log.duration_seconds || 0}s</td>
        <td>${badgeFor(log.summary)}</td>
        <td>
          ${log.id ? `<a style="color:var(--accent);font-size:12px;text-decoration:none;" href="/api/logs/${log.id}/transcript" download="transcript_${log.id}.txt">⬇ Download</a>` : ''}
        </td>
      </tr>`).join('');
  } catch(e) {
    document.getElementById('dash-table-body').innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Could not load data — check Supabase credentials.</td></tr>';
  }
}

function badgeFor(summary) {
  if (!summary) return '<span class="badge badge-gray">Ended</span>';
  if (summary.toLowerCase().includes('confirm')) return '<span class="badge badge-green">✓ Booked</span>';
  if (summary.toLowerCase().includes('cancel')) return '<span class="badge badge-yellow">✗ Cancelled</span>';
  return '<span class="badge badge-gray">Completed</span>';
}

// ── Call Logs ───────────────────────────────────────────────────────────────
async function loadLogs() {
  const tbody = document.getElementById('logs-table-body');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr>';
  try {
    const logs = await fetch('/api/logs').then(r => r.json());
    if (!logs || logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">No call logs found.</td></tr>';
      return;
    }
    tbody.innerHTML = logs.map(log => `
      <tr>
        <td style="color:var(--muted);white-space:nowrap">${new Date(log.created_at).toLocaleString()}</td>
        <td style="font-weight:600">${log.phone_number || 'Unknown'}</td>
        <td>${log.duration_seconds || 0}s</td>
        <td>${badgeFor(log.summary)}</td>
        <td style="color:var(--muted);font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${log.summary || ''}">${log.summary || '—'}</td>
        <td>
          ${log.id ? `<a class="btn btn-ghost btn-sm" style="text-decoration:none;" href="/api/logs/${log.id}/transcript" download="transcript_${log.id}.txt">⬇ Transcript</a>` : '—'}
          ${log.recording_url ? `<a class="btn btn-ghost btn-sm" style="text-decoration:none;margin-left:4px;" href="${log.recording_url}" target="_blank">🎧 Recording</a>` : ''}
        </td>
      </tr>`).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#ef4444;">Error loading logs. Check Supabase credentials.</td></tr>';
  }
}

// ── Calendar ────────────────────────────────────────────────────────────────
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let allBookings = [];

async function loadCalendar() {
  try { allBookings = await fetch('/api/bookings').then(r => r.json()); } catch(e) { allBookings = []; }
  renderCalendar();
}

function changeMonth(dir) { calMonth += dir; if (calMonth > 11) { calMonth = 0; calYear++; } else if (calMonth < 0) { calMonth = 11; calYear--; } renderCalendar(); }

function renderCalendar() {
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('cal-month-label').textContent = `${months[calMonth]} ${calYear}`;
  const grid = document.getElementById('cal-grid');
  const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const today = new Date();

  // Build booking map by date string YYYY-MM-DD
  const bookMap = {};
  allBookings.forEach(b => {
    const d = b.created_at ? b.created_at.slice(0,10) : null;
    if (d) { bookMap[d] = bookMap[d] || []; bookMap[d].push(b); }
  });

  let html = days.map(d => `<div class="cal-day-name">${d}</div>`).join('');

  const first = new Date(calYear, calMonth, 1);
  const last = new Date(calYear, calMonth + 1, 0);
  const startPad = first.getDay();

  // Prev month padding
  for (let i = 0; i < startPad; i++) {
    const d = new Date(calYear, calMonth, -startPad + i + 1);
    html += `<div class="cal-cell other-month"><div class="cal-num">${d.getDate()}</div></div>`;
  }

  for (let day = 1; day <= last.getDate(); day++) {
    const dateStr = `${calYear}-${String(calMonth+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
    const bks = bookMap[dateStr] || [];
    const isToday = today.getFullYear()===calYear && today.getMonth()===calMonth && today.getDate()===day;
    html += `<div class="cal-cell${isToday?' today':''}" onclick="showDay('${dateStr}', ${JSON.stringify(bks).replace(/'/g,"&apos;")})">
      <div class="cal-num">${day}</div>
      ${bks.length ? `<div class="cal-dot"></div><div class="cal-booking-count">${bks.length} booking${bks.length>1?'s':''}</div>` : ''}
    </div>`;
  }

  // Next month padding
  const endPad = 6 - last.getDay();
  for (let i = 1; i <= endPad; i++) {
    html += `<div class="cal-cell other-month"><div class="cal-num">${i}</div></div>`;
  }

  grid.innerHTML = html;
  document.getElementById('day-panel').classList.remove('show');
}

function showDay(dateStr, bookings) {
  // Update old inline panel too
  const panel = document.getElementById('day-panel');
  if (panel) {
    panel.classList.add('show');
    document.getElementById('day-panel-title').textContent = `Bookings on ${dateStr}`;
  }
  // Open modal overlay
  openDayModal(dateStr, bookings);
}

function openDayModal(dateStr, bookings) {
  const modal = document.getElementById('day-modal');
  const dateObj = new Date(dateStr + 'T00:00:00');
  const formatted = dateObj.toLocaleDateString('en-IN', {weekday:'long', year:'numeric', month:'long', day:'numeric'});
  document.getElementById('modal-date-title').textContent = formatted;
  document.getElementById('modal-date-sub').textContent =
    bookings.length ? `${bookings.length} booking${bookings.length>1?'s':''} on this day` : 'No bookings on this day';

  if (!bookings || bookings.length === 0) {
    document.getElementById('modal-bookings-body').innerHTML =
      '<div style="text-align:center;padding:32px;color:var(--muted);font-size:14px;">📅 No bookings on this day.</div>';
  } else {
    document.getElementById('modal-bookings-body').innerHTML = bookings.map(b => `
      <div class="booking-item">
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <div style="font-weight:700;font-size:14px;">📞 ${b.phone_number || 'Unknown'}</div>
          <span class="badge badge-green">✅ Booked</span>
        </div>
        <div style="font-size:12px;color:var(--muted);margin-top:6px;">🕐 ${new Date(b.created_at).toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit'})}</div>
        ${b.summary ? `<div style="font-size:12px;color:var(--text);margin-top:6px;padding:8px;background:rgba(255,255,255,0.04);border-radius:6px;">💬 ${b.summary}</div>` : ''}
      </div>`).join('');
  }
  modal.classList.add('open');
}

function closeDayModal() {
  document.getElementById('day-modal').classList.remove('open');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDayModal(); });

// ── CRM ─────────────────────────────────────────────────────────────────────
async function loadCRM() {
  const tbody = document.getElementById('crm-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading contacts...</td></tr>';
  try {
    const contacts = await fetch('/api/contacts').then(r => r.json());
    if (!contacts.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:40px;color:var(--muted);">No contacts yet. They will appear here automatically after calls.</td></tr>';
      return;
    }
    tbody.innerHTML = contacts.map(c => `
      <tr style="border-bottom:1px solid var(--border);transition:background 0.12s;" onmouseover="this.style.background='rgba(255,255,255,0.025)'" onmouseout="this.style.background=''">
        <td style="padding:14px 16px;font-weight:600;">${c.caller_name || '<span style="color:var(--muted);font-weight:400;">Unknown</span>'}</td>
        <td style="padding:14px 16px;font-family:monospace;font-size:13px;">${c.phone_number || '—'}</td>
        <td style="padding:14px 16px;text-align:center;"><span style="background:rgba(108,99,255,0.12);color:var(--accent);padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">${c.total_calls}</span></td>
        <td style="padding:14px 16px;color:var(--muted);font-size:12px;">${c.last_seen ? new Date(c.last_seen).toLocaleString('en-IN') : '—'}</td>
        <td style="padding:14px 16px;">${c.is_booked
          ? '<span class="badge badge-green">✅ Booked</span>'
          : '<span class="badge badge-gray">📵 No booking</span>'}</td>
      </tr>`).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#ef4444;">Error loading contacts. Check Supabase credentials.</td></tr>';
  }
}

// ── Save Config ─────────────────────────────────────────────────────────────
async function saveConfig(section) {
  const get = id => { const el = document.getElementById(id); return el ? el.value : null; };

  const payload = {};

  if (section === 'agent') {
    Object.assign(payload, {
      first_line: get('first_line'),
      agent_instructions: get('agent_instructions'),
      stt_min_endpointing_delay: parseFloat(get('stt_min_endpointing_delay')),
    });
  } else if (section === 'models') {
    Object.assign(payload, {
      llm_model: get('llm_model'),
      tts_voice: get('tts_voice'),
      tts_language: get('tts_language'),
    });
  } else if (section === 'credentials') {
    Object.assign(payload, {
      livekit_url: get('livekit_url'), sip_trunk_id: get('sip_trunk_id'),
      livekit_api_key: get('livekit_api_key'), livekit_api_secret: get('livekit_api_secret'),
      openai_api_key: get('openai_api_key'), sarvam_api_key: get('sarvam_api_key'),
      cal_api_key: get('cal_api_key'), cal_event_type_id: get('cal_event_type_id'),
      telegram_bot_token: get('telegram_bot_token'), telegram_chat_id: get('telegram_chat_id'),
      supabase_url: get('supabase_url'), supabase_key: get('supabase_key'),
    });
  }

  const res = await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });

  const statusEl = document.getElementById('save-status-' + section);
  if (res.ok) {
    statusEl.style.opacity = '1';
    setTimeout(() => { statusEl.style.opacity = '0'; }, 2500);
  } else {
    alert('Failed to save. Check server logs.');
  }
}


// ── Language Presets ─────────────────────────────────────────────────────────
const LANG_PRESETS = {
  hinglish:    { flag:'🇮🇳', label:'Hinglish',                sub:'Hindi + English mix',        color:'#6c63ff' },
  hindi:       { flag:'🇮🇳', label:'Hindi',                   sub:'Pure Hindi',                  color:'#a855f7' },
  english:     { flag:'🇬🇧', label:'English (India)',          sub:'Indian English',              color:'#3b82f6' },
  tamil:       { flag:'🇮🇳', label:'Tamil',                   sub:'தமிழ்',                       color:'#f59e0b' },
  telugu:      { flag:'🇮🇳', label:'Telugu',                  sub:'తెలుగు',                      color:'#10b981' },
  gujarati:    { flag:'🇮🇳', label:'Gujarati',                sub:'ગુજરાતી',                     color:'#ef4444' },
  bengali:     { flag:'🇮🇳', label:'Bengali',                 sub:'বাংলা',                       color:'#f97316' },
  marathi:     { flag:'🇮🇳', label:'Marathi',                 sub:'मराठी',                       color:'#14b8a6' },
  kannada:     { flag:'🇮🇳', label:'Kannada',                 sub:'ಕನ್ನಡ',                       color:'#8b5cf6' },
  malayalam:   { flag:'🇮🇳', label:'Malayalam',               sub:'മലയാളം',                      color:'#ec4899' },
  multilingual:{ flag:'🌍', label:'Multilingual (Auto)',       sub:"Detects caller's language",   color:'#22c55e' },
};

let currentLangPreset = 'hinglish';

async function initLanguagePage() {
  try {
    const cfg = await fetch('/api/config').then(r=>r.json());
    currentLangPreset = cfg.lang_preset || 'hinglish';
  } catch(e) {}
  renderLangGrid();
}

function renderLangGrid() {
  const grid = document.getElementById('lang-grid');
  if (!grid) return;
  grid.innerHTML = Object.entries(LANG_PRESETS).map(([id, p]) => `
    <div onclick="selectLangPreset('${id}')" style="
      background:${id===currentLangPreset ? 'rgba(108,99,255,0.15)' : 'var(--bg)'};
      border:2px solid ${id===currentLangPreset ? p.color : 'var(--border)'};
      border-radius:12px;padding:18px;cursor:pointer;transition:all 0.15s;
      ${id===currentLangPreset ? 'box-shadow:0 0 16px rgba(108,99,255,0.2)' : ''}
    " onmouseover="this.style.borderColor='${p.color}'" onmouseout="this.style.borderColor='${id===currentLangPreset?p.color:'var(--border)'}'">
      <div style="font-size:28px;margin-bottom:8px;">${p.flag}</div>
      <div style="font-weight:700;font-size:14px;color:${id===currentLangPreset?p.color:'var(--text)'}">${p.label}</div>
      <div style="font-size:11px;color:var(--muted);margin-top:3px;">${p.sub}</div>
      ${id===currentLangPreset ? '<div style="font-size:10px;color:#22c55e;margin-top:6px;font-weight:600;">✓ ACTIVE</div>' : ''}
    </div>`).join('');
}

async function selectLangPreset(id) {
  const p = LANG_PRESETS[id];
  if (!p) return;
  currentLangPreset = id;
  renderLangGrid();
  // Save lang_preset, tts_language, tts_voice to config
  try {
    const cfg = await fetch('/api/config').then(r=>r.json());
    const voices = { hinglish:'kavya', hindi:'ritu', english:'dev', tamil:'priya', telugu:'kavya', gujarati:'rohan', bengali:'neha', marathi:'shubh', kannada:'rahul', malayalam:'ritu', multilingual:'kavya' };
    const langs  = { hinglish:'hi-IN', hindi:'hi-IN', english:'en-IN', tamil:'ta-IN', telugu:'te-IN', gujarati:'gu-IN', bengali:'bn-IN', marathi:'mr-IN', kannada:'kn-IN', malayalam:'ml-IN', multilingual:'hi-IN' };
    await fetch('/api/config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ lang_preset: id, tts_language: langs[id], tts_voice: voices[id] })
    });
    const toast = document.createElement('div');
    toast.style.cssText='position:fixed;bottom:24px;right:24px;background:#22c55e;color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;z-index:9999;animation:slideUp 0.3s ease';
    toast.textContent = `✅ ${p.label} preset activated!`;
    document.body.appendChild(toast);
    setTimeout(()=>toast.remove(), 2500);
  } catch(e) { alert('Failed to save: ' + e); }
}

// ── Outbound Calls ─────────────────────────────────────────────────────────── 
async function makeSingleCall() {
  const phone = document.getElementById('call-single-num').value.trim();
  if (!phone) return;
  const el = document.getElementById('single-call-status');
  el.textContent = '⏳ Dispatching...';
  el.style.color = 'var(--muted)';
  try {
    const res = await fetch('/api/call/single', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({phone})
    }).then(r=>r.json());
    if (res.status === 'ok') {
      el.innerHTML = `✅ Call dispatched! Dispatch ID: <code>${res.dispatch_id}</code>`;
      el.style.color = 'var(--green)';
    } else {
      el.textContent = '❌ ' + res.message;
      el.style.color = 'var(--red)';
    }
  } catch(e) {
    el.textContent = '❌ Error: ' + e;
    el.style.color = 'var(--red)';
  }
}

async function makeBulkCall() {
  const nums = document.getElementById('call-bulk-nums').value.trim();
  if (!nums) return;
  const el = document.getElementById('bulk-call-status');
  el.textContent = '⏳ Dispatching all numbers...';
  try {
    const res = await fetch('/api/call/bulk', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({numbers: nums})
    }).then(r=>r.json());
    const results = res.results || [];
    document.getElementById('call-results-card').style.display = 'block';
    document.getElementById('call-results-body').innerHTML = results.map(r => `
      <div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);">
        <span style="font-family:monospace;">${r.phone}</span>
        <span class="badge ${r.status==='ok'?'badge-green':'badge-gray'}">${r.status==='ok'?'✅ Sent':'❌ '+r.message}</span>
      </div>`).join('');
    el.textContent = `✅ ${results.filter(r=>r.status==='ok').length}/${results.length} calls dispatched`;
    el.style.color = 'var(--green)';
  } catch(e) {
    el.textContent = '❌ Error: ' + e;
    el.style.color = 'var(--red)';
  }
}

// ── Demo Link ─────────────────────────────────────────────────────────────────
let demoUrl = '';
function initDemo() {
  // no-op until user clicks generate
}
async function generateDemo() {
  const statusEl = document.getElementById('demo-status');
  statusEl.textContent = '⏳ Generating session...';
  try {
    const origin = window.location.origin;
    demoUrl = origin + '/demo';
    document.getElementById('demo-link-box').textContent = demoUrl;
    document.getElementById('demo-link-box').style.display = 'block';
    document.getElementById('copy-demo-btn').style.display = 'inline-flex';
    document.getElementById('open-demo-btn').style.display = 'inline-flex';
    document.getElementById('open-demo-btn').href = demoUrl;
    document.getElementById('demo-iframe').src = demoUrl;
    document.getElementById('demo-iframe').style.display = 'block';
    statusEl.textContent = 'Session ready — share the link or use the preview below';
  } catch(e) {
    statusEl.textContent = '❌ ' + e;
  }
}
function copyDemoLink() {
  navigator.clipboard.writeText(demoUrl);
  document.getElementById('copy-demo-btn').textContent = '✅ Copied!';
  setTimeout(()=>document.getElementById('copy-demo-btn').textContent='📋 Copy Link', 2000);
}

// ── Boot ────────────────────────────────────────────────────────────────────
loadDashboard();
