import os
import re
import json as _json
import zipfile
from datetime import date, datetime as _dt

from flask import Blueprint, jsonify, request, current_app
from carecall import db
from carecall.models import Client, EmergencyContact, Schedule, ScheduleContact, AudioFile, CallLog, WellnessSession, ReminderSession, WellnessBlackout, InboundMessage

# Project root (two levels up from this file: routes/ → carecall/ → project/)
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKUP_CONFIG_PATH  = os.path.join(_APP_ROOT, 'backup_config.json')
_SYSTEM_CONFIG_PATH  = os.path.join(_APP_ROOT, 'system_config.json')

api_bp = Blueprint('api', __name__)


# ── Clients ────────────────────────────────────────────────────────────────────

@api_bp.route('/clients', methods=['GET'])
def get_clients():
    clients = Client.query.order_by(Client.last_name, Client.first_name).all()
    return jsonify([c.to_dict() for c in clients])


@api_bp.route('/clients', methods=['POST'])
def create_client():
    data = request.get_json()
    if not data or not data.get('first_name') or not data.get('phone'):
        return jsonify({'error': 'first_name and phone are required'}), 400
    client = Client(
        first_name=data['first_name'].strip(),
        last_name=data.get('last_name', '').strip(),
        phone=_normalize_phone(data['phone']),
        address1=data.get('address1', '').strip(),
        address2=data.get('address2', '').strip(),
        city=data.get('city', '').strip(),
        state=data.get('state', '').strip().upper(),
        zip_code=data.get('zip_code', '').strip(),
        birthday=_parse_date(data.get('birthday')),
        notes=data.get('notes', ''),
    )
    db.session.add(client)
    db.session.commit()
    return jsonify(client.to_dict()), 201


@api_bp.route('/clients/<int:client_id>', methods=['GET'])
def get_client(client_id):
    return jsonify(db.get_or_404(Client, client_id).to_dict())


@api_bp.route('/clients/<int:client_id>', methods=['PUT'])
def update_client(client_id):
    client = db.get_or_404(Client, client_id)
    data = request.get_json()
    client.first_name = data.get('first_name', client.first_name).strip()
    client.last_name  = data.get('last_name',  client.last_name).strip()
    client.phone    = _normalize_phone(data.get('phone', client.phone))
    client.address1 = data.get('address1', client.address1).strip()
    client.address2 = data.get('address2', client.address2).strip()
    client.city     = data.get('city',     client.city).strip()
    client.state    = data.get('state',    client.state).strip().upper()
    client.zip_code  = data.get('zip_code',  client.zip_code).strip()
    if 'birthday' in data:
        client.birthday = _parse_date(data['birthday'])
    client.notes    = data.get('notes',    client.notes)
    client.active   = data.get('active',   client.active)
    db.session.commit()
    return jsonify(client.to_dict())


@api_bp.route('/clients/<int:client_id>', methods=['DELETE'])
def delete_client(client_id):
    client = db.get_or_404(Client, client_id)
    db.session.delete(client)
    db.session.commit()
    return '', 204


# ── Emergency contacts ─────────────────────────────────────────────────────────

@api_bp.route('/clients/<int:client_id>/contacts', methods=['GET'])
def get_contacts(client_id):
    db.get_or_404(Client, client_id)
    contacts = (EmergencyContact.query
                .filter_by(client_id=client_id)
                .order_by(EmergencyContact.priority)
                .all())
    return jsonify([c.to_dict() for c in contacts])


@api_bp.route('/clients/<int:client_id>/contacts', methods=['POST'])
def create_contact(client_id):
    db.get_or_404(Client, client_id)
    data = request.get_json()
    if not data or not data.get('name') or not data.get('phone'):
        return jsonify({'error': 'name and phone are required'}), 400
    contact = EmergencyContact(
        client_id=client_id,
        name=data['name'],
        phone=_normalize_phone(data['phone']),
        relationship=data.get('relationship', ''),
        priority=data.get('priority', 1),
        can_text=bool(data.get('can_text', False)),
    )
    db.session.add(contact)
    db.session.commit()
    return jsonify(contact.to_dict()), 201


@api_bp.route('/contacts/<int:contact_id>', methods=['PUT'])
def update_contact(contact_id):
    contact = db.get_or_404(EmergencyContact, contact_id)
    data = request.get_json()
    contact.name = data.get('name', contact.name)
    contact.phone = _normalize_phone(data.get('phone', contact.phone))
    contact.relationship = data.get('relationship', contact.relationship)
    contact.priority = data.get('priority', contact.priority)
    if 'can_text' in data:
        contact.can_text = bool(data['can_text'])
    db.session.commit()
    return jsonify(contact.to_dict())


