import os
import re

from flask import Blueprint, jsonify, request, current_app
from carecall import db
from carecall.models import Client, EmergencyContact, Schedule, CallLog, WellnessSession

api_bp = Blueprint('api', __name__)


# ── Clients ────────────────────────────────────────────────────────────────────

@api_bp.route('/clients', methods=['GET'])
def get_clients():
    clients = Client.query.order_by(Client.name).all()
    return jsonify([c.to_dict() for c in clients])


@api_bp.route('/clients', methods=['POST'])
def create_client():
    data = request.get_json()
    if not data or not data.get('name') or not data.get('phone'):
        return jsonify({'error': 'name and phone are required'}), 400
    client = Client(name=data['name'], phone=data['phone'], notes=data.get('notes', ''))
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
    client.name = data.get('name', client.name)
    client.phone = data.get('phone', client.phone)
    client.notes = data.get('notes', client.notes)
    client.active = data.get('active', client.active)
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
        phone=data['phone'],
        relationship=data.get('relationship', ''),
        priority=data.get('priority', 1),
    )
    db.session.add(contact)
    db.session.commit()
    return jsonify(contact.to_dict()), 201


@api_bp.route('/contacts/<int:contact_id>', methods=['PUT'])
def update_contact(contact_id):
    contact = db.get_or_404(EmergencyContact, contact_id)
    data = request.get_json()
    contact.name = data.get('name', contact.name)
    contact.phone = data.get('phone', contact.phone)
    contact.relationship = data.get('relationship', contact.relationship)
    contact.priority = data.get('priority', contact.priority)
    db.session.commit()
    return jsonify(contact.to_dict())


@api_bp.route('/contacts/<int:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    contact = db.get_or_404(EmergencyContact, contact_id)
    db.session.delete(contact)
    db.session.commit()
    return '', 204


# ── Schedules ──────────────────────────────────────────────────────────────────

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
        max_attempts=data.get('max_attempts', 3),
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
    schedule.max_attempts = data.get('max_attempts', schedule.max_attempts)
    schedule.attempt_interval_minutes = data.get('attempt_interval_minutes', schedule.attempt_interval_minutes)
    schedule.active = data.get('active', schedule.active)
    db.session.commit()

    if schedule.active:
        activate_schedule(schedule)
    elif was_active:
        deactivate_schedule(schedule_id)

    return jsonify(schedule.to_dict())


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
    folder = current_app.config['UPLOAD_FOLDER']
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith('.mp3'))
    return jsonify(files)


@api_bp.route('/uploads', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    if not f.filename or not f.filename.lower().endswith('.mp3'):
        return jsonify({'error': 'Only .mp3 files are accepted'}), 400
    filename = re.sub(r'[^\w\-.]', '_', f.filename)
    f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
    return jsonify({'filename': filename}), 201


@api_bp.route('/uploads/<filename>', methods=['DELETE'])
def delete_upload(filename):
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    if os.path.isfile(path):
        os.remove(path)
    return '', 204


# ── Call logs & sessions ───────────────────────────────────────────────────────

@api_bp.route('/logs', methods=['GET'])
def get_logs():
    limit = request.args.get('limit', 100, type=int)
    logs = CallLog.query.order_by(CallLog.timestamp.desc()).limit(limit).all()
    return jsonify([l.to_dict() for l in logs])


@api_bp.route('/sessions', methods=['GET'])
def get_sessions():
    sessions = WellnessSession.query.order_by(WellnessSession.started_at.desc()).limit(50).all()
    return jsonify([s.to_dict() for s in sessions])


@api_bp.route('/dashboard', methods=['GET'])
def dashboard():
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    active_clients = Client.query.filter_by(active=True).count()
    active_schedules = Schedule.query.filter_by(active=True).count()
    calls_today = CallLog.query.filter(CallLog.timestamp >= today_start).count()
    active_sessions = WellnessSession.query.filter(
        WellnessSession.status.in_(['pending', 'calling', 'escalating'])
    ).count()
    recent_logs = CallLog.query.order_by(CallLog.timestamp.desc()).limit(20).all()
    return jsonify({
        'active_clients': active_clients,
        'active_schedules': active_schedules,
        'calls_today': calls_today,
        'active_sessions': active_sessions,
        'recent_logs': [l.to_dict() for l in recent_logs],
    })


# ── Settings & test ────────────────────────────────────────────────────────────

@api_bp.route('/settings', methods=['GET'])
def get_settings():
    from carecall.tunnel import _public_url
    return jsonify({
        'twilio_account_sid': os.getenv('TWILIO_ACCOUNT_SID', '(not set)'),
        'twilio_from_number': os.getenv('TWILIO_FROM_NUMBER', '(not set)'),
        'public_url': _public_url or os.getenv('PUBLIC_URL', '(auto-detecting)'),
    })


@api_bp.route('/test-call', methods=['POST'])
def test_call():
    from carecall.twilio_client import make_call
    from carecall.tunnel import get_public_url
    data = request.get_json()
    to = (data or {}).get('to', '').strip()
    if not to:
        return jsonify({'error': 'Phone number required (E.164 format, e.g. +15551234567)'}), 400
    try:
        base = get_public_url()
        sid = make_call(to, f"{base}/webhook/test", f"{base}/webhook/test")
        return jsonify({'success': True, 'call_sid': sid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
