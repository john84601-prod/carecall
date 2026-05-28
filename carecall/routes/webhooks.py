from datetime import datetime
from threading import Thread

from flask import Blueprint, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

from carecall import db
from carecall.models import CallLog, WellnessSession, EmergencyContact

webhooks_bp = Blueprint('webhooks', __name__)


def _xml(vr):
    return Response(str(vr), mimetype='text/xml')


def _public_url():
    from carecall.tunnel import get_public_url
    return get_public_url()


# ── Reminder calls ─────────────────────────────────────────────────────────────

@webhooks_bp.route('/reminder-answer', methods=['POST'])
def reminder_answer():
    from carecall.models import ReminderSession
    log_id     = request.args.get('log_id',     type=int)
    session_id = request.args.get('session_id', type=int)

    log     = db.session.get(CallLog,         log_id)     if log_id     else None
    session = db.session.get(ReminderSession, session_id) if session_id else None

    # AnsweredBy values: human | machine_start | machine_end_beep |
    #                    machine_end_silence | machine_end_other | fax | unknown
    answered_by = request.form.get('AnsweredBy', 'unknown')
    result = 'left_voicemail' if answered_by.startswith('machine') else 'reached_human'

    vr = VoiceResponse()

    if log:
        log.status = result
        if session and session.status == 'calling':
            session.status = result
            session.resolved_at = datetime.utcnow()

        schedule = log.schedule
        if schedule and schedule.mp3_filename:
            mp3_url = f"{_public_url()}/uploads/{schedule.mp3_filename}"
            vr.play(mp3_url)
        else:
            vr.say("This is your scheduled reminder. Have a great day.", voice='alice')

        db.session.commit()
    else:
        vr.say("This is your scheduled reminder. Have a great day.", voice='alice')

    return _xml(vr)


# ── Wellness calls ─────────────────────────────────────────────────────────────

@webhooks_bp.route('/wellness-answer', methods=['POST'])
def wellness_answer():
    session_id = request.args.get('session_id', type=int)
    log_id = request.args.get('log_id', type=int)

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log = db.session.get(CallLog, log_id) if log_id else None

    if log:
        log.status = 'answered'
        db.session.commit()

    vr = VoiceResponse()

    if session:
        client = session.client
        schedule = session.schedule
        key = schedule.required_keypress

        gather = Gather(
            num_digits=1,
            action=f"{_public_url()}/webhook/wellness-keypress?session_id={session_id}&log_id={log_id}",
            method='POST',
            timeout=15,
        )
        gather.say(
            f"Hello {client.first_name}, this is your wellness check call. "
            f"Please press {key} to confirm you are okay.",
            voice='alice',
        )
        vr.append(gather)
        vr.say("We did not receive your response. Goodbye.", voice='alice')
    else:
        vr.say("Wellness check call. Session not found. Goodbye.", voice='alice')

    return _xml(vr)


@webhooks_bp.route('/wellness-keypress', methods=['POST'])
def wellness_keypress():
    session_id = request.args.get('session_id', type=int)
    log_id = request.args.get('log_id', type=int)
    digits = request.form.get('Digits', '')

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log = db.session.get(CallLog, log_id) if log_id else None

    vr = VoiceResponse()

    if session and log:
        log.keypress_received = digits
        if digits == session.schedule.required_keypress:
            log.status = 'acknowledged'
            session.status = 'acknowledged'
            session.resolved_at = datetime.utcnow()
            vr.say("Thank you. We have recorded your check-in. Take care.", voice='alice')
        else:
            log.status = 'wrong-keypress'
            vr.say("That was not the expected response. Goodbye.", voice='alice')
        db.session.commit()
    else:
        vr.say("Session not found. Goodbye.", voice='alice')

    return _xml(vr)


# ── Emergency calls ────────────────────────────────────────────────────────────