@api_bp.route('/contacts/<int:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    contact = db.get_or_404(EmergencyContact, contact_id)
    db.session.delete(contact)
    db.session.commit()
    return '', 204


# ── Schedules ──────────────────────────────────────────────────────────────────

@api_bp.route('/clients/<int:client_id>/schedules', methods=['GET'])
def get_client_schedules(client_id):
    db.get_or_404(Client, client_id)
    schedules = (Schedule.query
                 .filter_by(client_id=client_id)
                 .order_by(Schedule.time_of_day)
                 .all())
    return jsonify([s.to_dict() for s in schedules])


@api_bp.route('/schedules', methods=['GET'])
def get_schedules():
    schedules = Schedule.query.order_by(Schedule.client_id, Schedule.time_of_day).all()
    return jsonify([s.to_dict() for s in schedules])


@api_bp.route('/schedules/completions', methods=['GET'])
def schedule_completions():
    """Return {schedule_id: status} for every session on the given local date."""
    from datetime import datetime, date as _date, timedelta
    date_str = request.args.get('date')
    try:
        target_date = _date.fromisoformat(date_str) if date_str else _date.today()
    except ValueError:
        return jsonify({'statuses': {}}), 400

    _local_now  = datetime.now()
    _utc_now    = datetime.utcnow()
    _utc_offset = _utc_now - _local_now
    day_start   = datetime.combine(target_date, datetime.min.time()) + _utc_offset
    day_end     = day_start + timedelta(days=1)

    statuses = {}
    for ws in WellnessSession.query.filter(
        WellnessSession.started_at >= day_start,
        WellnessSession.started_at <  day_end,
    ).all():
        if ws.schedule_id:
            statuses[ws.schedule_id] = ws.status

    for rs in ReminderSession.query.filter(
        ReminderSession.started_at >= day_start,
        ReminderSession.started_at <  day_end,
    ).all():
        if rs.schedule_id:
            statuses[rs.schedule_id] = rs.status

    return jsonify({'statuses': statuses})


@api_bp.route('/schedules/<int:schedule_id>/admin-ok', methods=['POST'])
def schedule_admin_ok(schedule_id):
    """Administratively mark a schedule's call as complete for a given date."""
    from datetime import datetime
    sched = db.get_or_404(Schedule, schedule_id)
    now   = datetime.utcnow()

    if sched.call_type == 'wellness':
        session = WellnessSession(
            schedule_id=schedule_id,
            client_id=sched.client_id,
            status='admin_ok',
            current_attempt=0,
            started_at=now,
            resolved_at=now,
        )
        db.session.add(session)
        db.session.flush()
        log = CallLog(
            schedule_id=schedule_id,
            client_id=sched.client_id,
            wellness_session_id=session.id,
            call_type='wellness',
            attempt_number=0,
            status='admin_ok',
            timestamp=now,
            notes='Admin OK',
        )
    else:
        session = ReminderSession(
            schedule_id=schedule_id,
            client_id=sched.client_id,
            status='admin_ok',
            current_attempt=0,
            started_at=now,
            resolved_at=now,
        )
        db.session.add(session)
        db.session.flush()
        log = CallLog(
            schedule_id=schedule_id,
            client_id=sched.client_id,
            reminder_session_id=session.id,
            call_type='reminder',
            attempt_number=0,
            status='admin_ok',
            timestamp=now,
            notes='Admin OK',
        )

    db.session.add(log)
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/schedules', methods=['POST'])
def create_schedule():
    from carecall.scheduler import activate_schedule
    data = request.get_json()
    if not data or not data.get('client_id') or not data.get('call_type') or not data.get('time_of_day'):
        return jsonify({'error': 'client_id, call_type, and time_of_day are required'}), 400

    schedule = Schedule(
        client_id=data['client_id'],
        name=data.get('name', ''),
        call_type=data['call_type'],
        time_of_day=data['time_of_day'],
        days_of_week=data.get('days_of_week', '0,1,2,3,4,5,6'),
        mp3_filename=data.get('mp3_filename'),
        required_keypress=data.get('required_keypress', '1'),
        max_attempts=min(int(data.get('max_attempts', 3)), 20),
        attempt_interval_minutes=data.get('attempt_interval_minutes', 10),
        active=data.get('active', True),
    )
    db.session.add(schedule)
    db.session.commit()

    if schedule.active:
        activate_schedule(schedule)

    return jsonify(schedule.to_dict()), 201


@api_bp.route('/schedules/<int:schedule_id>', methods=['PUT'])
def update_schedule(schedule_id):
    from carecall.scheduler import activate_schedule, deactivate_schedule
    schedule = db.get_or_404(Schedule, schedule_id)
    was_active = schedule.active
    data = request.get_json()

    schedule.name = data.get('name', schedule.name)
    schedule.call_type = data.get('call_type', schedule.call_type)
    schedule.time_of_day = data.get('time_of_day', schedule.time_of_day)
    schedule.days_of_week = data.get('days_of_week', schedule.days_of_week)
    schedule.mp3_filename = data.get('mp3_filename', schedule.mp3_filename)
    schedule.required_keypress = data.get('required_keypress', schedule.required_keypress)
    if 'max_attempts' in data:
        schedule.max_attempts = min(int(data['max_attempts']), 20)
    schedule.attempt_interval_minutes = data.get('attempt_interval_minutes', schedule.attempt_interval_minutes)
    schedule.active = data.get('active', schedule.active)
    db.session.commit()

    if schedule.active:
        activate_schedule(schedule)
    elif was_active:
        deactivate_schedule(schedule_id)

    return jsonify(schedule.to_dict())


@api_bp.route('/schedules/<int:schedule_id>/contacts', methods=['PUT'])
def set_schedule_contacts(schedule_id):
    """Replace the full ordered emergency-contact list for a wellness schedule."""
    db.get_or_404(Schedule, schedule_id)
    data = request.get_json() or []
    ScheduleContact.query.filter_by(schedule_id=schedule_id).delete()
    for i, item in enumerate(data, start=1):
        sc = ScheduleContact(
            schedule_id=schedule_id,
            emergency_contact_id=int(item['emergency_contact_id']),
            priority=i,
        )
        db.session.add(sc)
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    from carecall.scheduler import deactivate_schedule
    schedule = db.get_or_404(Schedule, schedule_id)
    deactivate_schedule(schedule_id)
    db.session.delete(schedule)
    db.session.commit()
    return '', 204


# ── Audio files ────────────────────────────────────────────────────────────────

@api_bp.route('/uploads', methods=['GET'])
def list_uploads():
    """Return all audio files. Optional ?client_id=N to filter to one client only.
    Optional ?q=text for a search across filename, display name, client name/phone."""
    client_id = request.args.get('client_id', type=int)
    q = request.args.get('q', '').strip().lower()

    query = AudioFile.query
    if client_id is not None:
        query = query.filter_by(client_id=client_id)

    # Global files first, then client files; alphabetical within each group
    files = query.order_by(AudioFile.client_id.is_(None).desc(), AudioFile.display_name).all()

    if q:
        files = [f for f in files if
                 q in f.filename.lower() or
                 q in (f.display_name or '').lower() or
                 (f.client and q in f.client.full_name.lower()) or
                 (f.client and q in (f.client.phone or '').lower())]

    return jsonify([f.to_dict() for f in files])


@api_bp.route('/uploads', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    if not f.filename or not f.filename.lower().endswith('.mp3'):
        return jsonify({'error': 'Only .mp3 files are accepted'}), 400

    client_id   = request.form.get('client_id',   type=int)
    display_name = request.form.get('display_name', '').strip()
    filename = re.sub(r'[^\w\-.]', '_', f.filename)
    f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))

    if not display_name:
        display_name = filename.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ').title()

    # Upsert AudioFile record
    af = AudioFile.query.filter_by(filename=filename).first()
    if af:
        af.client_id    = client_id
        af.display_name = display_name
    else:
        af = AudioFile(filename=filename, display_name=display_name, client_id=client_id)
        db.session.add(af)
    db.session.commit()
    return jsonify(af.to_dict()), 201


