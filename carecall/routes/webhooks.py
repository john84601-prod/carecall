import logging
import os
from datetime import datetime
from threading import Thread

from flask import Blueprint, request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.twiml.messaging_response import MessagingResponse

from carecall import db
from carecall.models import CallLog, WellnessSession, EmergencyContact
from carecall.twilio_client import normalize_phone

logger = logging.getLogger(__name__)

webhooks_bp = Blueprint('webhooks', __name__)


@webhooks_bp.before_request
def _validate_twilio_signature():
    """Reject any request that doesn't carry a valid Twilio signature.

    Twilio signs every callback with the account's Auth Token.
    ProxyFix in create_app() ensures request.url reflects the public
    ngrok URL that Twilio was given, so the HMAC check matches.
    """
    auth_token = os.getenv('TWILIO_AUTH_TOKEN', '')
    if not auth_token:
        logger.warning('TWILIO_AUTH_TOKEN not set — skipping webhook signature validation')
        return

    validator  = RequestValidator(auth_token)
    signature  = request.headers.get('X-Twilio-Signature', '')
    params     = request.form.to_dict() if request.method == 'POST' else {}

    if not validator.validate(request.url, params, signature):
        logger.warning(f'Invalid Twilio signature rejected: {request.url} from {request.remote_addr}')
        return Response('Forbidden', status=403)


def _xml(vr):
    return Response(str(vr), mimetype='text/xml')


def _public_url():
    from carecall.tunnel import get_public_url
    return get_public_url()


def _voice():
    """TTS voice — reads system_config.json, falls back to TWILIO_VOICE env var, then default."""
    try:
        from carecall.routes.api import _load_system_config
        v = _load_system_config().get('tts_voice')
        if v:
            return v
    except Exception:
        pass
    return os.getenv('TWILIO_VOICE', 'Polly.Joanna-Neural')


def _required_keypress():
    """Key the client must press to confirm wellness. Override with REQUIRED_KEYPRESS in .env."""
    return os.getenv('REQUIRED_KEYPRESS', '1')


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
            vr.say("This is your scheduled reminder. Have a great day.", voice=_voice())
        db.session.commit()
    else:
        vr.say("This is your scheduled reminder. Have a great day.", voice=_voice())

    return _xml(vr)


# ── Wellness calls ─────────────────────────────────────────────────────────────

@webhooks_bp.route('/wellness-answer', methods=['POST'])
def wellness_answer():
    """Fires immediately when the call connects (asyncAmd mode).

    The AMD result comes separately via /wellness-amd-result, so we don't
    know yet whether a human or machine answered.  We always respond with a
    Gather so the message starts playing right away — no 3-5 second silence.
    If the AMD callback determines it's voicemail, it redirects the live
    call to /wellness-voicemail before the Gather timeout expires.
    """
    session_id = request.args.get('session_id', type=int)
    log_id     = request.args.get('log_id',     type=int)

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log     = db.session.get(CallLog,         log_id)     if log_id     else None

    if log:
        log.status = 'answered'
        db.session.commit()

    vr = VoiceResponse()

    if session:
        client   = session.client
        schedule = session.schedule
        key      = _required_keypress()

        # Generous timeout — DetectMessageEnd can take up to ~30 s on some
        # carriers to confirm the beep before the AMD callback fires and
        # redirects the call away from this Gather.
        gather = Gather(
            num_digits=1,
            action=f"{_public_url()}/webhook/wellness-keypress"
                   f"?session_id={session_id}&log_id={log_id}",
            method='POST',
            timeout=30,
        )

        if schedule.mp3_filename:
            gather.play(f"{_public_url()}/uploads/{schedule.mp3_filename}")
        else:
            gather.say(
                f"Hello {client.first_name}, this is your wellness check call. "
                f"Please press {key} to confirm you are okay.",
                voice=_voice(),
            )

        vr.append(gather)
        vr.say("We did not receive your response. Goodbye.", voice=_voice())
    else:
        vr.say("Wellness check call. Session not found. Goodbye.", voice=_voice())

    return _xml(vr)