@webhooks_bp.route('/emergency-answer', methods=['POST'])
def emergency_answer():
    session_id = request.args.get('session_id', type=int)
    contact_id = request.args.get('contact_id', type=int)
    log_id = request.args.get('log_id', type=int)

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log = db.session.get(CallLog, log_id) if log_id else None
    contact = db.session.get(EmergencyContact, contact_id) if contact_id else None

    if log:
        log.status = 'answered'
        db.session.commit()

    vr = VoiceResponse()

    if session and contact:
        client = session.client
        key = session.schedule.required_keypress

        gather = Gather(
            num_digits=1,
            action=(
                f"{_public_url()}/webhook/emergency-keypress"
                f"?session_id={session_id}&contact_id={contact_id}&log_id={log_id}"
            ),
            method='POST',
            timeout=20,
        )
        gather.say(
            f"This is an urgent wellness notification. {client.full_name} has not responded to "
            f"{session.current_attempt} wellness check calls. "
            f"As their emergency contact, please press {key} to confirm "
            "you will follow up with them immediately.",
            voice='alice',
        )
        vr.append(gather)
        vr.say("We did not receive your acknowledgment. Goodbye.", voice='alice')
    else:
        vr.say("Emergency wellness notification. Session not found. Goodbye.", voice='alice')

    return _xml(vr)


@webhooks_bp.route('/emergency-keypress', methods=['POST'])
def emergency_keypress():
    session_id = request.args.get('session_id', type=int)
    contact_id = request.args.get('contact_id', type=int)
    log_id = request.args.get('log_id', type=int)
    digits = request.form.get('Digits', '')

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log = db.session.get(CallLog, log_id) if log_id else None
    contact = db.session.get(EmergencyContact, contact_id) if contact_id else None

    vr = VoiceResponse()

    if session and log and contact:
        log.keypress_received = digits
        if digits == session.schedule.required_keypress:
            log.status = 'acknowledged'
            session.emergency_acknowledged = True
            session.status = 'escalated'
            session.resolved_at = datetime.utcnow()
            vr.say(
                f"Thank you {contact.name}. Your acknowledgment has been recorded. "
                "Please follow up with the client as soon as possible.",
                voice='alice',
            )
        else:
            log.status = 'wrong-keypress'
            vr.say("Incorrect key pressed. Goodbye.", voice='alice')
        db.session.commit()
    else:
        vr.say("Session not found. Goodbye.", voice='alice')

    return _xml(vr)


# ── Twilio call status callback ────────────────────────────────────────────────

@webhooks_bp.route('/call-status', methods=['POST'])
def call_status():
    """Twilio posts here when a call's final status is known."""
    from carecall.scheduler import handle_wellness_no_response, handle_emergency_no_response

    call_type = request.args.get('call_type')
    log_id = request.args.get('log_id', type=int)
    session_id = request.args.get('session_id', type=int)
    contact_id = request.args.get('contact_id', type=int)
    twilio_status = request.form.get('CallStatus', '')

    log = db.session.get(CallLog, log_id) if log_id else None
    if log and log.status in ('initiated', 'answered'):
        log.status = twilio_status
        db.session.commit()

    terminal = {'completed', 'no-answer', 'busy', 'failed'}
    if twilio_status not in terminal:
        return '', 204

    if call_type == 'reminder' and session_id:
        from carecall.models import ReminderSession
        from carecall.scheduler import handle_reminder_no_response
        r_session = db.session.get(ReminderSession, session_id)
        if r_session and r_session.status == 'calling':
            t = Thread(target=handle_reminder_no_response, args=(session_id,), daemon=True)
            t.start()

    elif call_type == 'wellness' and session_id:
        session = db.session.get(WellnessSession, session_id)
        if session and session.status not in ('acknowledged', 'escalating', 'escalated', 'failed'):
            t = Thread(target=handle_wellness_no_response, args=(session_id,), daemon=True)
            t.start()

    elif call_type == 'emergency' and session_id:
        session = db.session.get(WellnessSession, session_id)
        if session and not session.emergency_acknowledged:
            t = Thread(target=handle_emergency_no_response, args=(session_id, contact_id), daemon=True)
            t.start()

    return '', 204


# ── Test TwiML endpoint ────────────────────────────────────────────────────────

@webhooks_bp.route('/test', methods=['POST'])
def test_twiml():
    vr = VoiceResponse()
    vr.say("CareCall test call successful. Your configuration is working correctly.", voice='alice')
    return _xml(vr)