@api_bp.route('/uploads/<filename>', methods=['DELETE'])
def delete_upload(filename):
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    if os.path.isfile(path):
        os.remove(path)
    af = AudioFile.query.filter_by(filename=filename).first()
    if af:
        db.session.delete(af)
        db.session.commit()
    return '', 204


@api_bp.route('/record', methods=['POST'])
def save_recording():
    """Accept a browser-recorded audio blob, convert to MP3 via ffmpeg, save to uploads/."""
    import subprocess
    import tempfile

    if 'audio' not in request.files:
        return jsonify({'error': 'No audio data received'}), 400

    audio_file   = request.files['audio']
    name         = request.form.get('name', '').strip()
    client_id    = request.form.get('client_id', type=int)
    display_name = request.form.get('display_name', '').strip() or name

    if not name:
        return jsonify({'error': 'Filename is required'}), 400

    # Sanitize and force .mp3 extension
    filename = re.sub(r'[^\w\-]', '_', name) + '.mp3'
    output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

    # Save the raw browser audio to a temp file
    with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                'ffmpeg', '-y',
                '-i', tmp_path,
                '-acodec', 'libmp3lame',
                '-ab', '128k',
                '-ar', '8000',   # 8kHz is optimal for phone audio
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            current_app.logger.error(f"ffmpeg stderr: {result.stderr}")
            return jsonify({'error': 'Audio conversion failed. Check that ffmpeg is installed.'}), 500
    except FileNotFoundError:
        return jsonify({
            'error': 'ffmpeg is not installed. Run: sudo apt-get install ffmpeg'
        }), 500
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Audio conversion timed out'}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Upsert AudioFile record
    af = AudioFile.query.filter_by(filename=filename).first()
    if af:
        af.client_id    = client_id
        af.display_name = display_name
    else:
        af = AudioFile(filename=filename, display_name=display_name, client_id=client_id)
        db.session.add(af)
    db.session.commit()
    return jsonify(af.to_dict()), 201


# ── Wellness blackout dates ────────────────────────────────────────────────────

@api_bp.route('/clients/<int:client_id>/blackouts', methods=['GET'])
def get_blackouts(client_id):
    db.get_or_404(Client, client_id)
    blackouts = (WellnessBlackout.query
                 .filter_by(client_id=client_id)
                 .order_by(WellnessBlackout.start_date)
                 .all())
    return jsonify([b.to_dict() for b in blackouts])


@api_bp.route('/clients/<int:client_id>/blackouts', methods=['POST'])
def create_blackout(client_id):
    db.get_or_404(Client, client_id)
    data = request.get_json()
    start = _parse_date((data or {}).get('start_date'))
    end   = _parse_date((data or {}).get('end_date'))
    if not start or not end:
        return jsonify({'error': 'start_date and end_date are required (YYYY-MM-DD)'}), 400
    if end < start:
        return jsonify({'error': 'end_date must be on or after start_date'}), 400
    b = WellnessBlackout(
        client_id  = client_id,
        start_date = start,
        end_date   = end,
        note       = (data.get('note') or '').strip(),
    )
    db.session.add(b)
    db.session.commit()
    return jsonify(b.to_dict()), 201


@api_bp.route('/blackouts/<int:blackout_id>', methods=['PUT'])
def update_blackout(blackout_id):
    b    = db.get_or_404(WellnessBlackout, blackout_id)
    data = request.get_json() or {}
    if 'start_date' in data:
        b.start_date = _parse_date(data['start_date'])
    if 'end_date' in data:
        b.end_date = _parse_date(data['end_date'])
    if 'note' in data:
        b.note = (data['note'] or '').strip()
    if not b.start_date or not b.end_date:
        return jsonify({'error': 'Invalid date'}), 400
    if b.end_date < b.start_date:
        return jsonify({'error': 'end_date must be on or after start_date'}), 400
    db.session.commit()
    return jsonify(b.to_dict())


@api_bp.route('/blackouts/<int:blackout_id>', methods=['DELETE'])
def delete_blackout(blackout_id):
    b = db.get_or_404(WellnessBlackout, blackout_id)
    db.session.delete(b)
    db.session.commit()
    return '', 204


# ── Call logs & sessions ───────────────────────────────────────────────────────

@api_bp.route('/logs', methods=['GET'])
def get_logs():
    limit = request.args.get('limit', 100, type=int)
    logs = CallLog.query.order_by(CallLog.timestamp.desc()).limit(limit).all()
    return jsonify([l.to_dict() for l in logs])


@api_bp.route('/wellness-sessions/<int:session_id>/cancel', methods=['POST'])
def cancel_wellness_session(session_id):
    """Immediately stop a wellness session and all pending retries/escalations."""
    from datetime import datetime
    from carecall.models import WellnessSession
    session = db.get_or_404(WellnessSession, session_id)
    if session.status not in ('pending', 'calling', 'escalating'):
        return jsonify({'error': 'Session is not active'}), 400
    session.status = 'cancelled'
    session.resolved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/reminder-sessions/<int:session_id>/cancel', methods=['POST'])
def cancel_reminder_session(session_id):
    """Immediately stop a reminder retry session."""
    from datetime import datetime
    from carecall.models import ReminderSession
    session = db.get_or_404(ReminderSession, session_id)
    if session.status not in ('pending', 'calling'):
        return jsonify({'error': 'Session is not active'}), 400
    session.status = 'cancelled'
    session.resolved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/sessions', methods=['GET'])
def get_sessions():
    sessions = WellnessSession.query.order_by(WellnessSession.started_at.desc()).limit(50).all()
    return jsonify([s.to_dict() for s in sessions])


@api_bp.route('/reminder-sessions', methods=['GET'])
def get_reminder_sessions():
    sessions = ReminderSession.query.order_by(ReminderSession.started_at.desc()).limit(50).all()
    return jsonify([s.to_dict() for s in sessions])


