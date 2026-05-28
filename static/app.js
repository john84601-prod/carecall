'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let allClients = [];
let currentClientId = null;   // client being edited in modal
let scheduleContacts = [];    // ordered list for the schedule being edited
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
  loadDashboard();
});

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
    document.getElementById('statToday').textContent     = d.calls_today;

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
    if (!d.recent_logs.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">No calls yet.</td></tr>';
    } else {
      tbody.innerHTML = d.recent_logs.map(l => `
        <tr>
          <td style="white-space:nowrap">${fmtTime(l.timestamp)}</td>
          <td>${esc(l.client_name)}</td>
          <td>${typeBadge(l.call_type)}</td>
          <td>#${l.attempt_number}</td>
          <td>${statusBadge(l.status)}</td>
        </tr>`).join('');
    }

    // Wellness sessions panel
    const sessPanel = document.getElementById('wellnessSessionsPanel');
    const sessTbody = document.getElementById('wellnessSessionsTable');
    if (d.recent_sessions && d.recent_sessions.length) {
      sessPanel.style.display = '';
      sessTbody.innerHTML = d.recent_sessions.map(s => {
        const ackBy = s.acknowledged_by_contact_name
          ? `${esc(s.acknowledged_by_contact_name)}<br><small style="color:var(--muted)">${esc(s.acknowledged_by_contact_phone || '')}</small>`
          : (s.status === 'escalated' ? '—' : '');
        return `<tr>
          <td>${esc(s.client_name)}</td>
          <td>${wellnessSessionBadge(s.status)}</td>
          <td>${s.current_attempt}</td>
          <td style="white-space:nowrap">${fmtTime(s.started_at)}</td>
          <td style="white-space:nowrap">${s.resolved_at ? fmtTime(s.resolved_at) : '—'}</td>
          <td>${ackBy || '—'}</td>
        </tr>`;
      }).join('');
    } else {
      sessPanel.style.display = 'none';
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
    renderClients();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function renderClients() {
  const container = document.getElementById('clientsContainer');
  if (!allClients.length) {
    container.innerHTML = '<div class="empty">No clients yet. Click "+ Add Client" to get started.</div>';
    return;
  }
  container.innerHTML = '<div class="clients-grid">' +
    allClients.map(c => `
      <div class="client-card">
        <div class="client-card-head">
          <div>
            <div class="client-name">${esc(c.full_name)} ${c.active ? '' : '<span class="badge badge-gray">inactive</span>'}</div>
            <div class="client-phone">${esc(c.phone)}</div>
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

function renderEcSummary(contacts) {
  if (!contacts || !contacts.length) {
    return '<div class="ec-list" style="margin-top:.5rem;font-size:.8rem;color:var(--muted)">No emergency contacts</div>';
  }
  return '<div class="ec-list">' +
    contacts.map(ec => `
      <div class="ec-item">
        <span class="ec-priority">${ec.priority}</span>
        <span style="flex:1">${esc(ec.name)}${ec.relationship ? ' <span style="color:var(--muted)">(' + esc(ec.relationship) + ')</span>' : ''}</span>
        <span style="color:var(--muted)">${esc(ec.phone)}</span>
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
  document.getElementById('clientPhone').value = c.phone;
  document.getElementById('clientBirthday').value = c.birthday || '';
  document.getElementById('clientAddress1').value = c.address1 || '';
  document.getElementById('clientAddress2').value = c.address2 || '';
  document.getElementById('clientCity').value = c.city || '';
  document.getElementById('clientState').value = c.state || '';
  document.getElementById('clientZip').value = c.zip_code || '';
  document.getElementById('clientNotes').value = c.notes || '';
  document.getElementById('clientActive').checked = c.active;
  document.getElementById('clientTabsSection').style.display = '';
  switchClientTab('schedules');
  // Auto-expand address section if client has address or notes data
  const hasAddress = c.address1 || c.address2 || c.city || c.state || c.zip_code || c.notes;
  document.getElementById('addrNotesBody').style.display = hasAddress ? '' : 'none';
  document.getElementById('addrNotesToggle').querySelector('.caret-char').textContent = hasAddress ? '▼' : '▶';
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
  ['schedules', 'contacts', 'audio'].forEach(t => {
    document.getElementById(`clientTab-${t}`).classList.toggle('active', t === tab);
    document.getElementById(`clientPane-${t}`).style.display = t === tab ? '' : 'none';
  });
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
          <br><span style="color:var(--muted)">${esc(c.phone)}</span>
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
  openOverlay('contactOverlay');
}

function editContact(id, name, phone, rel, priority, canText) {
  currentContactId = id;
  document.getElementById('contactModalTitle').textContent = 'Edit Emergency Contact';
  document.getElementById('contactId').value = id;
  document.getElementById('contactName').value = name;
  document.getElementById('contactPhone').value = phone;
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
async function loadSchedules() {
  try {
    const schedules = await api('GET', '/schedules');
    const tbody = document.getElementById('schedulesTable');
    if (!schedules.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No schedules yet. Open a client profile to add one.</td></tr>';
      return;
    }
    tbody.innerHTML = schedules.map(s => `
      <tr>
        <td>${esc(s.name || '—')}</td>
        <td>${esc(s.client_name)}</td>
        <td>${typeBadge(s.call_type)}</td>
        <td style="white-space:nowrap;font-weight:600">${fmt12h(s.time_of_day)}</td>
        <td style="white-space:nowrap">${fmtDays(s.days_of_week)}</td>
        <td>${s.active
          ? '<span class="badge badge-green">Active</span>'
          : '<span class="badge badge-gray">Inactive</span>'}</td>
      </tr>`).join('');
  } catch (e) {
    toast(e.message, 'error');
  }
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

function renderClientSchedules(schedules) {
  const el = document.getElementById('clientSchedulesList');
  if (!schedules.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.4rem 0">No call schedules yet.</div>';
    return;
  }
  el.innerHTML = schedules.map(s => {
    const title = s.name || (s.call_type === 'reminder' ? 'Reminder' : 'Wellness Check');
    const meta = [fmt12h(s.time_of_day), fmtDays(s.days_of_week)].join(' · ');
    return `
      <div class="schedule-item">
        <div class="schedule-item-info">
          <div class="schedule-item-title">${esc(title)} ${typeBadge(s.call_type)}</div>
          <div class="schedule-item-meta">${meta}</div>
        </div>
        <input type="checkbox" class="toggle" ${s.active ? 'checked' : ''}
          title="${s.active ? 'Active' : 'Inactive'}"
          onchange="toggleClientSchedule(${s.id}, this.checked)">
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
  document.getElementById('scheduleKey').value = '1';
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
    document.getElementById('scheduleKey').value          = s.required_keypress || '1';
    document.getElementById('scheduleMaxAttempts').value  = s.max_attempts || 3;
    document.getElementById('scheduleInterval').value     = s.attempt_interval_minutes || 10;

    // Load per-schedule emergency contacts
    scheduleContacts = (s.schedule_contacts || []).map(sc => ({
      emergency_contact_id: sc.emergency_contact_id,
      name: sc.name,
      phone: sc.phone,
      relationship: sc.relationship || '',
      can_text: sc.can_text,
    }));

    // Reset then conditionally expand EC collapsible
    const schedEcBody = document.getElementById('schedEcBody');
    const schedEcTog  = document.getElementById('schedEcToggle');
    const hasEcContacts = scheduleContacts.length > 0 && s.call_type === 'wellness';
    if (schedEcBody) schedEcBody.style.display = hasEcContacts ? '' : 'none';
    if (schedEcTog)  schedEcTog.querySelector('.caret-char').textContent = hasEcContacts ? '▼' : '▶';

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

  const time24 = _to24h(
    document.getElementById('scheduleHour').value,
    document.getElementById('scheduleMinute').value,
    document.getElementById('scheduleAmPm').value,
  );

  const callType = document.querySelector('input[name="callType"]:checked').value;

  const payload = {
    client_id:                parseInt(clientId),
    name:                     document.getElementById('scheduleName').value.trim(),
    call_type:                callType,
    time_of_day:              time24,
    days_of_week:             days,
    mp3_filename:             document.getElementById('scheduleMp3').value || null,
    required_keypress:        document.getElementById('scheduleKey').value || '1',
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
        scheduleContacts.map(c => ({ emergency_contact_id: c.emergency_contact_id }))
      );
    }
    closeOverlay('scheduleOverlay');
    await loadClientSchedules(currentClientId);
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function toggleClientSchedule(id, active) {
  try {
    await api('PUT', `/schedules/${id}`, { active });
    toast(active ? 'Schedule activated' : 'Schedule paused');
    await loadClientSchedules(currentClientId);
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
    // Auto-expand EC collapsible if contacts are already assigned
    if (scheduleContacts.length > 0) {
      const body = document.getElementById('schedEcBody');
      const tog  = document.getElementById('schedEcToggle');
      if (body) body.style.display = '';
      if (tog)  tog.querySelector('.caret-char').textContent = '▼';
    }
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
    ? `(${scheduleContacts.length} assigned)`
    : '(none — uses client contact list)';

  if (!scheduleContacts.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.25rem 0">None assigned — will fall back to the client\'s contact list.</div>';
  } else {
    el.innerHTML = scheduleContacts.map((c, i) => `
      <div class="ec-item" style="margin-bottom:.3rem">
        <span class="ec-priority">${i + 1}</span>
        <span style="flex:1">
          <strong>${esc(c.name)}</strong>${c.relationship ? ' · ' + esc(c.relationship) : ''}
          <br><span style="color:var(--muted);font-size:.82rem">${esc(c.phone)}</span>
        </span>
        <div class="action-btns">
          <button type="button" class="btn-ghost btn-sm" title="Move up"
            onclick="moveScheduleContact(${i}, -1)" ${i === 0 ? 'disabled' : ''}>▲</button>
          <button type="button" class="btn-ghost btn-sm" title="Move down"
            onclick="moveScheduleContact(${i}, 1)" ${i === scheduleContacts.length - 1 ? 'disabled' : ''}>▼</button>
          <button type="button" class="btn-danger btn-sm" onclick="removeScheduleContact(${i})">✕</button>
        </div>
      </div>`).join('');
  }

  // Refresh dropdown to show only unassigned contacts
  const assignedIds = new Set(scheduleContacts.map(c => c.emergency_contact_id));
  const available = clientContactsCache.filter(c => !assignedIds.has(c.id));
  const sel = document.getElementById('scheduleEcAdd');
  if (sel) {
    sel.innerHTML = '<option value="">— add a contact from the client\'s list —</option>' +
      available.map(c =>
        `<option value="${c.id}">${esc(c.name)}${c.relationship ? ' (' + esc(c.relationship) + ')' : ''}</option>`
      ).join('');
  }

  const hint = document.getElementById('scheduleEcHint');
  if (hint) {
    hint.textContent = clientContactsCache.length === 0
      ? 'Add emergency contacts to this client\'s profile first.'
      : '';
  }
}

function addScheduleContact() {
  const sel = document.getElementById('scheduleEcAdd');
  const id = parseInt(sel.value);
  if (!id) return;
  const contact = clientContactsCache.find(c => c.id === id);
  if (!contact) return;
  scheduleContacts.push({
    emergency_contact_id: contact.id,
    name: contact.name,
    phone: contact.phone,
    relationship: contact.relationship || '',
    can_text: contact.can_text,
  });
  renderScheduleEcList();
}

function moveScheduleContact(index, dir) {
  const newIndex = index + dir;
  if (newIndex < 0 || newIndex >= scheduleContacts.length) return;
  [scheduleContacts[index], scheduleContacts[newIndex]] =
    [scheduleContacts[newIndex], scheduleContacts[index]];
  renderScheduleEcList();
}

function removeScheduleContact(index) {
  scheduleContacts.splice(index, 1);
  renderScheduleEcList();
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

// ── Helpers ───────────────────────────────────────────────────────────────────
async function loadClientsIfNeeded() {
  if (!allClients.length) {
    allClients = await api('GET', '/clients');
  }
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
    initiated:      'badge-blue',
    answered:       'badge-blue',
    reached_human:  'badge-green',
    left_voicemail: 'badge-blue',
    acknowledged:   'badge-green',
    escalated:      'badge-green',
    'no-answer':    'badge-orange',
    busy:           'badge-orange',
    failed:         'badge-red',
    'wrong-keypress': 'badge-red',
    completed:      'badge-gray',
  };
  const labels = {
    reached_human:  'Reached Human',
    left_voicemail: 'Left Voicemail',
    'no-answer':    'No Answer',
    'wrong-keypress': 'Wrong Key',
  };
  return `<span class="badge ${classes[s] || 'badge-gray'}">${esc(labels[s] || s)}</span>`;
}

function wellnessSessionBadge(s) {
  const map = {
    pending:    ['badge-orange', 'Pending'],
    calling:    ['badge-blue',   'Calling'],
    acknowledged: ['badge-green', 'Acknowledged'],
    escalating: ['badge-orange', 'Escalating'],
    escalated:  ['badge-green',  'Escalated'],
    failed:     ['badge-red',    'Failed'],
  };
  const [cls, label] = map[s] || ['badge-gray', s];
  return `<span class="badge ${cls}">${label}</span>`;
}

function typeBadge(t) {
  return t === 'reminder'  ? '<span class="badge badge-blue">Reminder</span>' :
         t === 'wellness'  ? '<span class="badge badge-green">Wellness</span>' :
         t === 'emergency' ? '<span class="badge badge-red">Emergency</span>' :
         `<span class="badge badge-gray">${esc(t)}</span>`;
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
