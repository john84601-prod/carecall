'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let allClients = [];
let clientViewMode = 'rows';  // 'tiles' | 'rows'
let clientSortOrder = 'first'; // 'first' = first→last name, 'last' = last→first name
let currentClientId = null;   // client being edited in modal
let scheduleContacts = [];    // array of emergency_contact_id values active for the schedule being edited
let clientContactsCache = []; // client's full emergency contact list (loaded when schedule modal opens)
let allAudioFiles = [];       // full audio file list for Audio tab (client-side search)
let recordingContext = 'global'; // 'global' | 'client' — where to route the active recording

// Recording state
let mediaRecorder = null;
let audioChunks = [];
let recordedBlob = null;
let recordTimerInterval = null;
let recordStartTime = null;
let micStream = null;
let currentContactId = null;  // contact being edited

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupUploadZone();
  _initTooltips();
  _initReportDates();
  loadDashboard();
  loadSystemStatus();
  setInterval(loadSystemStatus, 60_000);
});

// ── System status bar ─────────────────────────────────────────────────────────
async function loadSystemStatus() {
  try {
    const s = await api('GET', '/status');
    _renderSystemStatus(s);
    const dot = document.getElementById('statusDot');
    if (dot) dot.className = 'status-dot ' + (s.all_ok ? 'online' : 'offline');
  } catch(e) {
    // API totally unreachable
    const dot = document.getElementById('statusDot');
    if (dot) dot.className = 'status-dot offline';
    _renderSystemStatusError();
  }
}

function _renderSystemStatus(s) {
  function _setItem(dotId, lblId, ok, label) {
    const d = document.getElementById(dotId);
    const l = document.getElementById(lblId);
    if (d) d.className = 'sys-dot ' + (ok ? 'sys-ok' : 'sys-err');
    if (l) l.textContent = label;
  }

  _setItem('sysDotScheduler', 'sysLabelScheduler',
    s.scheduler_running,
    s.scheduler_running ? 'Scheduler' : 'Scheduler ✗ Not Running');

  const urlLabel = s.public_url_ok
    ? (s.public_url || '').replace(/^https?:\/\//, '').replace(/\/.*$/, '')
    : 'No Public URL';
  _setItem('sysDotUrl', 'sysLabelUrl', s.public_url_ok, urlLabel);

  _setItem('sysDotTwilio', 'sysLabelTwilio',
    s.twilio_configured,
    s.twilio_configured ? 'Twilio' : 'Twilio ✗ Not Configured');

  const u = document.getElementById('sysUptime');
  if (u) u.textContent = 'Up ' + (s.uptime || '—');

  const t = document.getElementById('sysServerTime');
  if (t) t.textContent = s.server_time || '—';

  const bar = document.getElementById('sysStatusBar');
  if (bar) bar.classList.toggle('sys-warn', !s.all_ok);
}

function _renderSystemStatusError() {
  ['sysDotScheduler','sysDotUrl','sysDotTwilio'].forEach(id => {
    const d = document.getElementById(id);
    if (d) d.className = 'sys-dot sys-err';
  });
  const bar = document.getElementById('sysStatusBar');
  if (bar) bar.classList.add('sys-warn');
}

// ── Tooltip engine ────────────────────────────────────────────────────────────
function _initTooltips() {
  const tip = document.createElement('div');
  tip.className = 'tooltip-popup';
  document.body.appendChild(tip);

  let activeTipEl = null;

  document.addEventListener('mousemove', e => {
    const el = e.target.closest('[data-tooltip]');
    if (!el) {
      tip.style.opacity = '0';
      activeTipEl = null;
      return;
    }
    // Update text only when the target changes
    if (el !== activeTipEl) {
      tip.textContent = el.dataset.tooltip;
      activeTipEl = el;
    }
    tip.style.opacity = '1';

    // Position above the element, clamped to viewport, flip below if no room
    const r  = el.getBoundingClientRect();
    const tw = tip.offsetWidth;
    const th = tip.offsetHeight;
    let left = r.left + r.width / 2 - tw / 2;
    let top  = r.top  - th - 8;
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    if (top < 8) top = r.bottom + 8;
    tip.style.left = left + 'px';
    tip.style.top  = top  + 'px';
  });

  document.addEventListener('mouseleave', () => {
    tip.style.opacity = '0';
    activeTipEl = null;
  }, true); // capture phase so it fires even when mouse leaves the window
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
      switch (btn.dataset.tab) {
        case 'dashboard': loadDashboard(); break;
        case 'clients':   loadClients();   break;
        case 'schedules': loadSchedules(); break;
        case 'reports':   /* tiles are static; dates already initialised */ break;
        case 'audio':     loadAudio();     break;
        case 'settings':  loadSettings();  break;
      }
    });
  });
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch('/api' + path, opts);
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 3200);
}

// ── Overlays ──────────────────────────────────────────────────────────────────
function closeOverlay(id) {
  document.getElementById(id).style.display = 'none';
}
function openOverlay(id) {
  document.getElementById(id).style.display = 'flex';
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const d = await api('GET', '/dashboard');
    document.getElementById('statClients').textContent   = d.active_clients;
    document.getElementById('statSchedules').textContent = d.active_schedules;

    document.getElementById('statReminderScheduled').textContent = d.active_reminder_schedules;
    document.getElementById('statReminderCompleted').textContent = d.reminder_completed_today;
    document.getElementById('statReminderCalls').textContent     = d.reminder_calls_today;

    document.getElementById('statWellnessScheduled').textContent = d.active_wellness_schedules;
    document.getElementById('statWellnessCompleted').textContent = d.wellness_completed_today;
    document.getElementById('statWellnessCalls').textContent     = d.wellness_calls_today;

    document.getElementById('statAlertCycles').textContent = d.alert_cycles_today;

    const sc = document.getElementById('sessionCard');
    if (d.active_sessions > 0) {
      sc.style.display = '';
      document.getElementById('statSessions').textContent = d.active_sessions;
    } else {
      sc.style.display = 'none';
    }

    const rc = document.getElementById('reminderSessionCard');
    if (d.active_reminder_sessions > 0) {
      rc.style.display = '';
      document.getElementById('statReminderSessions').textContent = d.active_reminder_sessions;
    } else {
      rc.style.display = 'none';
    }

    const tbody = document.getElementById('logTable');
    if (!d.upcoming_calls || !d.upcoming_calls.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No more calls scheduled for today.</td></tr>';
    } else {
      tbody.innerHTML = d.upcoming_calls.map(l => {
        const scheduledSameAsNext = l.schedule_time === l.next_attempt_at;
        const nextCell = l.next_attempt_at
          ? `<span style="white-space:nowrap">${fmtScheduleTime(l.next_attempt_at)}</span>`
          : '—';
        const attemptCell = l.attempt_number != null ? `#${l.attempt_number}` : '—';
        return `<tr>
          <td>${esc(l.client_name)}</td>
          <td>${esc(l.schedule_name || '—')}</td>
          <td style="white-space:nowrap;font-weight:600">${l.schedule_time ? fmtScheduleTime(l.schedule_time) : '—'}</td>
          <td>${scheduledSameAsNext ? '—' : nextCell}</td>
          <td>${typeBadge(l.call_type)}</td>
          <td>${attemptCell}</td>
        </tr>`;
      }).join('');
    }

    // Wellness alert cycles panel (only escalated-to-contact sessions)
    const sessTbody = document.getElementById('wellnessSessionsTable');
    if (d.recent_sessions && d.recent_sessions.length) {
      const activeStatuses = new Set(['pending', 'calling', 'escalating']);
      sessTbody.innerHTML = d.recent_sessions.map(s => {
        const schedCell = s.schedule_time
          ? `${fmtScheduleTime(s.schedule_time)}${s.schedule_name ? `<br><small style="color:var(--muted)">${esc(s.schedule_name)}</small>` : ''}`
          : '—';
        const ackBy = s.acknowledged_by_contact_name
          ? `${esc(s.acknowledged_by_contact_name)}<br><small style="color:var(--muted)">${fmtPhone(s.acknowledged_by_contact_phone || '')}</small>`
          : '—';
        const stopBtn = activeStatuses.has(s.status)
          ? `<button class="btn-danger btn-sm" onclick="cancelWellnessSession(${s.id})">⏹ Stop</button>`
          : '';
        return `<tr>
          <td style="white-space:nowrap">${fmtTime(s.started_at)}</td>
          <td>${esc(s.client_name)}</td>
          <td style="white-space:nowrap">${schedCell}</td>
          <td>${s.current_attempt}</td>
          <td style="white-space:nowrap">${s.resolved_at ? fmtTime(s.resolved_at) : '—'}</td>
          <td>${ackBy}</td>
          <td>${stopBtn}</td>
        </tr>`;
      }).join('');
    } else {
      sessTbody.innerHTML = '<tr><td colspan="7" class="empty">No alert cycles today.</td></tr>';
    }

    // Active reminder sessions panel
    const remPanel = document.getElementById('reminderSessionsPanel');
    const remTbody = document.getElementById('reminderSessionsTable');
    if (d.active_reminder_sessions_list && d.active_reminder_sessions_list.length) {
      remPanel.style.display = '';
      remTbody.innerHTML = d.active_reminder_sessions_list.map(s => `
        <tr>
          <td>${esc(s.client_name)}</td>
          <td>${esc(s.schedule_name || '—')}</td>
          <td>${s.current_attempt}</td>
          <td style="white-space:nowrap">${fmtTime(s.started_at)}</td>
          <td><button class="btn-danger btn-sm" onclick="cancelReminderSession(${s.id})">⏹ Stop</button></td>
        </tr>`).join('');
    } else {
      if (remPanel) remPanel.style.display = 'none';
    }

    document.getElementById('statusDot').className = 'status-dot online';
  } catch (e) {
    document.getElementById('statusDot').className = 'status-dot offline';
  }
}