@api_bp.route('/dashboard', methods=['GET'])
def dashboard():
    from datetime import datetime, date as _date, timedelta
    from sqlalchemy import func, distinct as sa_distinct

    # Use local calendar day (not UTC) so "today" matches the server's clock.
    # Convert local midnight to the equivalent naive-UTC value for DB comparison.
    _local_now  = datetime.now()
    _utc_now    = datetime.utcnow()
    _utc_offset = _utc_now - _local_now          # timedelta: how far ahead UTC is
    local_midnight = datetime.combine(_date.today(), datetime.min.time())
    today_start = local_midnight + _utc_offset   # local midnight expressed as UTC

    # Core counts
    active_clients   = Client.query.filter_by(active=True).count()
    active_schedules = Schedule.query.filter_by(active=True).count()

    # Reminder stats
    active_reminder_schedules  = Schedule.query.filter_by(active=True, call_type='reminder').count()
    reminder_completed_today   = ReminderSession.query.filter(
        ReminderSession.started_at >= today_start,
        ReminderSession.status.in_(['reached_human', 'left_voicemail', 'admin_ok']),
    ).count()
    reminder_calls_today = CallLog.query.filter(
        CallLog.timestamp >= today_start,
        CallLog.call_type == 'reminder',
    ).count()

    # Wellness stats
    active_wellness_schedules = Schedule.query.filter_by(active=True, call_type='wellness').count()
    wellness_completed_today  = WellnessSession.query.filter(
        WellnessSession.started_at >= today_start,
        WellnessSession.status.in_(['acknowledged', 'escalated', 'admin_ok']),
    ).count()
    wellness_calls_today = CallLog.query.filter(
        CallLog.timestamp >= today_start,
        CallLog.call_type == 'wellness',
    ).count()

    # Alert cycles today = distinct wellness sessions that STARTED today and
    # escalated to emergency contacts.  Filtering on session.started_at (not
    # call log timestamp) prevents runaway post-midnight calls from a previous
    # night's session inflating today's count.
    alert_cycles_today = db.session.query(
        func.count(sa_distinct(CallLog.wellness_session_id))
    ).join(
        WellnessSession, CallLog.wellness_session_id == WellnessSession.id
    ).filter(
        CallLog.call_type == 'emergency',
        WellnessSession.started_at >= today_start,
    ).scalar() or 0

    # Active/in-progress sessions
    active_sessions = WellnessSession.query.filter(
        WellnessSession.status.in_(['pending', 'calling', 'escalating'])
    ).count()
    active_reminder_sessions_qs = ReminderSession.query.filter(
        ReminderSession.status.in_(['pending', 'calling'])
    ).order_by(ReminderSession.started_at.desc()).all()

    # ── Upcoming calls: scheduled slots for today + active retry sessions ────
    _local_time_str = _local_now.strftime('%H:%M')
    today_dow       = str(_local_now.weekday())   # '0'=Mon .. '6'=Sun

    # Schedule IDs already running in an active session (exclude from plain slots)
    _active_sched_ids = {
        ws.schedule_id for ws in WellnessSession.query.filter(
            WellnessSession.status.in_(['pending', 'calling', 'escalating'])
        ).all()
    } | {
        rs.schedule_id for rs in ReminderSession.query.filter(
            ReminderSession.status.in_(['pending', 'calling'])
        ).all()
    }

    upcoming_calls = []

    # 1. Scheduled slots not yet fired today
    for s in Schedule.query.filter_by(active=True).all():
        dow_list = [d.strip() for d in (s.days_of_week or '').split(',')]
        if today_dow not in dow_list:
            continue
        if s.time_of_day <= _local_time_str:
            continue
        if s.id in _active_sched_ids:
            continue
        upcoming_calls.append({
            'client_name':     s.client.full_name if s.client else '',
            'schedule_name':   s.name or '',
            'schedule_time':   s.time_of_day,
            'next_attempt_at': s.time_of_day,
            'call_type':       s.call_type,
            'attempt_number':  None,
        })

    # 2. Active wellness retry sessions
    for ws in WellnessSession.query.filter(
        WellnessSession.status.in_(['pending', 'calling', 'escalating'])
    ).all():
        last_log = (CallLog.query
                    .filter_by(wellness_session_id=ws.id)
                    .order_by(CallLog.timestamp.desc())
                    .first())
        sched = ws.schedule
        if last_log and sched:
            next_local = last_log.timestamp + timedelta(minutes=sched.attempt_interval_minutes) - _utc_offset
            upcoming_calls.append({
                'client_name':     ws.client.full_name if ws.client else '',
                'schedule_name':   sched.name or '',
                'schedule_time':   sched.time_of_day,
                'next_attempt_at': next_local.strftime('%H:%M'),
                'call_type':       'wellness',
                'attempt_number':  ws.current_attempt + 1,
            })

    # 3. Active reminder retry sessions
    for rs in ReminderSession.query.filter(
        ReminderSession.status.in_(['pending', 'calling'])
    ).all():
        last_log = (CallLog.query
                    .filter_by(reminder_session_id=rs.id)
                    .order_by(CallLog.timestamp.desc())
                    .first())
        sched = rs.schedule
        if last_log and sched:
            next_local = last_log.timestamp + timedelta(minutes=sched.attempt_interval_minutes) - _utc_offset
            upcoming_calls.append({
                'client_name':     rs.client.full_name if rs.client else '',
                'schedule_name':   sched.name or '',
                'schedule_time':   sched.time_of_day,
                'next_attempt_at': next_local.strftime('%H:%M'),
                'call_type':       'reminder',
                'attempt_number':  rs.current_attempt + 1,
            })

    upcoming_calls.sort(key=lambda x: x['next_attempt_at'])

    # Wellness alert cycles: only sessions that escalated to emergency contacts today.
    # A session qualifies if at least one emergency call was placed for it.
    _escalated_session_ids = (
        db.session.query(CallLog.wellness_session_id)
        .filter(
            CallLog.call_type == 'emergency',
            CallLog.timestamp  >= today_start,
            CallLog.wellness_session_id.isnot(None),
        )
        .distinct()
    )
    recent_sessions = (
        WellnessSession.query
        .filter(
            WellnessSession.started_at >= today_start,
            WellnessSession.id.in_(_escalated_session_ids),
        )
        .order_by(WellnessSession.started_at.desc())
        .all()
    )

    return jsonify({
        'active_clients':   active_clients,
        'active_schedules': active_schedules,
        # Reminder breakdown
        'active_reminder_schedules':  active_reminder_schedules,
        'reminder_completed_today':   reminder_completed_today,
        'reminder_calls_today':       reminder_calls_today,
        # Wellness breakdown
        'active_wellness_schedules':  active_wellness_schedules,
        'wellness_completed_today':   wellness_completed_today,
        'wellness_calls_today':       wellness_calls_today,
        # Alert cycles
        'alert_cycles_today': alert_cycles_today,
        # In-progress sessions (drive the conditional alert cards)
        'active_sessions': active_sessions,
        'active_reminder_sessions': len(active_reminder_sessions_qs),
        'active_reminder_sessions_list': [s.to_dict() for s in active_reminder_sessions_qs],
        # Detail panels
        'upcoming_calls':  upcoming_calls,
        'recent_sessions': [s.to_dict() for s in recent_sessions],
    })


