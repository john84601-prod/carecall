'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let allClients = [];
let currentClientId = null;   // client being edited in modal

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
  document.getElementById('schedulesPanel').style.display = 'none';
  document.getElementById('ecPanel').style.display = 'none';
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
  document.getElementById('schedulesPanel').style.display = '';
  document.getElementById('ecPanel').style.display = '';
  await Promise.all([loadClientSchedules(id), loadContactsList(id)]);
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
      document.getElementById('ecPanel').style.display = '';
      await loadContactsList(created.id);
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
        </span>
        <div class="action-btns">
          <button class="btn-edit btn-sm" onclick="editContact(${c.id},'${esc(c.name)}','${esc(c.phone)}','${esc(c.relationship)}',${c.priority})">Edit</button>
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
  openOverlay('contactOverlay');
}

function editContact(id, name, phone, rel, priority) {
  currentContactId = id;
  document.getElementById('contactModalTitle').textContent = 'Edit Emergency Contact';
  document.getElementById('contactId').value = id;
  document.getElementById('contactName').value = name;
  document.getElementById('contactPhone').value = phone;
  document.getElementById('contactRelationship').value = rel;
  document.getElementById('contactPriority').value = priority;
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
  await populateAudioSelect();
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
  resetAudioPreview();
  toggleScheduleFields();
  openOverlay('scheduleOverlay');
}

async function editClientSchedule(id) {
  _initTimePicker();
  await populateAudioSelect();
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
    if (id) {
      await api('PUT', `/schedules/${id}`, payload);
      toast('Schedule updated', 'success');
    } else {
      await api('POST', '/schedules', payload);
      toast('Schedule created', 'success');
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
  document.getElementById('reminderFields').style.display = type === 'reminder' ? '' : 'none';
  document.getElementById('wellnessFields').style.display = type === 'wellness' ? '' : 'none';
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
    sel.innerHTML = '<option value="">— select audio file —</option>' +
      files.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('');
    if (cur) sel.value = cur;
  } catch (_) {}
}

// ── Audio files ───────────────────────────────────────────────────────────────
async function loadAudio() {
  try {
    const files = await api('GET', '/uploads');
    renderAudio(files);
  } catch (e) {
    toast(e.message, 'error');
  }
}

function renderAudio(files) {
  const el = document.getElementById('audioList');
  if (!files.length) {
    el.innerHTML = '<div class="empty">No audio files uploaded yet.</div>';
    return;
  }
  el.innerHTML = files.map(f => `
    <div class="audio-item">
      <svg class="audio-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zM21 16a3 3 0 11-6 0 3 3 0 016 0z"/>
      </svg>
      <span class="audio-name">${esc(f)}</span>
      <div class="audio-actions">
        <a href="/uploads/${encodeURIComponent(f)}" class="btn-edit btn-sm" target="_blank">Play</a>
        <button class="btn-danger btn-sm" onclick="deleteAudio('${esc(f)}')">Delete</button>
      </div>
    </div>`).join('');
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
    try {
      const res = await fetch('/api/uploads', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      toast(`Uploaded ${data.filename}`, 'success');
    } catch (e) {
      toast(e.message, 'error');
    }
  }
  await loadAudio();
}

async function deleteAudio(filename) {
  if (!confirm(`Delete "${filename}"?`)) return;
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
    initiated: 'badge-blue', answered: 'badge-blue',
    acknowledged: 'badge-green', escalated: 'badge-green',
    'no-answer': 'badge-orange', busy: 'badge-orange',
    failed: 'badge-red', 'wrong-keypress': 'badge-red',
    completed: 'badge-gray',
  };
  return `<span class="badge ${classes[s] || 'badge-gray'}">${esc(s)}</span>`;
}

function typeBadge(t) {
  return t === 'reminder'  ? '<span class="badge badge-blue">Reminder</span>' :
         t === 'wellness'  ? '<span class="badge badge-green">Wellness</span>' :
         t === 'emergency' ? '<span class="badge badge-red">Emergency</span>' :
         `<span class="badge badge-gray">${esc(t)}</span>`;
}

// ── Microphone recording ───────────────────────────────────────────────────────
async function startRecording() {
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

  // Pick the best supported format
  const preferredTypes = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/mp4',
  ];
  const mimeType = preferredTypes.find(t => MediaRecorder.isTypeSupported(t)) || '';

  mediaRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
  mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
  mediaRecorder.onstop = () => {
    recordedBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
    const url = URL.createObjectURL(recordedBlob);
    document.getElementById('audioPreview').src = url;
    document.getElementById('recordPreview').style.display = '';
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  };

  mediaRecorder.start(250); // collect in 250ms chunks
  recordStartTime = Date.now();

  // Update UI
  document.getElementById('recordBtn').style.display = 'none';
  document.getElementById('stopBtn').style.display = '';
  document.getElementById('recordTimer').style.display = '';
  document.getElementById('recordPreview').style.display = 'none';
  document.getElementById('recordBtn').classList.add('recording');

  recordTimerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - recordStartTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    document.getElementById('recordTimer').textContent = `${m}:${s}`;
  }, 500);
}

function stopRecording() {
  clearInterval(recordTimerInterval);
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  }
  document.getElementById('recordBtn').style.display = '';
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('recordTimer').style.display = 'none';
  document.getElementById('recordBtn').classList.remove('recording');
}

async function saveRecording() {
  if (!recordedBlob) { toast('Nothing recorded yet', 'error'); return; }

  let name = document.getElementById('recordName').value.trim();
  if (!name) { toast('Please enter a filename before saving', 'error'); return; }
  // Strip any extension the user typed — the server always saves as .mp3
  name = name.replace(/\.[^.]+$/, '');

  const fd = new FormData();
  fd.append('audio', recordedBlob, 'recording.webm');
  fd.append('name', name);

  const btn = document.querySelector('.record-panel .btn-primary');
  const orig = btn.textContent;
  btn.textContent = 'Converting…';
  btn.disabled = true;

  try {
    const res = await fetch('/api/record', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    toast(`Saved "${data.filename}"`, 'success');
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