@webhooks_bp.route('/wellness-amd-result', methods=['POST'])
def wellness_amd_result():
    """Async AMD status callback — fires when Twilio determines human vs machine.

    For humans the Gather in wellness_answer already handles everything.
    For voicemail we redirect the live call to /wellness-voicemail so the
    message plays cleanly after the beep (DetectMessageEnd already waited).
    """
    import os as _os
    session_id  = request.args.get('session_id', type=int)
    log_id      = request.args.get('log_id',     type=int)
    call_sid    = request.form.get('CallSid', '')
    answered_by = request.form.get('AnsweredBy', 'human')
    is_machine  = answered_by.startswith('machine')

    logger.info(f"AMD result session={session_id} AnsweredBy={answered_by}")

    if is_machine and call_sid:
        # Interrupt the Gather and redirect to voicemail TwiML.
        try:
            from twilio.rest import Client as _TwilioClient
            tw = _TwilioClient(
                _os.getenv('TWILIO_ACCOUNT_SID'),
                _os.getenv('TWILIO_AUTH_TOKEN'),
            )
            tw.calls(call_sid).update(
                url=(f"{_public_url()}/webhook/wellness-voicemail"
                     f"?session_id={session_id}&log_id={log_id}"),
                method='POST',
            )
            logger.info(f"Redirected call {call_sid} to voicemail handler")
        except Exception as e:
            logger.error(f"Failed to redirect call {call_sid} to voicemail: {e}")

    return '', 204


@webhooks_bp.route('/wellness-voicemail', methods=['POST'])
def wellness_voicemail():
    """TwiML served after DetectMessageEnd confirms the beep.

    The call was redirected here by wellness_amd_result.  Play the message
    into the voicemail recording and hang up.
    """
    session_id = request.args.get('session_id', type=int)
    log_id     = request.args.get('log_id',     type=int)

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log     = db.session.get(CallLog,         log_id)     if log_id     else None

    if log:
        log.status = 'left_voicemail'
        db.session.commit()

    vr = VoiceResponse()

    if session:
        client   = session.client
        schedule = session.schedule
        key      = _required_keypress()

        if schedule.mp3_filename:
            vr.play(f"{_public_url()}/uploads/{schedule.mp3_filename}")
        else:
            vr.say(
                f"Hello {client.first_name}, this is a wellness check call. "
                f"We were unable to reach you. Please call back or press {key} "
                "when we try again to confirm you are okay.",
                voice=_voice(),
            )
    else:
        vr.say("Wellness check call. Session not found. Goodbye.", voice=_voice())

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
        if digits == _required_keypress():
            log.status = 'acknowledged'
            session.status = 'acknowledged'
            session.resolved_at = datetime.utcnow()
            vr.say("Thank you. We have recorded your check-in. Take care.", voice=_voice())
        else:
            log.status = 'wrong-keypress'
            vr.say("That was not the expected response. Goodbye.", voice=_voice())
        db.session.commit()
    else:
        vr.say("Session not found. Goodbye.", voice=_voice())

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

    # AnsweredBy: human | machine_start | machine_end_beep | machine_end_silence | …
    answered_by = request.form.get('AnsweredBy', 'human')
    is_machine = answered_by.startswith('machine')

    if log:
        log.status = 'left_voicemail' if is_machine else 'answered'
        db.session.commit()

    vr = VoiceResponse()

    if session and contact:
        client = session.client
        key = _required_keypress()

        if is_machine:
            # Voicemail — DetectMessageEnd already waited for the beep.
            # Speak clearly without a Gather (voicemail can't press keys).
            vr.say(
                f"Urgent message for {contact.name}. "
                f"{client.full_name} has not responded to "
                f"{session.current_attempt} wellness check calls. "
                "Please check on them as soon as possible.",
                voice=_voice(),
            )
        else:
            # Human answered — gather keypress confirmation.
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
                voice=_voice(),
            )
            vr.append(gather)
            vr.say("We did not receive your acknowledgment. Goodbye.", voice=_voice())
    else:
        vr.say("Emergency wellness notification. Session not found. Goodbye.", voice=_voice())

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
        if digits == _required_keypress():
            log.status = 'acknowledged'
            session.emergency_acknowledged = True
            session.acknowledged_by_contact_id = contact.id
            session.status = 'escalated'
            session.resolved_at = datetime.utcnow()
            vr.say(
                f"Thank you {contact.name}. Your acknowledgment has been recorded. "
                "Please follow up with the client as soon as possible.",
                voice=_voice(),
            )
        else:
            log.status = 'wrong-keypress'
            vr.say("Incorrect key pressed. Goodbye.", voice=_voice())
        db.session.commit()
    else:
        vr.say("Session not found. Goodbye.", voice=_voice())

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
        if session and session.status not in ('acknowledged', 'escalating', 'escalated', 'failed', 'cancelled'):
            t = Thread(target=handle_wellness_no_response, args=(session_id,), daemon=True)
            t.start()

    elif call_type == 'emergency' and session_id:
        session = db.session.get(WellnessSession, session_id)
        if session and not session.emergency_acknowledged and session.status != 'cancelled':
            t = Thread(target=handle_emergency_no_response, args=(session_id, contact_id), daemon=True)
            t.start()

    return '', 204