# ── Reports ────────────────────────────────────────────────────────────────────

@api_bp.route('/reports/calls', methods=['GET'])
def report_calls():
    """Flexible call log report used by both Individual and System Call History.

    Query params:
      name        – partial first or last name match (Individual report)
      phone       – partial phone number match (Individual report)
      call_type   – 'reminder' | 'wellness' | '' (both)
                    'wellness' also includes 'emergency' escalation calls
      start_date  – YYYY-MM-DD local date (inclusive)
      end_date    – YYYY-MM-DD local date (inclusive)
    """
    from datetime import datetime as _dt, timedelta

    client_id  = request.args.get('client_id',  type=int)   # exact match (Individual report)
    name       = request.args.get('name',       '').strip().lower()
    phone      = request.args.get('phone',      '').strip()
    call_type  = request.args.get('call_type',  '')   # '' = both
    start_date = request.args.get('start_date', '')
    end_date   = request.args.get('end_date',   '')

    # Convert local dates to UTC boundaries for DB comparison
    local_now  = _dt.now()
    utc_offset = _dt.utcnow() - local_now   # timedelta: how far ahead UTC is

    q = CallLog.query

    if start_date:
        sd = _parse_date(start_date)
        if sd:
            q = q.filter(CallLog.timestamp >= _dt.combine(sd, _dt.min.time()) + utc_offset)

    if end_date:
        ed = _parse_date(end_date)
        if ed:
            # end of local day = start of next day in UTC
            q = q.filter(CallLog.timestamp <
                         _dt.combine(ed, _dt.min.time()) + utc_offset + timedelta(days=1))

    if call_type == 'reminder':
        q = q.filter(CallLog.call_type == 'reminder')
    elif call_type == 'wellness':
        # Include emergency escalation calls — they are part of the wellness flow
        q = q.filter(CallLog.call_type.in_(['wellness', 'emergency']))

    # Exact client match (Individual report — user selected from autocomplete)
    if client_id:
        q = q.filter(CallLog.client_id == client_id)

    if name or phone:
        q = q.join(Client, CallLog.client_id == Client.id)
        if name:
            q = q.filter(
                db.or_(
                    Client.first_name.ilike(f'%{name}%'),
                    Client.last_name.ilike(f'%{name}%'),
                )
            )
        if phone:
            digits = re.sub(r'\D', '', phone)
            if digits:
                q = q.filter(Client.phone.contains(digits))

    logs = q.order_by(CallLog.timestamp.desc()).limit(2000).all()
    return jsonify([l.to_dict() for l in logs])


# ── Settings & test ────────────────────────────────────────────────────────────

# ── TTS voice setting ──────────────────────────────────────────────────────────

_ALLOWED_VOICES = [
    # American English
    'Polly.Danielle-Neural', 'Polly.Gregory-Neural', 'Polly.Ivy-Neural',
    'Polly.Joanna-Neural',   'Polly.Joey-Neural',    'Polly.Justin-Neural',
    'Polly.Kendra-Neural',   'Polly.Kevin-Neural',   'Polly.Kimberly-Neural',
    'Polly.Matthew-Neural',  'Polly.Ruth-Neural',    'Polly.Salli-Neural',
    'Polly.Stephen-Neural',
    # British English
    'Polly.Amy-Neural', 'Polly.Arthur-Neural', 'Polly.Brian-Neural', 'Polly.Emma-Neural',
    # Australian English
    'Polly.Olivia-Neural',
]

_DEFAULT_VOICE = 'Polly.Joanna-Neural'


@api_bp.route('/voice', methods=['GET'])
def get_voice():
    cfg = _load_system_config()
    return jsonify({'voice': cfg.get('tts_voice', _DEFAULT_VOICE)})


@api_bp.route('/voice', methods=['POST'])
def set_voice():
    data  = request.get_json() or {}
    voice = str(data.get('voice', _DEFAULT_VOICE))
    if voice not in _ALLOWED_VOICES:
        return jsonify({'error': 'Invalid voice selection'}), 400
    cfg = _load_system_config()
    cfg['tts_voice'] = voice
    _save_system_config(cfg)
    return jsonify({'success': True, 'voice': voice})


# ── Inbound greeting config ────────────────────────────────────────────────────

_DEFAULT_GREETING_SCRIPT = (
    "You have reached CareCall. "
    "Please leave a message after the tone and we will follow up with you. "
    "Press the pound key when finished."
)
_INBOUND_GREETING_FILE = '_inbound_greeting.mp3'


@api_bp.route('/inbound-greeting', methods=['GET'])
def get_inbound_greeting():
    cfg = _load_system_config()
    upload_folder = current_app.config['UPLOAD_FOLDER']
    has_recording = os.path.isfile(os.path.join(upload_folder, _INBOUND_GREETING_FILE))
    return jsonify({
        'type':            cfg.get('inbound_greeting_type', 'script'),
        'script':          cfg.get('inbound_greeting_script', _DEFAULT_GREETING_SCRIPT),
        'has_recording':   has_recording,
    })


@api_bp.route('/inbound-greeting', methods=['POST'])
def save_inbound_greeting():
    data = request.get_json() or {}
    cfg  = _load_system_config()
    if 'type' in data:
        cfg['inbound_greeting_type'] = 'recording' if data['type'] == 'recording' else 'script'
    if 'script' in data:
        cfg['inbound_greeting_script'] = str(data['script']).strip() or _DEFAULT_GREETING_SCRIPT
    _save_system_config(cfg)
    return jsonify({'success': True})


@api_bp.route('/inbound-greeting/recording', methods=['POST'])
def upload_inbound_greeting_recording():
    """Accept a browser-recorded blob, convert to MP3, save as the inbound greeting."""
    import subprocess, tempfile
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio data received'}), 400
    audio_file  = request.files['audio']
    upload_folder = current_app.config['UPLOAD_FOLDER']
    output_path = os.path.join(upload_folder, _INBOUND_GREETING_FILE)
    with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', tmp_path,
             '-acodec', 'libmp3lame', '-ab', '128k', '-ar', '8000', output_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            current_app.logger.error(f"ffmpeg greeting: {result.stderr}")
            return jsonify({'error': 'Audio conversion failed. Is ffmpeg installed?'}), 500
    except FileNotFoundError:
        return jsonify({'error': 'ffmpeg is not installed. Run: sudo apt-get install ffmpeg'}), 500
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Conversion timed out'}), 500
    finally:
        try: os.unlink(tmp_path)
        except OSError: pass
    # Switch config to use recording
    cfg = _load_system_config()
    cfg['inbound_greeting_type'] = 'recording'
    _save_system_config(cfg)
    return jsonify({'success': True})


