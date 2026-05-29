import os
import re
from datetime import date

from flask import Blueprint, jsonify, request, current_app
from carecall import db
from carecall.models import Client, EmergencyContact, Schedule, ScheduleContact, AudioFile, CallLog, WellnessSession, ReminderSession

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
    from datetime import datetime, date as _date
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
        ReminderSession.status.in_(['reached_human', 'left_voicemail']),
    ).count()
    reminder_calls_today = CallLog.query.filter(
        CallLog.timestamp >= today_start,
        CallLog.call_type == 'reminder',
    ).count()

    # Wellness stats
    active_wellness_schedules = Schedule.query.filter_by(active=True, call_type='wellness').count()
    wellness_completed_today  = WellnessSession.query.filter(
        WellnessSession.started_at >= today_start,
        WellnessSession.status.in_(['acknowledged', 'escalated']),
    ).count()
    wellness_calls_today = CallLog.query.filter(
        CallLog.timestamp >= today_start,
        CallLog.call_type == 'wellness',
    ).count()

    # Alert cycles today = distinct wellness sessions where an emergency call was made today
    alert_cycles_today = db.session.query(
        func.count(sa_distinct(CallLog.wellness_session_id))
    ).filter(
        CallLog.call_type == 'emergency',
        CallLog.timestamp >= today_start,
    ).scalar() or 0

    # Active/in-progress sessions
    active_sessions = WellnessSession.query.filter(
        WellnessSession.status.in_(['pending', 'calling', 'escalating'])
    ).count()
    active_reminder_sessions_qs = ReminderSession.query.filter(
        ReminderSession.status.in_(['pending', 'calling'])
    ).order_by(ReminderSession.started_at.desc()).all()

    recent_logs     = CallLog.query.filter(
        CallLog.timestamp >= today_start
    ).order_by(CallLog.timestamp.desc()).all()
    recent_sessions = WellnessSession.query.filter(
        WellnessSession.started_at >= today_start
    ).order_by(WellnessSession.started_at.desc()).all()

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
        'recent_logs':     [l.to_dict() for l in recent_logs],
        'recent_sessions': [s.to_dict() for s in recent_sessions],
    })


# ── Settings & test ────────────────────────────────────────────────────────────

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