# ── Inbound SMS reply (emergency contact acknowledgment) ───────────────────────

_SMS_ACK_KEYWORDS = {'ok', 'yes', '1', 'ack', 'acknowledge', 'confirmed', 'confirm'}

@webhooks_bp.route('/sms-reply', methods=['POST'])
def sms_reply():
    """Twilio posts here when an emergency contact replies to an alert SMS.

    Matches the sender's phone number to an emergency contact, finds any open
    WellnessSession where that contact has been called, and acknowledges it if
    the reply body is a recognised acknowledgment keyword.
    """
    from_raw = request.form.get('From', '')
    body     = request.form.get('Body', '').strip().lower()
    from_norm = normalize_phone(from_raw)

    mr = MessagingResponse()

    # Find all emergency contacts whose normalized phone matches the sender
    all_contacts = EmergencyContact.query.all()
    matching = [c for c in all_contacts if normalize_phone(c.phone) == from_norm]

    if not matching:
        logger.info(f"SMS reply from unknown number {from_raw} — no matching emergency contact")
        mr.message("CareCall: Your number is not registered as an emergency contact. No action taken.")
        return Response(str(mr), mimetype='text/xml')

    # Find an open WellnessSession where one of these contacts has been called
    open_sessions = WellnessSession.query.filter(
        WellnessSession.status.in_(['escalating'])
    ).all()

    target_session = None
    target_contact = None
    for contact in matching:
        for sess in open_sessions:
            if contact.id in sess.get_contacts_called():
                target_session = sess
                target_contact = contact
                break
        if target_session:
            break

    if not target_session:
        # Check if a recently-resolved session exists (acknowledged by call in the meantime)
        logger.info(f"SMS reply from {from_raw} — no open session found for this contact")
        mr.message("CareCall: No active wellness alert found for your number. It may have already been resolved.")
        return Response(str(mr), mimetype='text/xml')

    # Check for acknowledgment keyword
    if not any(kw in body for kw in _SMS_ACK_KEYWORDS):
        client_name = target_session.client.full_name if target_session.client else 'the client'
        mr.message(
            f"CareCall: Active alert for {client_name}. "
            f"Reply OK to confirm you will follow up immediately."
        )
        return Response(str(mr), mimetype='text/xml')

    # Acknowledge the session
    now = datetime.utcnow()
    target_session.emergency_acknowledged = True
    target_session.acknowledged_by_contact_id = target_contact.id
    target_session.status    = 'escalated'
    target_session.resolved_at = now

    # Log the SMS acknowledgment
    log = CallLog(
        schedule_id         = target_session.schedule_id,
        client_id           = target_session.client_id,
        wellness_session_id = target_session.id,
        emergency_contact_id= target_contact.id,
        call_type           = 'emergency',
        attempt_number      = 0,
        status              = 'acknowledged',
        timestamp           = now,
        notes               = f'Acknowledged via SMS reply from {from_raw}',
    )
    db.session.add(log)
    db.session.commit()

    client_name = target_session.client.full_name if target_session.client else 'the client'
    logger.info(
        f"Session {target_session.id} acknowledged via SMS by "
        f"{target_contact.name} ({from_raw})"
    )
    mr.message(
        f"CareCall: Thank you {target_contact.name}. Your acknowledgment for "
        f"{client_name} has been recorded. Please follow up immediately."
    )
    return Response(str(mr), mimetype='text/xml')