@api_bp.route('/inbound-greeting/recording', methods=['DELETE'])
def delete_inbound_greeting_recording():
    upload_folder = current_app.config['UPLOAD_FOLDER']
    path = os.path.join(upload_folder, _INBOUND_GREETING_FILE)
    try:
        os.unlink(path)
    except OSError:
        pass
    cfg = _load_system_config()
    cfg['inbound_greeting_type'] = 'script'
    _save_system_config(cfg)
    return jsonify({'success': True})


# ── Inbound messages ───────────────────────────────────────────────────────────

@api_bp.route('/inbound-messages', methods=['GET'])
def list_inbound_messages():
    msgs = InboundMessage.query.order_by(InboundMessage.received_at.desc()).all()
    # Backfill duration for any messages where it came back as 0
    changed = False
    for m in msgs:
        if m.duration_seconds == 0 and m.recording_sid:
            try:
                rec = _get_twilio_client().recordings(m.recording_sid).fetch()
                if rec.duration:
                    m.duration_seconds = int(rec.duration)
                    changed = True
            except Exception:
                pass
    if changed:
        db.session.commit()
    return jsonify([m.to_dict() for m in msgs])


@api_bp.route('/inbound-messages/unread-count', methods=['GET'])
def inbound_unread_count():
    count = InboundMessage.query.filter_by(listened=False).count()
    return jsonify({'count': count})


@api_bp.route('/inbound-messages/<int:msg_id>', methods=['PATCH'])
def update_inbound_message(msg_id):
    msg = db.session.get(InboundMessage, msg_id)
    if not msg:
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json() or {}
    if 'listened' in data:
        msg.listened = bool(data['listened'])
    if 'notes' in data:
        msg.notes = str(data['notes'])
    db.session.commit()
    return jsonify(msg.to_dict())


@api_bp.route('/inbound-messages/<int:msg_id>', methods=['DELETE'])
def delete_inbound_message(msg_id):
    msg = db.session.get(InboundMessage, msg_id)
    if not msg:
        return jsonify({'error': 'Not found'}), 404
    # Delete the Twilio recording too so we don't accumulate storage
    if msg.recording_sid:
        try:
            _get_twilio_client().recordings(msg.recording_sid).delete()
        except Exception as e:
            current_app.logger.warning(f"Could not delete Twilio recording {msg.recording_sid}: {e}")
    db.session.delete(msg)
    db.session.commit()
    return '', 204


@api_bp.route('/inbound-messages/<int:msg_id>/audio', methods=['GET'])
def proxy_inbound_audio(msg_id):
    """Proxy the Twilio recording so credentials stay server-side."""
    import requests as _req
    from io import BytesIO
    from flask import send_file
    msg = db.session.get(InboundMessage, msg_id)
    if not msg or not msg.recording_sid:
        return jsonify({'error': 'Not found'}), 404
    account_sid = os.getenv('TWILIO_ACCOUNT_SID', '')
    auth_token  = os.getenv('TWILIO_AUTH_TOKEN', '')
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Recordings/{msg.recording_sid}.mp3"
    try:
        r = _req.get(url, auth=(account_sid, auth_token), timeout=30)
        if r.status_code != 200:
            current_app.logger.error(f"Twilio returned {r.status_code} for recording {msg.recording_sid}")
            return jsonify({'error': 'Recording not available'}), 502
        audio = r.content
        # Backfill duration if it was 0 when the recording callback fired
        if not msg.duration_seconds and audio:
            try:
                rec = _get_twilio_client().recordings(msg.recording_sid).fetch()
                if rec.duration:
                    msg.duration_seconds = int(rec.duration)
                    db.session.commit()
            except Exception:
                pass
        # send_file with BytesIO lets Werkzeug handle Range requests,
        # which browsers require to play audio via the <audio> element.
        return send_file(BytesIO(audio), mimetype='audio/mpeg', download_name='message.mp3')
    except Exception as e:
        current_app.logger.error(f"Audio proxy error for {msg.recording_sid}: {e}")
        return jsonify({'error': 'Could not fetch recording'}), 502


def _get_twilio_client():
    from twilio.rest import Client as _TwilioClient
    return _TwilioClient(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))


@api_bp.route('/status', methods=['GET'])
def get_system_status():
    """Live system health check — scheduler, public URL, Twilio config, uptime, server time."""
    from datetime import datetime as _dt
    from carecall.scheduler import is_scheduler_running
    from carecall.tunnel import get_public_url

    # ── Scheduler ──────────────────────────────────────────────────────────
    scheduler_ok = is_scheduler_running()

    # ── Public URL / ngrok ─────────────────────────────────────────────────
    try:
        public_url = get_public_url()
        url_ok = bool(public_url and public_url.startswith('http'))
    except Exception:
        public_url = None
        url_ok = False

    # ── Twilio credentials ─────────────────────────────────────────────────
    twilio_ok = bool(
        os.getenv('TWILIO_ACCOUNT_SID', '').startswith('AC') and
        os.getenv('TWILIO_AUTH_TOKEN', '') and
        os.getenv('TWILIO_FROM_NUMBER', '')
    )

    # ── Uptime ─────────────────────────────────────────────────────────────
    start = current_app.config.get('START_TIME')
    if start:
        secs = int((_dt.now() - start).total_seconds())
        d, r  = divmod(secs, 86400)
        h, r  = divmod(r,    3600)
        m     = r // 60
        uptime = (f"{d}d {h}h {m}m" if d else f"{h}h {m}m" if h else f"{m}m")
    else:
        uptime = '—'

    # ── Server time (local) ────────────────────────────────────────────────
    server_time = _dt.now().strftime('%a, %b %-d, %Y  %-I:%M %p')

    calls_paused = _load_system_config().get('calls_paused', False)

    return jsonify({
        'scheduler_running': scheduler_ok,
        'public_url':        public_url,
        'public_url_ok':     url_ok,
        'twilio_configured': twilio_ok,
        'calls_paused':      calls_paused,
        'uptime':            uptime,
        'server_time':       server_time,
        'all_ok':            scheduler_ok and url_ok and twilio_ok and not calls_paused,
    })