// ── Clients ───────────────────────────────────────────────────────────────────
async function loadClients() {
  try {
    allClients = await api('GET', '/clients');
    filterAndRenderClients();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function setClientView(mode) {
  clientViewMode = mode;
  document.getElementById('viewTilesBtn').classList.toggle('active', mode === 'tiles');
  document.getElementById('viewRowsBtn').classList.toggle('active', mode === 'rows');
  filterAndRenderClients();
}

function setClientSort(order) {
  clientSortOrder = order;
  document.getElementById('sortFirstBtn').classList.toggle('active', order === 'first');
  document.getElementById('sortLastBtn').classList.toggle('active', order === 'last');
  filterAndRenderClients();
}

function clearClientFilters() {
  const nameEl     = document.getElementById('filterName');
  const phoneEl    = document.getElementById('filterPhone');
  const reminderEl = document.getElementById('filterReminder');
  const wellnessEl = document.getElementById('filterWellness');
  const activeEl   = document.querySelector('input[name="clientStatus"][value="active"]');
  if (nameEl)     nameEl.value = '';
  if (phoneEl)    phoneEl.value = '';
  if (reminderEl) reminderEl.checked = false;
  if (wellnessEl) wellnessEl.checked = false;
  if (activeEl)   activeEl.checked = true;
  filterAndRenderClients();
}

function filterAndRenderClients() {
  const name   = (document.getElementById('filterName')?.value  || '').trim().toLowerCase();
  const phone  = (document.getElementById('filterPhone')?.value || '').trim().replace(/\D/g, '');
  const wantR  = document.getElementById('filterReminder')?.checked;
  const wantW  = document.getElementById('filterWellness')?.checked;
  const status = document.querySelector('input[name="clientStatus"]:checked')?.value || 'active';

  const filtered = allClients.filter(c => {
    // Name filter
    if (name && !c.first_name.toLowerCase().includes(name) &&
                !c.last_name.toLowerCase().includes(name) &&
                !c.full_name.toLowerCase().includes(name)) return false;

    // Phone filter
    if (phone) {
      const digits = String(c.phone || '').replace(/\D/g, '');
      if (!digits.includes(phone)) return false;
    }

    // Schedule-type filters (AND: both must match when both checked)
    const types = c.schedule_types || [];
    if (wantR && !types.includes('reminder')) return false;
    if (wantW && !types.includes('wellness')) return false;

    // Status filter
    if (status === 'active'   && !c.active) return false;
    if (status === 'inactive' &&  c.active) return false;

    return true;
  });

  // Sort
  filtered.sort((a, b) => clientSortOrder === 'first'
    ? a.first_name.localeCompare(b.first_name) || a.last_name.localeCompare(b.last_name)
    : a.last_name.localeCompare(b.last_name)   || a.first_name.localeCompare(b.first_name)
  );

  renderClients(filtered);
}

function typeChips(types) {
  return (types || []).map(t =>
    t === 'reminder'
      ? '<span class="type-chip type-chip-r" title="Reminder call">R</span>'
      : t === 'wellness'
      ? '<span class="type-chip type-chip-w" title="Wellness check">W</span>'
      : ''
  ).join('');
}

function renderClients(clients) {
  const container = document.getElementById('clientsContainer');
  if (!clients.length) {
    container.innerHTML = allClients.length
      ? '<div class="empty">No clients match the current filters.</div>'
      : '<div class="empty">No clients yet. Click "+ Add Client" to get started.</div>';
    return;
  }
  if (clientViewMode === 'rows') {
    _renderClientRows(clients, container);
  } else {
    _renderClientTiles(clients, container);
  }
}

function _renderClientTiles(clients, container) {
  container.innerHTML = '<div class="clients-grid">' +
    clients.map(c => `
      <div class="client-card">
        <div class="client-card-head">
          <div>
            <div class="client-name">
              ${esc(c.full_name)}&nbsp;${typeChips(c.schedule_types)}
              ${c.active ? '' : '<span class="badge badge-gray">inactive</span>'}
            </div>
            <div class="client-phone">${fmtPhone(c.phone)}</div>
          </div>
          <div class="action-btns">
            <button class="btn-edit" onclick="editClient(${c.id})">Edit</button>
            <button class="btn-danger" onclick="deleteClient(${c.id}, '${esc(c.full_name)}')">Delete</button>
          </div>
        </div>
        ${c.birthday ? `<div class="client-notes">🎂 ${fmtBirthday(c.birthday)}</div>` : ''}
        ${fmtAddress(c) ? `<div class="client-notes">${esc(fmtAddress(c))}</div>` : ''}
        ${c.notes ? `<div class="client-notes" style="margin-top:.25rem">${esc(c.notes)}</div>` : ''}
        ${renderEcSummary(c.emergency_contacts)}
      </div>`).join('') +
  '</div>';
}

function _renderClientRows(clients, container) {
  container.innerHTML = `
    <div class="table-wrap">
      <table class="clients-table">
        <thead><tr>
          <th>Name</th>
          <th>Phone</th>
          <th>Calls</th>
          <th>Status</th>
          <th>Emergency Contacts</th>
          <th></th>
        </tr></thead>
        <tbody>${clients.map(c => `
          <tr>
            <td>
              <strong>${esc(c.full_name)}</strong>
              ${c.birthday ? `<br><small style="color:var(--muted)">${fmtBirthday(c.birthday)}</small>` : ''}
            </td>
            <td style="white-space:nowrap">${fmtPhone(c.phone)}</td>
            <td style="white-space:nowrap">${typeChips(c.schedule_types) || '<span style="color:var(--muted)">—</span>'}</td>
            <td>${c.active
              ? '<span class="badge badge-green">Active</span>'
              : '<span class="badge badge-gray">Inactive</span>'}</td>
            <td style="font-size:.83rem;color:var(--muted)">
              ${c.emergency_contacts.length
                ? c.emergency_contacts.map(ec => esc(ec.name)).join(', ')
                : '—'}
            </td>
            <td class="action-btns">
              <button class="btn-edit btn-sm" onclick="editClient(${c.id})">Edit</button>
              <button class="btn-danger btn-sm" onclick="deleteClient(${c.id}, '${esc(c.full_name)}')">Delete</button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function renderEcSummary(contacts) {
  if (!contacts || !contacts.length) {
    return '<div class="ec-list" style="margin-top:.5rem;font-size:.8rem;color:var(--muted)">No emergency contacts</div>';
  }
  return '<div class="ec-list">' +
    contacts.map(ec => `
      <div class="ec-item">
        <span class="ec-priority">${ec.priority}</span>
        <span style="flex:1">${esc(ec.name)}${ec.relationship ? ' <span style="color:var(--muted)">(' + esc(ec.relationship) + ')</span>' : ''}</span>
        <span style="color:var(--muted)">${fmtPhone(ec.phone)}</span>
      </div>`).join('') +
  '</div>';
}

function showClientModal(client) {
  currentClientId = null;
  document.getElementById('clientModalTitle').textContent = 'Add Client';
  document.getElementById('clientId').value = '';
  document.getElementById('clientFirstName').value = '';
  document.getElementById('clientLastName').value = '';
  document.getElementById('clientPhone').value = '';
  document.getElementById('clientBirthday').value = '';
  document.getElementById('clientAddress1').value = '';
  document.getElementById('clientAddress2').value = '';
  document.getElementById('clientCity').value = '';
  document.getElementById('clientState').value = '';
  document.getElementById('clientZip').value = '';
  document.getElementById('clientNotes').value = '';
  document.getElementById('clientActive').checked = true;
  document.getElementById('clientTabsSection').style.display = 'none';
  document.getElementById('clientPrintBtn').style.display = 'none';
  cancelBlackoutEdit();
  _blackoutsCache = [];
  // Collapse address section for a clean blank form
  document.getElementById('addrNotesBody').style.display = 'none';
  document.getElementById('addrNotesToggle').querySelector('.caret-char').textContent = '▶';
  openOverlay('clientOverlay');
}

async function editClient(id) {
  currentClientId = id;
  const c = allClients.find(x => x.id === id);
  if (!c) return;
  document.getElementById('clientModalTitle').textContent = 'Edit Client';
  document.getElementById('clientId').value = c.id;
  document.getElementById('clientFirstName').value = c.first_name;
  document.getElementById('clientLastName').value = c.last_name;
  document.getElementById('clientPhone').value = fmtPhone(c.phone);
  document.getElementById('clientBirthday').value = c.birthday || '';
  document.getElementById('clientAddress1').value = c.address1 || '';
  document.getElementById('clientAddress2').value = c.address2 || '';
  document.getElementById('clientCity').value = c.city || '';
  document.getElementById('clientState').value = c.state || '';
  document.getElementById('clientZip').value = c.zip_code || '';
  document.getElementById('clientNotes').value = c.notes || '';
  document.getElementById('clientActive').checked = c.active;
  document.getElementById('clientTabsSection').style.display = '';
  document.getElementById('clientPrintBtn').style.display = '';
  switchClientTab('schedules');
  // Auto-expand Notes section if the client has notes
  document.getElementById('addrNotesBody').style.display = c.notes ? '' : 'none';
  document.getElementById('addrNotesToggle').querySelector('.caret-char').textContent = c.notes ? '▼' : '▶';
  await Promise.all([loadClientSchedules(id), loadContactsList(id), loadClientAudio(id)]);
  openOverlay('clientOverlay');
}

async function saveClient(e) {
  e.preventDefault();
  const id = document.getElementById('clientId').value;
  const payload = {
    first_name: document.getElementById('clientFirstName').value.trim(),
    last_name:  document.getElementById('clientLastName').value.trim(),
    phone:      document.getElementById('clientPhone').value.trim(),
    birthday:   document.getElementById('clientBirthday').value || null,
    address1:   document.getElementById('clientAddress1').value.trim(),
    address2:   document.getElementById('clientAddress2').value.trim(),
    city:       document.getElementById('clientCity').value.trim(),
    state:      document.getElementById('clientState').value.trim().toUpperCase(),
    zip_code:   document.getElementById('clientZip').value.trim(),
    notes:      document.getElementById('clientNotes').value.trim(),
    active:     document.getElementById('clientActive').checked,
  };
  try {
    if (id) {
      await api('PUT', `/clients/${id}`, payload);
      toast('Client updated', 'success');
    } else {
      const created = await api('POST', '/clients', payload);
      currentClientId = created.id;
      document.getElementById('clientId').value = created.id;
      document.getElementById('clientModalTitle').textContent = 'Edit Client';
      document.getElementById('clientTabsSection').style.display = '';
      document.getElementById('clientPrintBtn').style.display = '';
      switchClientTab('schedules');
      await Promise.all([loadClientSchedules(created.id), loadContactsList(created.id), loadClientAudio(created.id)]);
      toast('Client created', 'success');
    }
    await loadClients();
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function deleteClient(id, name) {
  if (!confirm(`Delete client "${name}"? This will also delete all their schedules and call history.`)) return;
  try {
    await api('DELETE', `/clients/${id}`);
    toast('Client deleted');
    await loadClients();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Client modal helpers ──────────────────────────────────────────────────────

function switchClientTab(tab) {
  ['schedules', 'exceptions', 'address', 'contacts', 'audio'].forEach(t => {
    document.getElementById(`clientTab-${t}`).classList.toggle('active', t === tab);
    document.getElementById(`clientPane-${t}`).style.display = t === tab ? '' : 'none';
  });
  if (tab === 'exceptions') loadBlackouts();
}

async function saveClientAddress() {
  if (!currentClientId) return;
  try {
    await api('PUT', `/clients/${currentClientId}`, {
      address1: document.getElementById('clientAddress1').value.trim(),
      address2: document.getElementById('clientAddress2').value.trim(),
      city:     document.getElementById('clientCity').value.trim(),
      state:    document.getElementById('clientState').value.trim().toUpperCase(),
      zip_code: document.getElementById('clientZip').value.trim(),
    });
    await loadClients();
    toast('Address saved', 'success');
  } catch(e) { toast(e.message, 'error'); }
}

// ── Wellness blackout / call exceptions ───────────────────────────────────────
let _blackoutsCache = [];

async function loadBlackouts() {
  if (!currentClientId) return;
  try {
    _blackoutsCache = await api('GET', `/clients/${currentClientId}/blackouts`);
    _renderBlackoutList();
  } catch(e) { console.error('Failed to load blackouts', e); }
}

function _renderBlackoutList() {
  const el = document.getElementById('blackoutList');
  if (!_blackoutsCache.length) {
    el.innerHTML = '<p style="color:var(--muted);font-size:.875rem;padding:.5rem 0">No exceptions added yet.</p>';
    return;
  }
  el.innerHTML = _blackoutsCache.map(b => {
    const s = _fmtDateMDY(b.start_date), e = _fmtDateMDY(b.end_date);
    const range = b.start_date === b.end_date ? s : `${s} – ${e}`;
    const note  = b.note ? `<span class="blackout-note">${esc(b.note)}</span>` : '';
    return `<div class="blackout-row">
      <div class="blackout-info"><span class="blackout-range">${range}</span>${note}</div>
      <div class="blackout-actions">
        <button class="btn-ghost btn-sm" onclick="_editBlackout(${b.id})">Edit</button>
        <button class="btn-danger btn-sm" onclick="_deleteBlackout(${b.id})">Delete</button>
      </div>
    </div>`;
  }).join('');
}

function _fmtDateMDY(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${mon[m-1]} ${d}, ${y}`;
}

async function saveBlackout() {
  const id    = document.getElementById('blackoutId').value;
  const start = document.getElementById('blackoutStart').value;
  const end   = document.getElementById('blackoutEnd').value;
  const note  = document.getElementById('blackoutNote').value.trim();
  if (!start || !end) { toast('Please select start and end dates', 'error'); return; }
  if (end < start)    { toast('End date must be on or after start date', 'error'); return; }
  try {
    if (id) {
      await api('PUT',  `/blackouts/${id}`, { start_date: start, end_date: end, note });
    } else {
      await api('POST', `/clients/${currentClientId}/blackouts`, { start_date: start, end_date: end, note });
    }
    cancelBlackoutEdit();
    await loadBlackouts();
    toast(id ? 'Exception updated' : 'Exception added', 'success');
  } catch(e) { toast(e.message, 'error'); }
}

function _editBlackout(id) {
  const b = _blackoutsCache.find(x => x.id === id);
  if (!b) return;
  document.getElementById('blackoutId').value    = b.id;
  document.getElementById('blackoutStart').value = b.start_date;
  document.getElementById('blackoutEnd').value   = b.end_date;
  document.getElementById('blackoutNote').value  = b.note || '';
  document.getElementById('blackoutSaveLabel').textContent = 'Update Exception';
  document.getElementById('blackoutCancelBtn').style.display = '';
}

function cancelBlackoutEdit() {
  ['blackoutId','blackoutStart','blackoutEnd','blackoutNote'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('blackoutSaveLabel').textContent = 'Add Exception';
  document.getElementById('blackoutCancelBtn').style.display = 'none';
}

async function _deleteBlackout(id) {
  if (!confirm('Delete this wellness call exception?')) return;
  try {
    await api('DELETE', `/blackouts/${id}`);
    await loadBlackouts();
    toast('Exception deleted', 'success');
  } catch(e) { toast(e.message, 'error'); }
}

function toggleClientSection(bodyId, btn) {
  const body = document.getElementById(bodyId);
  const caret = btn.querySelector('.caret-char');
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : '';
  if (caret) caret.textContent = isOpen ? '▶' : '▼';
}

// ── Emergency contacts ────────────────────────────────────────────────────────
async function loadContactsList(clientId) {
  try {
    const contacts = await api('GET', `/clients/${clientId}/contacts`);
    const el = document.getElementById('ecList');
    if (!contacts.length) {
      el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0">No emergency contacts yet.</div>';
      return;
    }
    el.innerHTML = contacts.map(c => `
      <div class="ec-item">
        <span class="ec-priority">${c.priority}</span>
        <span style="flex:1">
          <strong>${esc(c.name)}</strong>${c.relationship ? ' · ' + esc(c.relationship) : ''}
          <br><span style="color:var(--muted)">${fmtPhone(c.phone)}</span>
          ${c.can_text ? ' <span class="badge badge-blue" style="font-size:.7rem">Can Text</span>' : ''}
        </span>
        <div class="action-btns">
          <button class="btn-edit btn-sm" onclick="editContact(${c.id},'${esc(c.name)}','${esc(c.phone)}','${esc(c.relationship)}',${c.priority},${c.can_text})">Edit</button>
          <button class="btn-danger btn-sm" onclick="deleteContact(${c.id})">✕</button>
        </div>
      </div>`).join('');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function showContactModal() {
  currentContactId = null;
  document.getElementById('contactModalTitle').textContent = 'Add Emergency Contact';
  document.getElementById('contactId').value = '';
  document.getElementById('contactName').value = '';
  document.getElementById('contactPhone').value = '';
  document.getElementById('contactRelationship').value = '';
  document.getElementById('contactPriority').value = 1;
  document.getElementById('contactCanText').checked = false;
  document.getElementById('contactSmsResult').innerHTML = '';
  openOverlay('contactOverlay');
}

function editContact(id, name, phone, rel, priority, canText) {
  currentContactId = id;
  document.getElementById('contactModalTitle').textContent = 'Edit Emergency Contact';
  document.getElementById('contactId').value = id;
  document.getElementById('contactName').value = name;
  document.getElementById('contactPhone').value = fmtPhone(phone);
  document.getElementById('contactRelationship').value = rel;
  document.getElementById('contactPriority').value = priority;
  document.getElementById('contactCanText').checked = !!canText;
  openOverlay('contactOverlay');
}

async function saveContact(e) {
  e.preventDefault();
  const id = document.getElementById('contactId').value;
  const payload = {
    name:         document.getElementById('contactName').value.trim(),
    phone:        document.getElementById('contactPhone').value.trim(),
    relationship: document.getElementById('contactRelationship').value.trim(),
    priority:     parseInt(document.getElementById('contactPriority').value),
    can_text:     document.getElementById('contactCanText').checked,
  };
  try {
    if (id) {
      await api('PUT', `/contacts/${id}`, payload);
      toast('Contact updated', 'success');
    } else {
      await api('POST', `/clients/${currentClientId}/contacts`, payload);
      toast('Contact added', 'success');
    }
    closeOverlay('contactOverlay');
    await loadContactsList(currentClientId);
    await loadClients();
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function deleteContact(id) {
  if (!confirm('Remove this emergency contact?')) return;
  try {
    await api('DELETE', `/contacts/${id}`);
    toast('Contact removed');
    await loadContactsList(currentClientId);
    await loadClients();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Client audio files ────────────────────────────────────────────────────────

async function loadClientAudio(clientId) {
  try {
    const files = await api('GET', `/uploads?client_id=${clientId}`);
    renderClientAudio(files);
  } catch (e) {
    toast(e.message, 'error');
  }
}

function renderClientAudio(files) {
  const el = document.getElementById('clientAudioList');
  if (!el) return;
  if (!files.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.4rem 0">No audio files yet. Record or upload one above.</div>';
    return;
  }
  el.innerHTML = files.map(f => `
    <div class="audio-item">
      <svg class="audio-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zM21 16a3 3 0 11-6 0 3 3 0 016 0z"/>
      </svg>
      <span class="audio-name">${esc(f.display_name || f.filename)}</span>
      <div class="audio-actions">
        <a href="/uploads/${encodeURIComponent(f.filename)}" class="btn-edit btn-sm" target="_blank">Play</a>
        <button class="btn-danger btn-sm" onclick="deleteClientAudio('${esc(f.filename)}')">Delete</button>
      </div>
    </div>`).join('');
}

async function uploadClientAudio(fileList) {
  for (const file of fileList) {
    if (!file.name.toLowerCase().endsWith('.mp3')) {
      toast(`${file.name} is not an .mp3 file`, 'error');
      continue;
    }
    const fd = new FormData();
    fd.append('file', file);
    if (currentClientId) fd.append('client_id', currentClientId);
    try {
      const res = await fetch('/api/uploads', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      toast(`Uploaded ${data.display_name || data.filename}`, 'success');
    } catch (e) {
      toast(e.message, 'error');
    }
  }
  // Reset file input
  const input = document.getElementById('clientAudioUpload');
  if (input) input.value = '';
  if (currentClientId) await loadClientAudio(currentClientId);
}

async function deleteClientAudio(filename) {
  if (!confirm(`Delete "${filename}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/uploads/${encodeURIComponent(filename)}`);
    toast('Audio file deleted');
    if (currentClientId) await loadClientAudio(currentClientId);
  } catch (e) {
    toast(e.message, 'error');
  }
}

// Client-context recording — shares mediaRecorder state, routes to client UI
function startClientRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    toast('A recording is already in progress', 'error');
    return;
  }
  recordingContext = 'client';
  _doStartRecording();
}

function stopClientRecording() {
  _doStopRecording();
}

async function saveClientRecording() {
  if (!recordedBlob) { toast('Nothing recorded yet', 'error'); return; }
  let name = document.getElementById('clientRecordName').value.trim();
  if (!name) { toast('Please enter a filename', 'error'); return; }
  name = name.replace(/\.[^.]+$/, '');

  const fd = new FormData();
  fd.append('audio', recordedBlob, 'recording.webm');
  fd.append('name', name);
  fd.append('display_name', name.replace(/_/g, ' ').replace(/-/g, ' '));
  if (currentClientId) fd.append('client_id', currentClientId);

  const btn = document.querySelector('#clientRecordPreview .btn-primary');
  const orig = btn.textContent;
  btn.textContent = 'Converting…';
  btn.disabled = true;

  try {
    const res = await fetch('/api/record', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    toast(`Saved "${data.display_name || data.filename}"`, 'success');
    discardClientRecording();
    if (currentClientId) await loadClientAudio(currentClientId);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

function discardClientRecording() {
  recordedBlob = null;
  const preview = document.getElementById('clientAudioPreview');
  if (preview) { URL.revokeObjectURL(preview.src); preview.src = ''; }
  document.getElementById('clientRecordPreview').style.display = 'none';
  document.getElementById('clientRecorderPanel').style.display = 'none';
  document.getElementById('clientRecordBtn').style.display = '';
  document.getElementById('clientRecordName').value = '';
}

// ── Schedules tab (read-only overview) ────────────────────────────────────────
let _schedAllSchedules = [];

async function loadSchedules() {
  try {
    _schedAllSchedules = await api('GET', '/schedules');
    // Initialise date picker to today if not already set
    const picker = document.getElementById('schedDate');
    if (picker && !picker.value) picker.value = _todayStr();
    _renderSchedules();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function _renderSchedules() {
  const tbody   = document.getElementById('schedulesTable');
  const picker  = document.getElementById('schedDate');
  const dateStr = picker ? picker.value : _todayStr();

  if (!_schedAllSchedules.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No schedules yet. Open a client profile to add one.</td></tr>';
    return;
  }

  // Convert selected date → APScheduler day-of-week (0=Mon … 6=Sun)
  const [y, m, d] = dateStr.split('-').map(Number);
  const jsDay = new Date(y, m - 1, d).getDay();
  const apDay = String((jsDay + 6) % 7);

  const filtered = _schedAllSchedules.filter(s => {
    const dow = (s.days_of_week || '').split(',').map(x => x.trim());
    return dow.includes(apDay);
  });

  if (!filtered.length) {
    const label = dateStr === _todayStr() ? 'today' : 'the selected date';
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No calls scheduled for ${label}.</td></tr>`;
    return;
  }

  // Fetch session statuses for today/past dates
  let schedStatuses = {};
  const isPastOrToday = dateStr <= _todayStr();
  if (isPastOrToday) {
    try {
      const res = await api('GET', `/schedules/completions?date=${dateStr}`);
      schedStatuses = res.statuses || {};
    } catch (_) { /* non-fatal */ }
  }

  filtered.sort((a, b) => (a.time_of_day || '').localeCompare(b.time_of_day || ''));

  tbody.innerHTML = filtered.map(s => {
    let statusCell = '';
    const status = schedStatuses[s.id];
    if (status) {
      statusCell = schedStatusBadge(status);
    } else if (isPastOrToday) {
      statusCell = `<button class="btn-ok btn-sm"
        onclick="schedAdminOk(${s.id}, '${esc(s.client_name)}', '${esc(s.name || s.call_type)}', '${dateStr}')">
        ✓ OK</button>`;
    }
    return `<tr>
      <td>${esc(s.client_name)}</td>
      <td style="white-space:nowrap;font-weight:600">${fmt12h(s.time_of_day)}</td>
      <td>${esc(s.name || '—')}</td>
      <td>${typeBadge(s.call_type)}</td>
      <td style="white-space:nowrap">${fmtDays(s.days_of_week)}</td>
      <td style="text-align:right">${statusCell}</td>
    </tr>`;
  }).join('');
}

async function schedAdminOk(scheduleId, clientName, callName, dateStr) {
  const label = dateStr === _todayStr() ? 'today' : dateStr;
  const msg   = `Mark "${callName}" for ${clientName} as Admin OK for ${label}?\n\nThis will record the call as administratively completed.`;
  if (!confirm(msg)) return;

  try {
    await api('POST', `/schedules/${scheduleId}/admin-ok`, { date: dateStr });
    toast(`Admin OK recorded for ${clientName}`, 'success');
    _renderSchedules();
    if (currentClientId) loadClientSchedules(currentClientId);
  } catch (e) {
    toast(e.message || 'Failed to record Admin OK', 'error');
  }
}

function schedShiftDay(delta) {
  const picker = document.getElementById('schedDate');
  if (!picker || !picker.value) return;
  picker.value = _shiftDateStr(picker.value, delta);
  _renderSchedules();
}

// ── Per-client schedule list (inside client modal) ─────────────────────────────
async function loadClientSchedules(clientId) {
  try {
    const schedules = await api('GET', `/clients/${clientId}/schedules`);
    renderClientSchedules(schedules);
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function renderClientSchedules(schedules) {
  const el = document.getElementById('clientSchedulesList');
  if (!schedules.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.4rem 0">No call schedules yet.</div>';
    return;
  }

  // Fetch today's statuses for these schedules
  let schedStatuses = {};
  try {
    const res = await api('GET', `/schedules/completions?date=${_todayStr()}`);
    schedStatuses = res.statuses || {};
  } catch (_) { /* non-fatal */ }

  el.innerHTML = schedules.map(s => {
    const title  = s.name || (s.call_type === 'reminder' ? 'Reminder' : 'Wellness Check');
    const meta   = [fmt12h(s.time_of_day), fmtDays(s.days_of_week)].join(' · ');
    const status = schedStatuses[s.id];
    const statusHtml = status
      ? `<div style="display:flex;align-items:center">${schedStatusBadge(status)}</div>`
      : `<div style="display:flex;align-items:center"><button class="btn-ok btn-sm"
          onclick="schedAdminOk(${s.id}, '${esc(s.client_name)}', '${esc(s.name || s.call_type)}', '${_todayStr()}')">
          ✓ OK</button></div>`;
    return `
      <div class="schedule-item">
        <div class="schedule-item-info">
          <div class="schedule-item-title">${esc(title)} ${typeBadge(s.call_type)}</div>
          <div class="schedule-item-meta">${meta}</div>
        </div>
        ${statusHtml}
        <label style="display:flex;align-items:center;gap:.35rem;cursor:pointer;font-size:.8rem;color:var(--muted);white-space:nowrap">
          <input type="checkbox" class="toggle" ${s.active ? 'checked' : ''}
            onchange="toggleClientSchedule(${s.id}, this.checked, this.closest('label').querySelector('.sched-active-lbl'))">
          <span class="sched-active-lbl" style="color:${s.active ? 'var(--green)' : 'var(--muted)'}">
            ${s.active ? 'Active' : 'Inactive'}
          </span>
        </label>
        <div class="action-btns">
          <button class="btn-edit btn-sm" onclick="editClientSchedule(${s.id})">Edit</button>
          <button class="btn-danger btn-sm" onclick="deleteClientSchedule(${s.id})">Delete</button>
        </div>
      </div>`;
  }).join('');
}

async function showAddScheduleModal() {
  _initTimePicker();
  await Promise.all([populateAudioSelect(), _loadClientContactsCache()]);
  document.getElementById('scheduleModalTitle').textContent = 'Add Call Schedule';
  document.getElementById('scheduleId').value = '';
  document.getElementById('scheduleClientId').value = currentClientId;
  document.getElementById('scheduleName').value = '';
  document.querySelector('input[name="callType"][value="reminder"]').checked = true;
  document.getElementById('scheduleActive').checked = true;
  document.getElementById('scheduleStatusLabel').textContent = 'Active';
  document.getElementById('scheduleHour').value = '8';
  document.getElementById('scheduleMinute').value = '00';
  document.getElementById('scheduleAmPm').value = 'AM';
  document.querySelectorAll('#dayPicker input').forEach(cb => cb.checked = true);
  document.getElementById('scheduleMp3').value = '';
  document.getElementById('scheduleMaxAttempts').value = 3;
  document.getElementById('scheduleInterval').value = 10;
  scheduleContacts = [];
  // Collapse EC section for a fresh form
  const schedEcBody = document.getElementById('schedEcBody');
  const schedEcTog  = document.getElementById('schedEcToggle');
  if (schedEcBody) schedEcBody.style.display = 'none';
  if (schedEcTog)  schedEcTog.querySelector('.caret-char').textContent = '▶';
  resetAudioPreview();
  toggleScheduleFields();
  openOverlay('scheduleOverlay');
}

async function editClientSchedule(id) {
  _initTimePicker();
  await Promise.all([populateAudioSelect(), _loadClientContactsCache()]);
  try {
    const schedules = await api('GET', `/clients/${currentClientId}/schedules`);
    const s = schedules.find(x => x.id === id);
    if (!s) return;

    document.getElementById('scheduleModalTitle').textContent = 'Edit Call Schedule';
    document.getElementById('scheduleId').value = s.id;
    document.getElementById('scheduleClientId').value = s.client_id;
    document.getElementById('scheduleName').value = s.name || '';

    document.querySelector(`input[name="callType"][value="${s.call_type}"]`).checked = true;

    const active = s.active;
    document.getElementById('scheduleActive').checked = active;
    document.getElementById('scheduleStatusLabel').textContent = active ? 'Active' : 'Inactive';

    const t = _to12h(s.time_of_day);
    document.getElementById('scheduleHour').value   = t.hour;
    document.getElementById('scheduleMinute').value = t.minute;
    document.getElementById('scheduleAmPm').value   = t.ampm;

    const days = (s.days_of_week || '').split(',');
    document.querySelectorAll('#dayPicker input').forEach(cb => {
      cb.checked = days.includes(cb.value);
    });

    document.getElementById('scheduleMp3').value         = s.mp3_filename || '';
    document.getElementById('scheduleMaxAttempts').value  = s.max_attempts || 3;
    document.getElementById('scheduleInterval').value     = s.attempt_interval_minutes || 10;

    // Load per-schedule emergency contacts — track which contact IDs are active
    scheduleContacts = (s.schedule_contacts || []).map(sc => sc.emergency_contact_id);

    resetAudioPreview();
    toggleScheduleFields();
    openOverlay('scheduleOverlay');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function saveSchedule(e) {
  e.preventDefault();
  const id       = document.getElementById('scheduleId').value;
  const clientId = document.getElementById('scheduleClientId').value;

  const days = [...document.querySelectorAll('#dayPicker input:checked')]
    .map(cb => cb.value).join(',');
  if (!days) { toast('Select at least one Call Day', 'error'); return; }

  const callType = document.querySelector('input[name="callType"]:checked').value;
  const isActive = document.getElementById('scheduleActive').checked;

  // Validate EC requirement before touching the API
  if (callType === 'wellness' && isActive && scheduleContacts.length === 0) {
    toast('At least one emergency contact must be active for an active wellness schedule', 'error');
    return;
  }

  const time24 = _to24h(
    document.getElementById('scheduleHour').value,
    document.getElementById('scheduleMinute').value,
    document.getElementById('scheduleAmPm').value,
  );

  const payload = {
    client_id:                parseInt(clientId),
    name:                     document.getElementById('scheduleName').value.trim(),
    call_type:                callType,
    time_of_day:              time24,
    days_of_week:             days,
    mp3_filename:             document.getElementById('scheduleMp3').value || null,
    max_attempts:             parseInt(document.getElementById('scheduleMaxAttempts').value) || 3,
    attempt_interval_minutes: parseInt(document.getElementById('scheduleInterval').value) || 10,
    active:                   document.getElementById('scheduleActive').checked,
  };

  try {
    let scheduleId = id;
    if (id) {
      await api('PUT', `/schedules/${id}`, payload);
      toast('Schedule updated', 'success');
    } else {
      const created = await api('POST', '/schedules', payload);
      scheduleId = created.id;
      toast('Schedule created', 'success');
    }
    // Save per-schedule emergency contacts (wellness only)
    if (callType === 'wellness') {
      await api('PUT', `/schedules/${scheduleId}/contacts`,
        scheduleContacts.map(id => ({ emergency_contact_id: id }))
      );
    }
    closeOverlay('scheduleOverlay');
    await loadClientSchedules(currentClientId);
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function toggleClientSchedule(id, active, labelEl) {
  // Optimistic UI update
  if (labelEl) {
    labelEl.textContent = active ? 'Active' : 'Inactive';
    labelEl.style.color = active ? 'var(--green)' : 'var(--muted)';
  }
  try {
    await api('PUT', `/schedules/${id}`, { active });
    toast(active ? 'Schedule activated' : 'Schedule paused');
    await loadClients(); // refresh type chips on client cards
  } catch (e) {
    toast(e.message, 'error');
    await loadClientSchedules(currentClientId);
  }
}

async function deleteClientSchedule(id) {
  if (!confirm('Delete this call schedule? Future calls will stop immediately.')) return;
  try {
    await api('DELETE', `/schedules/${id}`);
    toast('Schedule deleted');
    await loadClientSchedules(currentClientId);
  } catch (e) {
    toast(e.message, 'error');
  }
}

function toggleScheduleFields() {
  const type = document.querySelector('input[name="callType"]:checked')?.value;
  document.getElementById('wellnessFields').style.display = type === 'wellness' ? '' : 'none';
  const hint = document.getElementById('audioHint');
  if (hint) hint.textContent = type === 'wellness'
    ? '(optional — falls back to text-to-speech if not set)'
    : '(required — select a recorded file)';
  if (type === 'wellness') {
    renderScheduleEcList();
    // Always expand EC collapsible for wellness schedules
    const body = document.getElementById('schedEcBody');
    const tog  = document.getElementById('schedEcToggle');
    if (body) body.style.display = '';
    if (tog)  tog.querySelector('.caret-char').textContent = '▼';
  }
}

// ── Schedule emergency contacts ───────────────────────────────────────────────

async function _loadClientContactsCache() {
  if (!currentClientId) return;
  try {
    clientContactsCache = await api('GET', `/clients/${currentClientId}/contacts`);
  } catch (_) {
    clientContactsCache = [];
  }
}

function renderScheduleEcList() {
  const el = document.getElementById('scheduleEcList');
  if (!el) return;

  // Update count badge in collapsible header
  const countEl = document.getElementById('schedEcCount');
  if (countEl) countEl.textContent = scheduleContacts.length
    ? `(${scheduleContacts.length} active)`
    : '(none)';

  if (!clientContactsCache.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.25rem 0">No emergency contacts on file. Add them in the Contacts tab first.</div>';
    return;
  }

  el.innerHTML = clientContactsCache.map(c => {
    const isActive = scheduleContacts.includes(c.id);
    return `
      <div class="ec-item" style="margin-bottom:.4rem">
        <span style="flex:1">
          <strong>${esc(c.name)}</strong>${c.relationship ? ' · ' + esc(c.relationship) : ''}
          <br><span style="color:var(--muted);font-size:.82rem">${fmtPhone(c.phone)}</span>
        </span>
        <label style="display:flex;align-items:center;gap:.35rem;cursor:pointer;font-size:.8rem;white-space:nowrap">
          <input type="checkbox" class="toggle" ${isActive ? 'checked' : ''}
            onchange="toggleScheduleContact(${c.id}, this.checked, this.closest('label').querySelector('.ec-active-lbl'))">
          <span class="ec-active-lbl" style="color:${isActive ? 'var(--green)' : 'var(--muted)'}">
            ${isActive ? 'Active' : 'Inactive'}
          </span>
        </label>
      </div>`;
  }).join('');
}

function toggleScheduleContact(contactId, active, labelEl) {
  if (active) {
    if (!scheduleContacts.includes(contactId)) scheduleContacts.push(contactId);
  } else {
    scheduleContacts = scheduleContacts.filter(id => id !== contactId);
  }
  if (labelEl) {
    labelEl.textContent = active ? 'Active' : 'Inactive';
    labelEl.style.color = active ? 'var(--green)' : 'var(--muted)';
  }
  const countEl = document.getElementById('schedEcCount');
  if (countEl) countEl.textContent = scheduleContacts.length
    ? `(${scheduleContacts.length} active)`
    : '(none)';
}

// ── Time picker helpers ────────────────────────────────────────────────────────
function _initTimePicker() {
  const hourSel = document.getElementById('scheduleHour');
  if (hourSel.options.length) return;
  for (let h = 1; h <= 12; h++) hourSel.add(new Option(String(h), String(h)));
  const minSel = document.getElementById('scheduleMinute');
  for (let m = 0; m < 60; m += 5) {
    const v = String(m).padStart(2, '0');
    minSel.add(new Option(v, v));
  }
}

function _to24h(hour12, minute, ampm) {
  let h = parseInt(hour12, 10);
  if (ampm === 'PM' && h !== 12) h += 12;
  if (ampm === 'AM' && h === 12) h = 0;
  return `${String(h).padStart(2, '0')}:${minute}`;
}

function _to12h(time24) {
  if (!time24) return { hour: '8', minute: '00', ampm: 'AM' };
  const [h, m] = time24.split(':').map(Number);
  const ampm = h < 12 ? 'AM' : 'PM';
  const hour = h % 12 || 12;
  // Round minute down to nearest 5 for the select
  const minute = String(Math.floor(m / 5) * 5).padStart(2, '0');
  return { hour: String(hour), minute, ampm };
}

function fmt12h(time24) {
  const { hour, minute, ampm } = _to12h(time24);
  return `${hour}:${minute} ${ampm}`;
}

// ── Audio preview ──────────────────────────────────────────────────────────────
let _audioPlaying = false;

function toggleAudioPreview() {
  const filename = document.getElementById('scheduleMp3').value;
  if (!filename) { toast('Select an audio file first', 'error'); return; }

  const player = document.getElementById('scheduleAudioPlayer');
  const btn    = document.getElementById('audioPreviewBtn');

  if (_audioPlaying) {
    player.pause();
    player.currentTime = 0;
    _audioPlaying = false;
    btn.textContent = '▶ Preview';
    btn.classList.remove('playing');
  } else {
    player.src = `/uploads/${encodeURIComponent(filename)}`;
    player.play().catch(err => toast('Could not play audio: ' + err.message, 'error'));
    _audioPlaying = true;
    btn.textContent = '⏹ Stop';
    btn.classList.add('playing');
    player.onended = () => {
      _audioPlaying = false;
      btn.textContent = '▶ Preview';
      btn.classList.remove('playing');
    };
  }
}

function resetAudioPreview() {
  const player = document.getElementById('scheduleAudioPlayer');
  if (player) { player.pause(); player.currentTime = 0; }
  _audioPlaying = false;
  const btn = document.getElementById('audioPreviewBtn');
  if (btn) { btn.textContent = '▶ Preview'; btn.classList.remove('playing'); }
}

async function populateAudioSelect() {
  try {
    const files = await api('GET', '/uploads');
    const sel = document.getElementById('scheduleMp3');
    const cur = sel.value;

    // Split into this client's files vs global/shared (hide other clients' files)
    const clientFiles = files.filter(f => f.client_id === currentClientId);
    const globalFiles = files.filter(f => f.client_id === null);

    sel.innerHTML = '';
    sel.appendChild(new Option('— select audio file —', ''));

    if (clientFiles.length) {
      const grp = document.createElement('optgroup');
      grp.label = 'This Client\'s Files';
      clientFiles.forEach(f => grp.appendChild(new Option(f.display_name || f.filename, f.filename)));
      sel.appendChild(grp);
    }
    if (globalFiles.length) {
      const grp = document.createElement('optgroup');
      grp.label = clientFiles.length ? 'Global / Shared Files' : 'Audio Files';
      globalFiles.forEach(f => grp.appendChild(new Option(f.display_name || f.filename, f.filename)));
      sel.appendChild(grp);
    }
    if (!clientFiles.length && !globalFiles.length) {
      sel.appendChild(new Option('No audio files available', ''));
    }

    if (cur) sel.value = cur;
  } catch (_) {}
}

// ── Audio files tab ───────────────────────────────────────────────────────────
async function loadAudio() {
  try {
    allAudioFiles = await api('GET', '/uploads');
    filterAudio(document.getElementById('audioSearch')?.value || '');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function filterAudio(q) {
  const term = q.trim().toLowerCase();
  const filtered = term
    ? allAudioFiles.filter(f =>
        (f.display_name || '').toLowerCase().includes(term) ||
        f.filename.toLowerCase().includes(term) ||
        (f.client_name  || '').toLowerCase().includes(term) ||
        (f.client_phone || '').toLowerCase().includes(term))
    : allAudioFiles;
  renderAudio(filtered);
}

function renderAudio(files) {
  const el = document.getElementById('audioList');
  if (!files.length) {
    el.innerHTML = '<div class="empty">No audio files found.</div>';
    return;
  }
  el.innerHTML = files.map(f => {
    const clientTag = f.client_name
      ? `<span class="badge badge-blue" style="font-size:.7rem;margin-left:.4rem">${esc(f.client_name)}</span>`
      : '<span class="badge badge-gray" style="font-size:.7rem;margin-left:.4rem">Global</span>';
    return `
    <div class="audio-item">
      <svg class="audio-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zM21 16a3 3 0 11-6 0 3 3 0 016 0z"/>
      </svg>
      <span class="audio-name">${esc(f.display_name || f.filename)}${clientTag}</span>
      <div class="audio-actions">
        <a href="/uploads/${encodeURIComponent(f.filename)}" class="btn-edit btn-sm" target="_blank">Play</a>
        <button class="btn-danger btn-sm" onclick="deleteAudio('${esc(f.filename)}')">Delete</button>
      </div>
    </div>`;
  }).join('');
}

function setupUploadZone() {
  const zone = document.getElementById('uploadZone');
  const input = document.getElementById('fileInput');

  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => uploadFiles(input.files));

  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    uploadFiles(e.dataTransfer.files);
  });
}

async function uploadFiles(fileList) {
  for (const file of fileList) {
    if (!file.name.toLowerCase().endsWith('.mp3')) {
      toast(`${file.name} is not an .mp3 file`, 'error');
      continue;
    }
    const fd = new FormData();
    fd.append('file', file);
    // No client_id — global upload from Audio tab
    try {
      const res = await fetch('/api/uploads', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      toast(`Uploaded ${data.display_name || data.filename}`, 'success');
    } catch (e) {
      toast(e.message, 'error');
    }
  }
  await loadAudio();
}

async function deleteAudio(filename) {
  if (!confirm(`Delete "${filename}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/uploads/${encodeURIComponent(filename)}`);
    toast('File deleted');
    await loadAudio();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const s = await api('GET', '/settings');
    document.getElementById('settingsInfo').innerHTML = `
      <div class="settings-row">
        <div class="settings-key">Twilio Account SID</div>
        <div class="settings-val">${esc(s.twilio_account_sid)}</div>
      </div>
      <div class="settings-row">
        <div class="settings-key">Twilio From Number</div>
        <div class="settings-val">${esc(s.twilio_from_number)}</div>
      </div>
      <div class="settings-row">
        <div class="settings-key">Public Webhook URL</div>
        <div class="settings-val">${esc(s.public_url)}</div>
      </div>`;
  } catch (e) {
    toast(e.message, 'error');
  }
  // Load version info in parallel
  try {
    const v = await api('GET', '/version');
    _renderVersionInfo(v, null);
    window._versionData = v;
  } catch (e) {
    document.getElementById('versionInfo').innerHTML =
      '<span style="color:var(--muted);font-size:.85rem">Version info unavailable</span>';
  }
  // Load backup config
  await loadBackupConfig();
}

function _renderVersionInfo(v, updateStatus) {
  const ghUrl = v.github_repo ? `https://github.com/${v.github_repo}` : null;
  const repoLink = ghUrl
    ? `<a href="${ghUrl}/commits/master" target="_blank" style="color:var(--teal);font-size:.8rem">View history ↗</a>`
    : '';
  let statusHtml = '';
  if (updateStatus === 'checking') {
    statusHtml = '<span style="color:var(--muted);font-size:.83rem">Checking…</span>';
  } else if (updateStatus === 'up_to_date') {
    statusHtml = '<span style="color:var(--green);font-weight:600;font-size:.83rem">✓ Up to date</span>';
  } else if (updateStatus === 'update_available') {
    const ghUrl2 = v.github_repo ? `https://github.com/${v.github_repo}` : '#';
    statusHtml = `<span style="color:var(--orange);font-weight:600;font-size:.83rem">
      ⬆ Update available —
      <code style="font-size:.78rem">cd ~/carecall && git pull && sudo systemctl restart carecall</code>
    </span>`;
  } else if (updateStatus === 'error') {
    statusHtml = '<span style="color:var(--muted);font-size:.83rem">Could not reach GitHub</span>';
  }
  document.getElementById('versionInfo').innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.35rem;flex:1">
      <div style="display:flex;align-items:center;gap:.75rem;flex-wrap:wrap">
        <span style="font-family:monospace;font-size:.9rem;font-weight:600">${esc(v.commit)}</span>
        <span style="color:var(--muted);font-size:.83rem">${esc(v.date)}</span>
        ${repoLink}
      </div>
      ${statusHtml ? `<div>${statusHtml}</div>` : ''}
    </div>
    <button class="btn-ghost btn-sm" onclick="checkForUpdates()" id="updateCheckBtn"
      style="flex-shrink:0;white-space:nowrap">Check for Updates</button>`;
}

async function checkForUpdates() {
  const v = window._versionData;
  if (!v || !v.github_repo) {
    toast('GitHub repo info not available', 'error'); return;
  }
  document.getElementById('updateCheckBtn').disabled = true;
  _renderVersionInfo(v, 'checking');
  try {
    const res = await fetch(
      `https://api.github.com/repos/${v.github_repo}/commits/master`,
      { headers: { Accept: 'application/vnd.github.v3+json' } }
    );
    if (!res.ok) throw new Error('GitHub API error');
    const data = await res.json();
    const latestSha = data.sha;
    const status = latestSha.startsWith(v.commit_long) || v.commit_long.startsWith(latestSha.substring(0, 7))
      ? 'up_to_date' : 'update_available';
    _renderVersionInfo(v, status);
  } catch (e) {
    _renderVersionInfo(v, 'error');
  }
}

async function sendTestSms() {
  const to     = document.getElementById('contactPhone').value.trim();
  const name   = document.getElementById('contactName').value.trim() || 'this contact';
  const result = document.getElementById('contactSmsResult');
  result.innerHTML = '';
  if (!to) {
    result.innerHTML = `<div class="info-box" style="background:#fdecea;border-color:#f5c6c3;color:#7b1f1a">Enter a phone number first.</div>`;
    return;
  }
  const btn = event.currentTarget;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {
    const r = await api('POST', '/test-sms', { to, name });
    result.innerHTML = `<div class="info-box" style="background:#e8f8f0;border-color:#a8d8b9;color:#1a5e2f">
      Test text sent! SID: <code>${esc(r.message_sid)}</code></div>`;
  } catch (e) {
    result.innerHTML = `<div class="info-box" style="background:#fdecea;border-color:#f5c6c3;color:#7b1f1a">${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function sendTestCall() {
  const to = document.getElementById('testPhone').value.trim();
  const result = document.getElementById('testResult');
  result.innerHTML = '';
  try {
    const r = await api('POST', '/test-call', { to });
    result.innerHTML = `<div class="info-box" style="background:#e8f8f0;border-color:#a8d8b9;color:#1a5e2f">
      Call initiated. SID: <code>${esc(r.call_sid)}</code></div>`;
  } catch (e) {
    result.innerHTML = `<div class="info-box" style="background:#fdecea;border-color:#f5c6c3;color:#7b1f1a">
      ${esc(e.message)}</div>`;
  }
}

// ── Reports ───────────────────────────────────────────────────────────────────

let _rpt1Selected = null;  // { id, full_name, phone } — client chosen from autocomplete

// Close all report dropdowns when clicking outside them
document.addEventListener('click', e => {
  if (!e.target.closest('.rpt-ac-wrap')) {
    document.querySelectorAll('.rpt-dropdown').forEach(d => { d.innerHTML = ''; d.style.display = 'none'; });
  }
});

async function rptSearch(field, value) {
  // Any new keystroke clears the current selection
  if (_rpt1Selected) {
    _rpt1Selected = null;
    _rptUpdateSelectedUI();
  }

  const dropId = field === 'name' ? 'rpt1NameDropdown' : 'rpt1PhoneDropdown';
  const drop   = document.getElementById(dropId);
  const q      = value.trim().toLowerCase();

  if (!q) { drop.innerHTML = ''; drop.style.display = 'none'; return; }

  await loadClientsIfNeeded();

  const matches = allClients.filter(c => {
    if (field === 'name') {
      return c.full_name.toLowerCase().includes(q) ||
             c.first_name.toLowerCase().includes(q) ||
             c.last_name.toLowerCase().includes(q);
    } else {
      const digits = q.replace(/\D/g, '');
      return digits && (c.phone || '').replace(/\D/g, '').includes(digits);
    }
  }).slice(0, 12);

  if (!matches.length) {
    drop.innerHTML = '<div class="rpt-dropdown-empty">No clients found</div>';
  } else {
    drop.innerHTML = matches.map(c =>
      `<div class="rpt-dropdown-item" onmousedown="rptSelectClient(${c.id})">
         <span class="rpt-di-name">${esc(c.full_name)}</span>
         <span class="rpt-di-phone">${fmtPhone(c.phone)}</span>
       </div>`
    ).join('');
  }
  drop.style.display = 'block';
}

function rptSelectClient(id) {
  const c = allClients.find(x => x.id === id);
  if (!c) return;
  _rpt1Selected = c;
  // Populate both fields
  document.getElementById('rpt1Name').value  = c.full_name;
  document.getElementById('rpt1Phone').value = fmtPhone(c.phone);
  // Hide both dropdowns
  ['rpt1NameDropdown','rpt1PhoneDropdown'].forEach(did => {
    const d = document.getElementById(did);
    d.innerHTML = ''; d.style.display = 'none';
  });
  _rptUpdateSelectedUI();
}

function rptClearClient() {
  _rpt1Selected = null;
  document.getElementById('rpt1Name').value  = '';
  document.getElementById('rpt1Phone').value = '';
  ['rpt1NameDropdown','rpt1PhoneDropdown'].forEach(did => {
    const d = document.getElementById(did);
    d.innerHTML = ''; d.style.display = 'none';
  });
  _rptUpdateSelectedUI();
  document.getElementById('rpt1Name').focus();
}

function _rptUpdateSelectedUI() {
  const el = document.getElementById('rpt1SelectedClient');
  if (_rpt1Selected) {
    document.getElementById('rpt1SelectedName').textContent  = _rpt1Selected.full_name;
    document.getElementById('rpt1SelectedPhone').textContent = fmtPhone(_rpt1Selected.phone);
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}

function _initReportDates() {
  const today = new Date().toISOString().split('T')[0];
  ['rpt1Start','rpt1End','rpt2Start','rpt2End'].forEach(id => {
    const el = document.getElementById(id);
    if (el && !el.value) el.value = today;
  });
  _rptUpdateDateArrows();
}

function rptShiftDay(delta) {
  const startEl = document.getElementById('rpt1Start');
  const endEl   = document.getElementById('rpt1End');
  if (!startEl.value || !endEl.value) return;

  const today    = _todayStr();
  const newStart = _shiftDateStr(startEl.value, delta);
  const newEnd   = _shiftDateStr(endEl.value,   delta);

  // Block forward movement past today
  if (newEnd > today) return;
  // Block if range would invert (shouldn't happen when shifting together, but safety check)
  if (newStart > newEnd) return;

  startEl.value = newStart;
  endEl.value   = newEnd;
  _rptUpdateDateArrows();
}

function _shiftDateStr(dateStr, delta) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const date = new Date(y, m - 1, d);    // local midnight — no timezone shift
  date.setDate(date.getDate() + delta);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function _todayStr() {
  const n = new Date();
  return [
    n.getFullYear(),
    String(n.getMonth() + 1).padStart(2, '0'),
    String(n.getDate()).padStart(2, '0'),
  ].join('-');
}

function rpt2ShiftDay(delta) {
  const startEl = document.getElementById('rpt2Start');
  const endEl   = document.getElementById('rpt2End');
  if (!startEl.value || !endEl.value) return;

  const today    = _todayStr();
  const newStart = _shiftDateStr(startEl.value, delta);
  const newEnd   = _shiftDateStr(endEl.value,   delta);

  if (newEnd > today) return;
  if (newStart > newEnd) return;

  startEl.value = newStart;
  endEl.value   = newEnd;
  _rptUpdateDateArrows();
}

function _rptUpdateDateArrows() {
  const today = _todayStr();
  const end1  = document.getElementById('rpt1End');
  const btn1  = document.getElementById('rpt1NextDay');
  if (btn1) btn1.disabled = !end1?.value || end1.value >= today;
  const end2  = document.getElementById('rpt2End');
  const btn2  = document.getElementById('rpt2NextDay');
  if (btn2) btn2.disabled = !end2?.value || end2.value >= today;
}

async function runReport(type) {
  const params = new URLSearchParams();
  let title, subtitle;

  if (type === 'individual') {
    if (!_rpt1Selected) {
      toast('Please select a client from the dropdown first', 'error');
      document.getElementById('rpt1Name').focus();
      return;
    }
    const ct = document.querySelector('input[name="rpt1Type"]:checked')?.value || '';
    const s  = document.getElementById('rpt1Start').value;
    const e  = document.getElementById('rpt1End').value;
    params.set('client_id', _rpt1Selected.id);
    if (ct) params.set('call_type',  ct);
    if (s)  params.set('start_date', s);
    if (e)  params.set('end_date',   e);
    title    = 'Individual Call History';
    subtitle = _rptFilterSummary({
      clientName: _rpt1Selected.full_name,
      clientPhone: _rpt1Selected.phone,
      callType: ct, start: s, end: e,
    });
  } else {
    const ct = document.querySelector('input[name="rpt2Type"]:checked')?.value || '';
    const s  = document.getElementById('rpt2Start').value;
    const e  = document.getElementById('rpt2End').value;
    if (ct) params.set('call_type',  ct);
    if (s)  params.set('start_date', s);
    if (e)  params.set('end_date',   e);
    title    = 'System Call History';
    subtitle = _rptFilterSummary({ callType: ct, start: s, end: e });
  }

  try {
    const rows = await api('GET', `/reports/calls?${params}`);
    _printReportTitle = title;
    _printClientData  = null;
    document.getElementById('printPageContent').innerHTML =
      _buildCallReportHtml(rows, title, subtitle);
    document.getElementById('printPreviewOverlay').style.display = '';
  } catch(e) {
    toast('Report failed: ' + e.message, 'error');
  }
}

function _rptFilterSummary({ clientName, clientPhone, callType, start, end }) {
  const parts = [];
  if (clientName) parts.push(`${clientName}  ${fmtPhone(clientPhone || '')}`);

  if (start && end && start === end) parts.push(_fmtDateMDY(start));
  else if (start && end)             parts.push(`${_fmtDateMDY(start)} – ${_fmtDateMDY(end)}`);
  else if (start)                    parts.push(`From ${_fmtDateMDY(start)}`);
  else if (end)                      parts.push(`Through ${_fmtDateMDY(end)}`);
  else                               parts.push('All dates');

  if (callType === 'reminder')       parts.push('Reminder calls only');
  else if (callType === 'wellness')  parts.push('Wellness calls only');
  else                               parts.push('All call types');

  return parts.join('  ·  ');
}

function _buildCallReportHtml(rows, title, subtitle) {
  const printDate = new Date().toLocaleDateString('en-US',
    { weekday:'short', year:'numeric', month:'long', day:'numeric' });

  const rowsHtml = rows.map(r => {
    const schedCell = r.schedule_time
      ? `${fmtScheduleTime(r.schedule_time)}${r.schedule_name
          ? `<br><small>${esc(r.schedule_name)}</small>` : ''}`
      : '—';
    const typeLabel = { reminder:'Reminder', wellness:'Wellness', emergency:'Emergency' }[r.call_type]
      || r.call_type;
    const statusLabel = {
      system_down:   'System Down',
      internet_down: 'Internet Down',
      reached_human: 'Reached Human',
      left_voicemail:'Left Voicemail',
      'no-answer':   'No Answer',
      'wrong-keypress':'Wrong Key',
    }[r.status] || (r.status || '—');

    return `<tr>
      <td style="white-space:nowrap">${fmtTime(r.timestamp)}</td>
      <td>${esc(r.client_name)}</td>
      <td style="white-space:nowrap">${fmtPhone(r.client_phone || '')}</td>
      <td>${typeLabel}</td>
      <td style="white-space:nowrap">${schedCell}</td>
      <td style="text-align:center">#${r.attempt_number}</td>
      <td>${statusLabel}</td>
    </tr>`;
  }).join('');

  return `
    <div class="pp-header">
      <div class="pp-logo">CareCall</div>
      <div class="pp-print-date">Generated ${printDate}</div>
    </div>

    <div class="pp-name">${esc(title)}</div>
    <div class="pp-meta" style="margin-bottom:.15rem">${esc(subtitle)}</div>

    <div class="pp-section-title">
      Call Log &mdash; ${rows.length} record${rows.length !== 1 ? 's' : ''}
    </div>

    ${rows.length ? `
      <table class="pp-table">
        <thead><tr>
          <th>Date / Time</th>
          <th>Client</th>
          <th>Phone</th>
          <th>Type</th>
          <th>Schedule</th>
          <th>Attempt</th>
          <th>Status</th>
        </tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>`
    : '<p class="pp-empty" style="margin-top:.5rem">No calls found matching the selected filters.</p>'}
  `;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
async function loadClientsIfNeeded() {
  if (!allClients.length) {
    allClients = await api('GET', '/clients');
  }
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtPhone(e164) {
  if (!e164) return '';
  const digits = String(e164).replace(/\D/g, '');
  const local = (digits.length === 11 && digits[0] === '1') ? digits.slice(1) : digits;
  if (local.length === 10) {
    return `(${local.slice(0,3)}) ${local.slice(3,6)}-${local.slice(6)}`;
  }
  return e164; // non-US fallback — show as-is
}

function fmtBirthday(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  const today = new Date();
  let age = today.getFullYear() - d.getFullYear();
  const notYet = today.getMonth() < d.getMonth() ||
    (today.getMonth() === d.getMonth() && today.getDate() < d.getDate());
  if (notYet) age--;
  return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' }) +
    ` (age ${age})`;
}

function fmtAddress(c) {
  const lines = [c.address1, c.address2].filter(Boolean);
  const cityLine = [c.city, c.state, c.zip_code].filter(Boolean).join(', ');
  if (cityLine) lines.push(cityLine);
  return lines.join(' · ');
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtDays(str) {
  const map = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  if (!str) return 'Every day';
  const days = str.split(',').map(n => map[parseInt(n)]).filter(Boolean);
  if (days.length === 7) return 'Every day';
  return days.join(', ');
}

function statusBadge(s) {
  const classes = {
    initiated:        'badge-blue',
    answered:         'badge-blue',
    reached_human:    'badge-green',
    left_voicemail:   'badge-blue',
    acknowledged:     'badge-green',
    escalated:        'badge-green',
    'no-answer':      'badge-orange',
    busy:             'badge-orange',
    failed:           'badge-red',
    'wrong-keypress': 'badge-red',
    completed:        'badge-gray',
    system_down:      'badge-purple',
    internet_down:    'badge-purple',
  };
  const labels = {
    reached_human:    'Reached Human',
    left_voicemail:   'Left Voicemail',
    'no-answer':      'No Answer',
    'wrong-keypress': 'Wrong Key',
    system_down:      'System Down',
    internet_down:    'Internet Down',
  };
  const tips = {
    initiated:        'The call was placed but has not connected yet.',
    answered:         'A human picked up the phone.',
    reached_human:    'A human answered and heard the reminder message.',
    left_voicemail:   'Voicemail detected; the message was left after the beep.',
    acknowledged:     'The correct key was pressed confirming the person is okay.',
    escalated:        'An emergency contact confirmed they will follow up.',
    'no-answer':      'The call rang but nobody picked up.',
    busy:             'The line was busy.',
    failed:           'The call could not connect (bad number or carrier error).',
    'wrong-keypress': 'Someone answered but pressed the wrong key.',
    completed:        'The call finished normally.',
    system_down:      'Call missed — CareCall was not running at the scheduled time. Manual follow-up may be needed.',
    internet_down:    'Call attempted but failed — no internet or Twilio unreachable at the time.',
  };
  const tip = tips[s] ? ` data-tooltip="${tips[s]}"` : '';
  return `<span class="badge ${classes[s] || 'badge-gray'}"${tip}>${esc(labels[s] || s)}</span>`;
}

function wellnessSessionBadge(s) {
  const map = {
    pending:      ['badge-orange', 'Pending',      'Waiting to place the next call attempt.'],
    calling:      ['badge-blue',   'Calling',      'A call is actively in progress right now.'],
    acknowledged: ['badge-green',  'Acknowledged', 'The client pressed the correct key — confirmed okay.'],
    escalating:   ['badge-orange', 'Escalating',   'Max attempts reached; now calling emergency contacts in sequence.'],
    escalated:    ['badge-green',  'Escalated',    'An emergency contact confirmed they will follow up.'],
    failed:       ['badge-red',    'Failed',       'All client attempts and emergency contacts exhausted with no response.'],
    cancelled:    ['badge-gray',   'Cancelled',    'Manually stopped via the Stop button on the dashboard.'],
  };
  const [cls, label, tip] = map[s] || ['badge-gray', s, ''];
  const tipAttr = tip ? ` data-tooltip="${tip}"` : '';
  return `<span class="badge ${cls}"${tipAttr}>${label}</span>`;
}

async function cancelWellnessSession(id) {
  if (!confirm('Stop this wellness session? No further calls will be made.')) return;
  try {
    await api('POST', `/wellness-sessions/${id}/cancel`);
    toast('Session stopped', 'success');
    await loadDashboard();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function cancelReminderSession(id) {
  if (!confirm('Stop this reminder session? No further retry calls will be made.')) return;
  try {
    await api('POST', `/reminder-sessions/${id}/cancel`);
    toast('Reminder stopped', 'success');
    await loadDashboard();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function typeBadge(t) {
  const map = {
    reminder:  ['badge-blue',  'Reminder',  'A scheduled call that plays an audio message. No response required — just delivers the reminder.'],
    wellness:  ['badge-green', 'Wellness',  'A scheduled wellness check. The client must press a key to confirm they are okay.'],
    emergency: ['badge-red',   'Emergency', 'A call to an emergency contact after the client failed to respond to all wellness check attempts.'],
  };
  const [cls, label, tip] = map[t] || ['badge-gray', t, ''];
  const tipAttr = tip ? ` data-tooltip="${tip}"` : '';
  return `<span class="badge ${cls}"${tipAttr}>${label}</span>`;
}

function sessionStatusBadge(s) {
  const map = {
    pending:       ['badge-orange', 'In Progress'],
    calling:       ['badge-orange', 'In Progress'],
    escalating:    ['badge-red',    'Escalated'],
    escalated:     ['badge-red',    'Escalated'],
    acknowledged:  ['badge-green',  'Success'],
    reached_human: ['badge-green',  'Success'],
    left_voicemail:['badge-green',  'Success'],
    failed:        ['badge-red',    'Failed'],
    cancelled:     ['badge-gray',   'Cancelled'],
  };
  if (!s) return '<span class="badge badge-gray">—</span>';
  const [cls, label] = map[s] || ['badge-gray', s];
  return `<span class="badge ${cls}">${label}</span>`;
}

function schedStatusBadge(status) {
  const map = {
    admin_ok:      ['badge-green',  'Admin OK'],
    acknowledged:  ['badge-green',  'Acknowledged'],
    escalated:     ['badge-orange', 'Escalated'],
    reached_human: ['badge-green',  'Reached'],
    left_voicemail:['badge-blue',   'Voicemail'],
    failed:        ['badge-red',    'Failed'],
    pending:       ['badge-gray',   'Pending'],
    calling:       ['badge-blue',   'Calling…'],
    escalating:    ['badge-orange', 'Escalating'],
  };
  const [cls, label] = map[status] || ['badge-gray', status];
  return `<span class="badge ${cls}">${label}</span>`;
}

function fmtScheduleTime(t) {
  if (!t) return '—';
  const [h, m] = t.split(':').map(Number);
  const ampm = h >= 12 ? 'PM' : 'AM';
  const h12  = h % 12 || 12;
  return `${h12}:${m.toString().padStart(2, '0')} ${ampm}`;
}

// ── Print preview & PDF download ──────────────────────────────────────────────
let _printClientData  = null;
let _printReportTitle = null; // set when a report is opened; used for PDF filename

async function openPrintPreview() {
  if (!currentClientId) return;
  try {
    const [client, schedules, contacts] = await Promise.all([
      api('GET', `/clients/${currentClientId}`),
      api('GET', `/clients/${currentClientId}/schedules`),
      api('GET', `/clients/${currentClientId}/contacts`),
    ]);
    _printClientData = client;
    document.getElementById('printPageContent').innerHTML = _buildPrintHtml(client, schedules, contacts);
    document.getElementById('printPreviewOverlay').style.display = '';
  } catch(e) { toast('Could not load print preview: ' + e.message, 'error'); }
}

function closePrintPreview() {
  document.getElementById('printPreviewOverlay').style.display = 'none';
}

function printClientPage() {
  window.print();
}

async function downloadClientPdf() {
  const btn = document.getElementById('pdfDownloadBtn');
  btn.disabled = true;
  btn.textContent = 'Generating…';
  try {
    if (!window.html2canvas)
      await _loadScript('https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js');
    if (!window.jspdf)
      await _loadScript('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js');

    const el = document.getElementById('printPageContent');
    const canvas = await html2canvas(el, {
      scale: 2, useCORS: true, backgroundColor: '#ffffff', logging: false,
    });

    const { jsPDF } = window.jspdf;
    const pdf = new jsPDF({ unit: 'in', format: 'letter', orientation: 'portrait' });

    // Slice the canvas into letter-page (8.5×11in) segments
    const PW = canvas.width;                              // canvas pixels wide
    const PH = Math.round(PW * (11 / 8.5));              // pixels per page tall
    let yOff = 0, page = 0;

    while (yOff < canvas.height) {
      if (page > 0) pdf.addPage();
      const sliceH    = Math.min(PH, canvas.height - yOff);
      const pageC     = document.createElement('canvas');
      pageC.width     = PW;
      pageC.height    = PH;
      const ctx = pageC.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, PW, PH);
      ctx.drawImage(canvas, 0, yOff, PW, sliceH, 0, 0, PW, sliceH);
      pdf.addImage(pageC.toDataURL('image/jpeg', 0.93), 'JPEG', 0, 0, 8.5, 11);
      yOff += PH;
      page++;
    }

    const name = (_printReportTitle || _printClientData?.full_name || 'Report').replace(/[^a-zA-Z0-9 ]/g, ' ').trim().replace(/\s+/g, '_');
    pdf.save(`${name}_CareCall.pdf`);
  } catch(e) {
    toast('PDF generation failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⬇️ Download PDF';
  }
}

function _loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src; s.onload = resolve; s.onerror = reject;
    document.head.appendChild(s);
  });
}

function _buildPrintHtml(client, schedules, contacts) {
  const addr = [
    client.address1, client.address2,
    [client.city, client.state].filter(Boolean).join(', '),
    client.zip_code,
  ].filter(Boolean).join(', ');

  const dob = client.birthday ? _fmtDateMDY(client.birthday) : null;

  function _fmtDays(dow) {
    if (!dow) return 'Every day';
    const labels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    const days = dow.split(',').map(Number);
    if (days.length === 7) return 'Every day';
    if (days.join(',') === '0,1,2,3,4') return 'Weekdays';
    if (days.join(',') === '5,6') return 'Weekends';
    return days.map(d => labels[d]).join(', ');
  }

  const printDate = new Date().toLocaleDateString('en-US', { year:'numeric', month:'long', day:'numeric' });

  const schedRows = schedules.map(s => `
    <tr>
      <td>${fmtScheduleTime(s.time_of_day)}</td>
      <td>${esc(s.name || '—')}</td>
      <td>${s.call_type.charAt(0).toUpperCase() + s.call_type.slice(1)}</td>
      <td>${_fmtDays(s.days_of_week)}</td>
      <td style="text-align:center">${s.max_attempts}</td>
      <td style="text-align:center">${s.attempt_interval_minutes} min</td>
      <td>${s.active ? 'Active' : 'Inactive'}</td>
    </tr>`).join('');

  const contactRows = contacts.map(c => `
    <tr>
      <td style="text-align:center">${c.priority}</td>
      <td>${esc(c.name)}</td>
      <td>${fmtPhone(c.phone)}</td>
      <td>${esc(c.relationship || '—')}</td>
      <td style="text-align:center">${c.can_text ? 'Yes' : '—'}</td>
    </tr>`).join('');

  return `
    <div class="pp-header">
      <div class="pp-logo">CareCall</div>
      <div class="pp-print-date">Printed ${printDate}</div>
    </div>

    <div class="pp-name">${esc(client.full_name)}</div>
    <div class="pp-meta">
      ${fmtPhone(client.phone)}${dob ? ` &bull; DOB: ${dob}` : ''}
      &bull; <span class="${client.active ? 'pp-active' : 'pp-inactive'}">${client.active ? 'Active' : 'Inactive'}</span>
    </div>
    ${addr ? `<div class="pp-address">${esc(addr)}</div>` : ''}

    ${client.notes ? `
      <div class="pp-section-title">Notes</div>
      <div class="pp-notes">${esc(client.notes)}</div>` : ''}

    <div class="pp-section-title">Call Schedules</div>
    ${schedules.length ? `
      <table class="pp-table">
        <thead><tr>
          <th>Time</th><th>Name</th><th>Type</th><th>Days</th>
          <th>Max Attempts</th><th>Retry Interval</th><th>Status</th>
        </tr></thead>
        <tbody>${schedRows}</tbody>
      </table>` : '<p class="pp-empty">No call schedules configured.</p>'}

    <div class="pp-section-title">Emergency Contacts</div>
    ${contacts.length ? `
      <table class="pp-table">
        <thead><tr>
          <th>#</th><th>Name</th><th>Phone</th><th>Relationship</th><th>Can Text</th>
        </tr></thead>
        <tbody>${contactRows}</tbody>
      </table>` : '<p class="pp-empty">No emergency contacts configured.</p>'}
  `;
}

// ── Microphone recording (shared engine) ──────────────────────────────────────

// Global-tab entry point
function startRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    toast('A recording is already in progress', 'error');
    return;
  }
  recordingContext = 'global';
  _doStartRecording();
}

function stopRecording() {
  _doStopRecording();
}

async function _doStartRecording() {
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    if (e.name === 'NotAllowedError') {
      toast('Microphone access was denied. Please allow it in your browser settings.', 'error');
    } else {
      toast('Could not access microphone: ' + e.message, 'error');
    }
    return;
  }

  audioChunks = [];

  const preferredTypes = [
    'audio/webm;codecs=opus', 'audio/webm',
    'audio/ogg;codecs=opus',  'audio/ogg', 'audio/mp4',
  ];
  const mimeType = preferredTypes.find(t => MediaRecorder.isTypeSupported(t)) || '';
  mediaRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
  mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };

  const ctx = recordingContext; // capture for onstop closure
  mediaRecorder.onstop = () => {
    recordedBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
    const url = URL.createObjectURL(recordedBlob);
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }

    if (ctx === 'client') {
      document.getElementById('clientAudioPreview').src = url;
      document.getElementById('clientRecordPreview').style.display = '';
      document.getElementById('clientStopBtn').style.display = 'none';
      document.getElementById('clientRecordTimer').style.display = 'none';
      document.getElementById('clientRecDot').style.display = 'none';
    } else {
      document.getElementById('audioPreview').src = url;
      document.getElementById('recordPreview').style.display = '';
      document.getElementById('recordBtn').style.display = '';
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('recordTimer').style.display = 'none';
      document.getElementById('recordBtn').classList.remove('recording');
    }
  };

  mediaRecorder.start(250);
  recordStartTime = Date.now();

  // Which timer element to tick
  const timerId = recordingContext === 'client' ? 'clientRecordTimer' : 'recordTimer';
  document.getElementById(timerId).style.display = '';
  recordTimerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - recordStartTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    document.getElementById(timerId).textContent = `${m}:${s}`;
  }, 500);

  // Show the right controls
  if (recordingContext === 'client') {
    document.getElementById('clientRecorderPanel').style.display = '';
    document.getElementById('clientRecordPreview').style.display = 'none';
    document.getElementById('clientRecordBtn').style.display = 'none';
    document.getElementById('clientStopBtn').style.display = '';
    document.getElementById('clientRecDot').style.display = '';
  } else {
    document.getElementById('recordBtn').style.display = 'none';
    document.getElementById('stopBtn').style.display = '';
    document.getElementById('recordPreview').style.display = 'none';
    document.getElementById('recordBtn').classList.add('recording');
  }
}

function _doStopRecording() {
  clearInterval(recordTimerInterval);
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop(); // onstop fires async and finishes the UI update
  } else {
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  }
}

async function saveRecording() {
  if (!recordedBlob) { toast('Nothing recorded yet', 'error'); return; }

  let name = document.getElementById('recordName').value.trim();
  if (!name) { toast('Please enter a filename before saving', 'error'); return; }
  name = name.replace(/\.[^.]+$/, '');

  const fd = new FormData();
  fd.append('audio', recordedBlob, 'recording.webm');
  fd.append('name', name);
  fd.append('display_name', name.replace(/_/g, ' ').replace(/-/g, ' '));
  // No client_id — global recording from Audio tab

  const btn = document.querySelector('.record-panel .btn-primary');
  const orig = btn.textContent;
  btn.textContent = 'Converting…';
  btn.disabled = true;

  try {
    const res = await fetch('/api/record', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    toast(`Saved "${data.display_name || data.filename}"`, 'success');
    discardRecording();
    await loadAudio();
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

function discardRecording() {
  recordedBlob = null;
  const preview = document.getElementById('audioPreview');
  URL.revokeObjectURL(preview.src);
  preview.src = '';
  document.getElementById('recordPreview').style.display = 'none';
  document.getElementById('recordName').value = '';
}

// ── Backup ──────────────────────────────────────────────────────────────────

let _backupDrives = [];           // last drive list from server
let _fileBrowserCurrentPath = ''; // current path open in file browser
let _fileBrowserEntries = [];     // flat list: [{path, is_dir}, ...] indexed by onclick

async function loadBackupDrives() {
  const note = document.getElementById('driveRefreshNote');
  note.textContent = 'Scanning…';
  try {
    const res = await fetch('/api/backup/usb-drives');
    const data = await res.json();
    _backupDrives = data.drives || [];
    _renderBackupDrives(_backupDrives);
    // Populate destination select
    const sel = document.getElementById('backupDestSelect');
    while (sel.options.length > 1) sel.remove(1);
    _backupDrives.filter(d => d.mountpoint).forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.mountpoint;
      opt.textContent = `${d.label || d.device} (${d.size}) — ${d.mountpoint}`;
      sel.appendChild(opt);
    });
    note.textContent = _backupDrives.length
      ? `${_backupDrives.length} drive(s) found`
      : 'No USB drives detected';
  } catch (e) {
    note.textContent = 'Error scanning drives';
  }
}

function _renderBackupDrives(drives) {
  const el = document.getElementById('backupDriveList');
  if (!drives.length) {
    el.innerHTML = '<span style="color:var(--muted);font-size:.85rem">No removable drives detected.</span>';
    return;
  }
  // Use index-based onclick to avoid quote-escaping issues with paths in HTML attributes
  el.innerHTML = drives.map((d, i) => `
    <div class="backup-drive-card">
      <div class="backup-drive-info">
        <div class="backup-drive-label">💾 ${esc(d.label || d.device)}</div>
        <div class="backup-drive-meta">
          ${esc(d.device)} &nbsp;·&nbsp; ${esc(d.size)}
          ${d.vendor || d.model ? ` &nbsp;·&nbsp; ${esc([d.vendor, d.model].filter(Boolean).join(' '))}` : ''}
        </div>
        ${d.mountpoint
          ? `<div class="backup-drive-mount">Mounted at <code>${esc(d.mountpoint)}</code></div>`
          : '<div class="backup-drive-mount" style="color:var(--orange)">Not mounted</div>'}
      </div>
      <div class="backup-drive-actions">
        ${d.mountpoint
          ? `<button class="btn-ghost btn-sm" onclick="_backupBrowseDrive(${i})">📂 Browse</button>`
          : ''}
        <button class="btn-danger btn-sm" onclick="_backupFormatDrive(${i})">⚠️ Format exFAT</button>
      </div>
    </div>
  `).join('');
}

// Index-based wrappers so onclick attributes never contain quotes from path strings
function _backupBrowseDrive(i) {
  const d = _backupDrives[i];
  if (d && d.mountpoint) openFileBrowser(d.mountpoint);
}

function _backupFormatDrive(i) {
  const d = _backupDrives[i];
  if (!d) return;
  confirmFormatDrive(d.device, d.label || d.device);
}

function confirmFormatDrive(device, label) {
  if (!confirm(
    `⚠️  WARNING: This will ERASE ALL DATA on ${device} (${label}) and format it as exFAT.\n\n` +
    `This cannot be undone.\n\nContinue?`
  )) return;
  if (!confirm(`Final confirmation: permanently erase ${device}?`)) return;
  _doFormatDrive(device, label);
}

async function _doFormatDrive(device, label) {
  const note = document.getElementById('driveRefreshNote');
  note.textContent = `Formatting ${device}… (this may take a moment)`;
  try {
    const res = await fetch('/api/backup/format', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({device, label: 'CareCallBak'})
    });
    const data = await res.json();
    if (!res.ok) {
      console.error('Format error response:', data);
      note.textContent = `Format failed: ${data.error}`;
      toast(`Format failed: ${data.error}`, 'error');
    } else {
      if (data.mount_log) console.log('Mount attempts:', data.mount_log);
      const mp = data.mountpoint;
      note.textContent = mp
        ? `Format complete — mounted at ${mp}`
        : data.message || 'Format complete (refreshing…)';
      toast(mp ? `Formatted and mounted at ${mp}` : 'Format complete', 'success');
      // Refresh drive list; if we got a mountpoint back, auto-select it
      await loadBackupDrives();
      if (mp) {
        document.getElementById('backupDestPath').value = mp;
        document.getElementById('backupDestSelect').value = mp;
        document.getElementById('backupDestStatus').textContent = `Destination set to: ${mp}`;
      }
    }
  } catch (e) {
    note.textContent = `Error: ${e.message}`;
    toast(`Format error: ${e.message}`, 'error');
  }
}

// ── File browser ──────────────────────────────────────────────────────────

function openFileBrowser(path) {
  _fileBrowserCurrentPath = path;
  document.getElementById('fileBrowserOverlay').style.display = 'flex';
  _loadFileBrowserPath(path);
}

async function _loadFileBrowserPath(path) {
  const listEl = document.getElementById('fileBrowserList');
  const pathEl = document.getElementById('fileBrowserPath');
  pathEl.textContent = path;
  listEl.innerHTML = '<div style="padding:.75rem;color:var(--muted)">Loading…</div>';
  try {
    const res = await fetch(`/api/backup/browse?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (!res.ok) {
      listEl.innerHTML = `<div style="padding:.75rem;color:var(--red)">${esc(data.error)}</div>`;
      return;
    }
    _fileBrowserCurrentPath = data.path;
    pathEl.textContent = data.path;

    // Build a flat index array so onclick handlers use integers, not path strings
    _fileBrowserEntries = [];
    let html = '';

    if (data.parent) {
      const idx = _fileBrowserEntries.length;
      _fileBrowserEntries.push({ path: data.parent, is_dir: true });
      html += `<div class="fb-row fb-dir" onclick="_fbNavigate(${idx})">
        <span class="fb-icon">📁</span><span class="fb-name">..</span>
        <span class="fb-meta">Parent folder</span>
      </div>`;
    }

    data.entries.forEach(e => {
      const idx = _fileBrowserEntries.length;
      _fileBrowserEntries.push({ path: e.path, is_dir: e.is_dir });
      if (e.is_dir) {
        html += `<div class="fb-row fb-dir" onclick="_fbNavigate(${idx})">
          <span class="fb-icon">📁</span>
          <span class="fb-name">${esc(e.name)}</span>
          <span class="fb-meta">${e.modified}</span>
        </div>`;
      } else {
        html += `<div class="fb-row">
          <span class="fb-icon">📄</span>
          <span class="fb-name">${esc(e.name)}</span>
          <span class="fb-meta">${_fmtBytes(e.size)} &nbsp; ${e.modified}</span>
        </div>`;
      }
    });

    listEl.innerHTML = html || '<div style="padding:.75rem;color:var(--muted)">Empty folder</div>';
  } catch (err) {
    listEl.innerHTML = `<div style="padding:.75rem;color:var(--red)">Error: ${esc(err.message)}</div>`;
  }
}

function _fbNavigate(idx) {
  const entry = _fileBrowserEntries[idx];
  if (entry && entry.is_dir) _loadFileBrowserPath(entry.path);
}

function selectBrowsedPath() {
  if (!_fileBrowserCurrentPath) return;
  document.getElementById('backupDestPath').value = _fileBrowserCurrentPath;
  document.getElementById('backupDestSelect').value = '';
  document.getElementById('backupDestStatus').textContent = `Path set to: ${_fileBrowserCurrentPath}`;
  closeOverlay('fileBrowserOverlay');
}

// ── Destination helpers ───────────────────────────────────────────────────

function backupDestSelectChanged() {
  const val = document.getElementById('backupDestSelect').value;
  if (val) {
    document.getElementById('backupDestPath').value = val;
    document.getElementById('backupDestStatus').textContent = `Destination: ${val}`;
  }
}

function backupDestPathChanged() {
  document.getElementById('backupDestSelect').value = '';
  const v = document.getElementById('backupDestPath').value.trim();
  document.getElementById('backupDestStatus').textContent = v ? `Custom path: ${v}` : '';
}

function _getBackupDestination() {
  return document.getElementById('backupDestPath').value.trim();
}

// ── Run backup now ────────────────────────────────────────────────────────

async function runBackupNow() {
  const dest = _getBackupDestination();
  const resultEl = document.getElementById('backupRunResult');
  if (!dest) {
    resultEl.innerHTML = '<span style="color:var(--red)">Please select or enter a backup destination first.</span>';
    return;
  }
  resultEl.innerHTML = '<span style="color:var(--muted)">Running backup…</span>';
  try {
    const res = await fetch('/api/backup/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({destination: dest})
    });
    const data = await res.json();
    if (!res.ok) {
      resultEl.innerHTML = `<span style="color:var(--red)">Backup failed: ${esc(data.error)}</span>`;
    } else {
      resultEl.innerHTML = `<span style="color:var(--green)">✔ Backup complete — <strong>${esc(data.filename)}</strong> (${esc(data.size)})</span>`;
      toast('Backup complete!', 'success');
    }
  } catch (e) {
    resultEl.innerHTML = `<span style="color:var(--red)">Error: ${esc(e.message)}</span>`;
  }
}

// ── Scheduled backup config ───────────────────────────────────────────────

async function loadBackupConfig() {
  try {
    const res = await fetch('/api/backup/config');
    const cfg = await res.json();
    document.getElementById('backupEnabled').checked   = !!cfg.enabled;
    document.getElementById('backupFrequency').value   = cfg.frequency   || 'daily';
    document.getElementById('backupTime').value        = cfg.time        || '02:00';
    document.getElementById('backupDayOfWeek').value   = cfg.day_of_week || 'sun';
    document.getElementById('backupDayOfMonth').value  = String(cfg.day_of_month || 1);
    if (cfg.destination) {
      document.getElementById('backupDestPath').value = cfg.destination;
      document.getElementById('backupDestStatus').textContent = `Saved destination: ${cfg.destination}`;
    }
    _updateBackupScheduleUI();
  } catch (e) {
    console.warn('Could not load backup config:', e);
  }
}

function backupScheduleChanged() {
  _updateBackupScheduleUI();
}

function _updateBackupScheduleUI() {
  const enabled = document.getElementById('backupEnabled').checked;
  document.getElementById('backupScheduleFields').style.display = enabled ? 'flex' : 'none';
  const freq = document.getElementById('backupFrequency').value;
  document.getElementById('backupDayOfWeekGroup').style.display  = freq === 'weekly'  ? '' : 'none';
  document.getElementById('backupDayOfMonthGroup').style.display = freq === 'monthly' ? '' : 'none';
}

async function saveBackupConfig() {
  const dest = _getBackupDestination();
  const resultEl = document.getElementById('backupSaveResult');
  resultEl.textContent = 'Saving…';
  const payload = {
    enabled:      document.getElementById('backupEnabled').checked,
    destination:  dest,
    frequency:    document.getElementById('backupFrequency').value,
    time:         document.getElementById('backupTime').value,
    day_of_week:  document.getElementById('backupDayOfWeek').value,
    day_of_month: parseInt(document.getElementById('backupDayOfMonth').value, 10),
  };
  try {
    const res = await fetch('/api/backup/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      resultEl.innerHTML = `<span style="color:var(--red)">Error: ${esc(data.error)}</span>`;
    } else {
      resultEl.innerHTML = '<span style="color:var(--green)">✔ Schedule saved</span>';
      setTimeout(() => { resultEl.textContent = ''; }, 4000);
      toast('Backup schedule saved', 'success');
    }
  } catch (e) {
    resultEl.innerHTML = `<span style="color:var(--red)">Error: ${esc(e.message)}</span>`;
  }
}

// ── Byte formatter helper ─────────────────────────────────────────────────

function _fmtBytes(n) {
  if (n == null) return '';
  if (n > 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n > 1024)    return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}