# ── Inbound calls (voicemail) ──────────────────────────────────────────────────

@webhooks_bp.route('/inbound-call', methods=['POST'])
def inbound_call():
    """TwiML for anyone who calls the CareCall Twilio number directly."""
    from flask import current_app
    from carecall.routes.api import _load_system_config, _INBOUND_GREETING_FILE

    cfg             = _load_system_config()
    greeting_type   = cfg.get('inbound_greeting_type', 'script')
    greeting_script = cfg.get('inbound_greeting_script',
                               "You have reached CareCall. "
                               "Please leave a message after the tone and we will follow up with you. "
                               "Press the pound key when finished.")

    vr = VoiceResponse()

    if greeting_type == 'recording':
        upload_folder = current_app.config['UPLOAD_FOLDER']
        greeting_path = os.path.join(upload_folder, _INBOUND_GREETING_FILE)
        if os.path.isfile(greeting_path):
            vr.play(f"{_public_url()}/uploads/{_INBOUND_GREETING_FILE}")
        else:
            vr.say(greeting_script, voice=_voice())
    else:
        vr.say(greeting_script, voice=_voice())

    vr.record(
        max_length=120,
        play_beep=True,
        finish_on_key='#',
        action=f"{_public_url()}/webhook/inbound-recording",
        method='POST',
        timeout=5,
    )
    vr.say("We did not receive a recording. Goodbye.", voice=_voice())
    return _xml(vr)


@webhooks_bp.route('/inbound-recording', methods=['POST'])
def inbound_recording():
    """Twilio posts here when the inbound recording is ready."""
    from carecall.models import InboundMessage, Client
    from datetime import datetime as _dt

    recording_sid = request.form.get('RecordingSid', '')
    duration      = request.form.get('RecordingDuration', '0')
    call_sid      = request.form.get('CallSid', '')
    from_number   = request.form.get('From', '')

    try:
        duration_int = int(duration)
    except ValueError:
        duration_int = 0

    # Skip zero-length recordings (caller hung up immediately)
    if duration_int > 0 and recording_sid:
        norm = normalize_phone(from_number)
        matched = next(
            (c for c in Client.query.all() if normalize_phone(c.phone) == norm),
            None,
        )
        msg = InboundMessage(
            call_sid=call_sid,
            recording_sid=recording_sid,
            from_number=from_number,
            duration_seconds=duration_int,
            received_at=_dt.utcnow(),
            matched_client_id=matched.id if matched else None,
        )
        db.session.add(msg)
        db.session.commit()
        logger.info(f"Inbound voicemail saved: {recording_sid} from {from_number} ({duration_int}s)")
    else:
        logger.info(f"Inbound call from {from_number} — no recording (duration={duration_int}s)")

    vr = VoiceResponse()
    vr.say("Thank you for your message. Goodbye.", voice=_voice())
    return _xml(vr)


# ── Test TwiML endpoint ────────────────────────────────────────────────────────

@webhooks_bp.route('/test', methods=['POST'])
def test_twiml():
    vr = VoiceResponse()
    vr.say("CareCall test call successful. Your configuration is working correctly.", voice=_voice())
    return _xml(vr)