@api_bp.route('/calls/paused', methods=['GET'])
def get_calls_paused():
    return jsonify({'paused': _load_system_config().get('calls_paused', False)})


@api_bp.route('/calls/paused', methods=['POST'])
def set_calls_paused():
    data = request.get_json() or {}
    cfg = _load_system_config()
    cfg['calls_paused'] = bool(data.get('paused', False))
    _save_system_config(cfg)
    return jsonify({'success': True, 'paused': cfg['calls_paused']})


@api_bp.route('/version', methods=['GET'])
def get_version():
    """Return the running git commit hash, date, and GitHub repo for update checks."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    def _git(*args):
        try:
            return subprocess.check_output(
                ['git'] + list(args), cwd=root, stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            return None

    commit_short = _git('rev-parse', '--short', 'HEAD') or 'unknown'
    commit_long  = _git('rev-parse', 'HEAD')             or 'unknown'
    commit_date  = _git('log', '-1', '--format=%cd', '--date=format:%B %d, %Y') or 'unknown'
    remote_url   = _git('remote', 'get-url', 'origin')   or ''

    # Extract owner/repo from https://github.com/owner/repo.git
    import re
    m = re.search(r'github\.com[/:](.+?)(?:\.git)?$', remote_url)
    github_repo = m.group(1) if m else None

    return jsonify({
        'commit':      commit_short,
        'commit_long': commit_long,
        'date':        commit_date,
        'github_repo': github_repo,
    })


@api_bp.route('/settings', methods=['GET'])
def get_settings():
    from carecall.tunnel import get_public_url
    try:
        public_url = get_public_url()
    except Exception:
        public_url = os.getenv('PUBLIC_URL', '(not available)')
    return jsonify({
        'twilio_account_sid': os.getenv('TWILIO_ACCOUNT_SID', '(not set)'),
        'twilio_from_number': os.getenv('TWILIO_FROM_NUMBER', '(not set)'),
        'public_url': public_url,
    })


@api_bp.route('/test-call', methods=['POST'])
def test_call():
    from carecall.twilio_client import make_call
    from carecall.tunnel import get_public_url
    data = request.get_json()
    to = _normalize_phone((data or {}).get('to', ''))
    if not to:
        return jsonify({'error': 'Phone number required'}), 400
    try:
        base = get_public_url()
        sid = make_call(to, f"{base}/webhook/test", f"{base}/webhook/test")
        return jsonify({'success': True, 'call_sid': sid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/test-sms', methods=['POST'])
def test_sms():
    from carecall.twilio_client import send_sms
    data = request.get_json()
    to   = _normalize_phone((data or {}).get('to', ''))
    name = (data or {}).get('name', 'this contact')
    if not to:
        return jsonify({'error': 'Phone number required'}), 400
    try:
        body = (
            f"CareCall test message for {name}. "
            f"If you receive this, SMS alerts are working correctly. "
            f"During a real alert you can reply OK to acknowledge."
        )
        sid = send_sms(f"+1{to}" if not to.startswith('+') else to, body)
        return jsonify({'success': True, 'message_sid': sid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Backup ────────────────────────────────────────────────────────────────────

@api_bp.route('/backup/usb-drives', methods=['GET'])
def backup_usb_drives():
    """List USB / removable storage partitions visible to the system."""
    import subprocess
    try:
        result = subprocess.run(
            ['lsblk', '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,LABEL,RM,VENDOR,MODEL'],
            capture_output=True, text=True, timeout=10
        )
        data = _json.loads(result.stdout)
    except FileNotFoundError:
        return jsonify({'drives': [], 'note': 'lsblk not available on this platform'})
    except Exception as e:
        return jsonify({'drives': [], 'error': str(e)})

    drives = []

    def _walk(node, parent_removable=False):
        is_rm = str(node.get('rm', '0')) in ('1', 'true', 'True')
        node_type = node.get('type', '')
        mount = (node.get('mountpoint') or '').strip()
        is_media = mount.startswith('/media') or mount.startswith('/mnt')

        if node_type == 'part' and (is_rm or parent_removable or is_media):
            drives.append({
                'device':     f"/dev/{node['name']}",
                'size':       node.get('size', '?'),
                'label':      (node.get('label') or node['name']).strip(),
                'mountpoint': mount or None,
                'vendor':     (node.get('vendor') or '').strip(),
                'model':      (node.get('model') or '').strip(),
            })
        for child in node.get('children') or []:
            _walk(child, is_rm or parent_removable)

    for dev in data.get('blockdevices', []):
        _walk(dev)

    return jsonify({'drives': drives})


@api_bp.route('/backup/browse', methods=['GET'])
def backup_browse():
    """Return a directory listing. Restricted to /media and /mnt."""
    import stat as _stat
    path = request.args.get('path', '/media')
    real = os.path.realpath(path)
    allowed = ('/media', '/mnt')
    if not any(real.startswith(r) for r in allowed):
        return jsonify({'error': 'Access restricted to /media and /mnt'}), 403
    try:
        entries = []
        for name in sorted(os.listdir(real)):
            full = os.path.join(real, name)
            try:
                s = os.stat(full)
                is_dir = _stat.S_ISDIR(s.st_mode)
                entries.append({
                    'name':     name,
                    'path':     full,
                    'is_dir':   is_dir,
                    'size':     None if is_dir else s.st_size,
                    'modified': _dt.fromtimestamp(s.st_mtime).strftime('%Y-%m-%d %H:%M'),
                })
            except Exception:
                pass
        parent = os.path.dirname(real)
        can_go_up = any(parent.startswith(r) for r in allowed)
        return jsonify({'path': real, 'parent': parent if can_go_up else None, 'entries': entries})
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/backup/format', methods=['POST'])
def backup_format():
    """Unmount and format a partition as exFAT. Requires passwordless sudo for mkfs.exfat."""
    import subprocess
    data = request.get_json() or {}
    device = data.get('device', '')
    label  = (data.get('label') or 'CareCallBak')[:11]   # FAT label limit

    # Only allow real block-device partition paths
    if not re.match(r'^/dev/(sd[a-z][1-9]|mmcblk\d+p\d+|vd[a-z][1-9])$', device):
        return jsonify({'error': 'Invalid device path'}), 400

    try:
        subprocess.run(['sudo', 'umount', device], capture_output=True)
        result = subprocess.run(
            ['sudo', 'mkfs.exfat', '-n', label, device],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or 'Format failed').strip()
            return jsonify({'error': err}), 500

        # Let udev finish processing the new filesystem signature before mounting
        subprocess.run(['udevadm', 'settle'], capture_output=True, timeout=10)

        mountpoint = None
        mount_log = []

        # ── Attempt 1: udisksctl (no sudo needed, auto-picks mount point) ──
        r1 = subprocess.run(
            ['udisksctl', 'mount', '-b', device],
            capture_output=True, text=True, timeout=15
        )
        mount_log.append(f'udisksctl: rc={r1.returncode} out={r1.stdout.strip()!r} err={r1.stderr.strip()!r}')
        if r1.returncode == 0:
            m = re.search(r'at\s+(\S+)', r1.stdout)
            if m:
                mountpoint = m.group(1).rstrip('.')

        # ── Attempt 2: sudo mkdir + sudo mount to /media/<user>/<label> ────
        if not mountpoint:
            import getpass, pwd
            user = getpass.getuser()
            pw   = pwd.getpwnam(user)
            mp   = f'/media/{user}/{label}'
            subprocess.run(['sudo', 'mkdir', '-p', mp], capture_output=True)
            # Pass uid/gid so the service account owns the filesystem (not root)
            r2 = subprocess.run(
                ['sudo', 'mount', '-t', 'exfat',
                 '-o', f'uid={pw.pw_uid},gid={pw.pw_gid}',
                 device, mp],
                capture_output=True, text=True, timeout=15
            )
            mount_log.append(f'sudo mount: rc={r2.returncode} {(r2.stderr or r2.stdout or "").strip()}')
            if r2.returncode == 0:
                mountpoint = mp

        return jsonify({
            'success':    True,
            'message':    f'{device} formatted as exFAT (label: {label})',
            'mountpoint': mountpoint,
            'mount_log':  mount_log,   # visible in browser console for debugging
        })
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Format timed out after 2 minutes'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/backup/run', methods=['POST'])
def backup_run():
    """Create a backup ZIP immediately at the given destination directory."""
    data = request.get_json() or {}
    destination = (data.get('destination') or '').strip()
    if not destination:
        return jsonify({'error': 'Destination path required'}), 400
    real_dest = os.path.realpath(destination)
    if not os.path.isdir(real_dest):
        return jsonify({'error': f'Destination does not exist: {destination}'}), 400
    try:
        result = _do_backup(real_dest)
        return jsonify({'success': True, 'filename': result['filename'], 'size': result['size']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/backup/config', methods=['GET'])
def get_backup_config():
    return jsonify(_load_backup_config())


@api_bp.route('/backup/config', methods=['POST'])
def save_backup_config():
    data = request.get_json() or {}
    config = {
        'enabled':       bool(data.get('enabled', False)),
        'destination':   str(data.get('destination', '')),
        'frequency':     str(data.get('frequency', 'daily')),
        'time':          str(data.get('time', '02:00')),
        'day_of_week':   str(data.get('day_of_week', 'sun')),
        'day_of_month':  int(data.get('day_of_month', 1)),
    }
    _save_backup_config(config)
    try:
        from carecall.scheduler import update_backup_job
        update_backup_job(config)
    except Exception as e:
        current_app.logger.warning(f"Backup scheduler update failed: {e}")
    return jsonify({'success': True})


# ── System config helpers ──────────────────────────────────────────────────────

def _load_system_config():
    try:
        with open(_SYSTEM_CONFIG_PATH) as f:
            return _json.load(f)
    except Exception:
        return {}


def _save_system_config(cfg):
    with open(_SYSTEM_CONFIG_PATH, 'w') as f:
        _json.dump(cfg, f, indent=2)


# ── Backup helpers ─────────────────────────────────────────────────────────────

def _load_backup_config():
    defaults = {
        'enabled': False, 'destination': '',
        'frequency': 'daily', 'time': '02:00',
        'day_of_week': 'sun', 'day_of_month': 1,
    }
    try:
        with open(_BACKUP_CONFIG_PATH) as f:
            defaults.update(_json.load(f))
    except Exception:
        pass
    return defaults


def _save_backup_config(config):
    with open(_BACKUP_CONFIG_PATH, 'w') as f:
        _json.dump(config, f, indent=2)


def _do_backup(destination_dir):
    """Build a timestamped ZIP of all essential CareCall files."""
    ts = _dt.now().strftime('%Y%m%d_%H%M%S')
    filename = f'carecall_backup_{ts}.zip'
    filepath = os.path.join(destination_dir, filename)

    single_files = [
        ('carecall.db',      os.path.join(_APP_ROOT, 'carecall.db')),
        ('carecall_jobs.db', os.path.join(_APP_ROOT, 'carecall_jobs.db')),
        ('.env',             os.path.join(_APP_ROOT, '.env')),
        ('carecall.service', os.path.join(_APP_ROOT, 'carecall.service')),
        ('backup_config.json', _BACKUP_CONFIG_PATH),
    ]
    uploads_dir = os.path.join(_APP_ROOT, 'uploads')

    with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for arc_name, src in single_files:
            if os.path.isfile(src):
                zf.write(src, arc_name)
        if os.path.isdir(uploads_dir):
            for dirpath, _dirs, filenames in os.walk(uploads_dir):
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    zf.write(full, os.path.relpath(full, _APP_ROOT))

    size = os.path.getsize(filepath)
    if size > 1_048_576:
        hr = f'{size/1_048_576:.1f} MB'
    elif size > 1024:
        hr = f'{size/1024:.1f} KB'
    else:
        hr = f'{size} bytes'
    return {'filename': filename, 'size': hr}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize_phone(raw):
    """Normalize a US phone number to E.164 (+1XXXXXXXXXX).

    Accepts any common format: (555) 123-4567, 555-123-4567,
    5551234567, 15551234567, +15551234567, etc.
    Returns the normalized E.164 string, or the original value if it
    can't be interpreted as a 10- or 11-digit US number.
    """
    if not raw:
        return raw
    digits = re.sub(r'\D', '', str(raw))
    if len(digits) == 10:
        return '+1' + digits
    if len(digits) == 11 and digits[0] == '1':
        return '+' + digits
    # Already non-US or unrecognised — return stripped but don't mangle
    return raw.strip()


def _parse_date(value):
    """Parse a YYYY-MM-DD string into a date object, or return None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
