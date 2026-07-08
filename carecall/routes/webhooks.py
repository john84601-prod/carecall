import base64
import logging
import os
from datetime import datetime
from threading import Thread

from flask import Blueprint, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.twiml.messaging_response import MessagingResponse

from carecall import db
from carecall.models import CallLog, WellnessSession, EmergencyContact
from carecall.voice_client import normalize_phone, validate_webhook_signature

logger = logging.getLogger(__name__)

webhooks_bp = Blueprint('webhooks', __name__)


@webhooks_bp.before_request
def _validate_provider_signature():
    """Reject any request that doesn't carry a valid signature from the
    configured voice provider.

    ProxyFix in create_app() ensures request.url reflects the public
    ngrok URL that the provider was given, so the HMAC check matches.
    """
    if not validate_webhook_signature(request):
        logger.warning(f'Invalid webhook signature rejected: {request.url} from {request.remote_addr}')
        return Response('Forbidden', status=403)


def _xml(vr):
    return Response(str(vr), mimetype='text/xml')


def _public_url():
    from carecall.tunnel import get_public_url
    return get_public_url()


def _voice():
    """TTS voice — reads system_config.json, falls back to TWILIO_VOICE env var, then a
    provider-appropriate default. Polly.*-Neural voices are Twilio/AWS-Polly-specific and
    aren't recognized by SignalWire's <Say>, so SignalWire gets a plain default instead.

    Telnyx is stricter still — its <speak> only accepts literally "female" or
    "male" for en-US — so any saved system_config voice (which may be a
    Twilio Polly name or SignalWire's "woman") is ignored for Telnyx rather
    than passed through and rejected by their API.
    """
    from carecall.voice_client import get_provider_name
    if get_provider_name() == 'telnyx':
        # Telnyx accepts Polly.*-Neural and Azure.* names (same format as
        # Twilio's Polly voices) as well as generic "female"/"male". Only the
        # SignalWire-classic names ("woman"/"man"/"alice") are incompatible.
        _sw_only = {'woman', 'man', 'alice'}
        try:
            from carecall.routes.api import _load_system_config
            v = _load_system_config().get('tts_voice', '')
            if v and v not in _sw_only:
                return v
        except Exception:
            pass
        return os.getenv('TELNYX_VOICE', 'female')
    try:
        from carecall.routes.api import _load_system_config
        v = _load_system_config().get('tts_voice')
        if v:
            return v
    except Exception:
        pass
    if get_provider_name() == 'signalwire':
        return os.getenv('SIGNALWIRE_VOICE', 'woman')
    return os.getenv('TWILIO_VOICE', 'Polly.Joanna-Neural')


def _required_keypress():
    """Key the client must press to confirm wellness. Override with REQUIRED_KEYPRESS in .env."""
    return os.getenv('REQUIRED_KEYPRESS', '1')


# ── Global system prompts (editable text or custom recording, see prompts.py) ──
#
# Every TTS line below is routed through one of these two helpers instead of
# being hardcoded, so it can be overridden globally from the dashboard
# (Settings → Messages) without touching code. A schedule's own per-client
# mp3 (schedule.mp3_filename) still takes priority over these when set —
# these are the fallback used whenever no client-specific recording exists.

def _twiml_prompt(target, key, **kwargs):
    """Say or play system prompt `key` onto a VoiceResponse or Gather object
    (both expose .say()/.play(), so either can be passed as `target`)."""
    from carecall.prompts import get_prompt_recording_path, get_prompt_text
    rec = get_prompt_recording_path(key)
    if rec:
        target.play(f"{_public_url()}/uploads/{os.path.basename(rec)}")
    else:
        target.say(get_prompt_text(key, **kwargs), voice=_voice())


def _telnyx_prompt_speak(ccid, key, **kwargs):
    from carecall.prompts import get_prompt_recording_path, get_prompt_text
    rec = get_prompt_recording_path(key)
    if rec:
        _telnyx_safe_command(ccid, 'playback_start', {
            'audio_url': f"{_public_url()}/uploads/{os.path.basename(rec)}",
            'client_state': _CLOSING_STATE,
        })
    else:
        _telnyx_safe_command(ccid, 'speak', {
            'payload': get_prompt_text(key, **kwargs), 'voice': _voice(), 'language': 'en-US',
            'client_state': _CLOSING_STATE,
        })


def _telnyx_prompt_gather(ccid, key, gather_params, **kwargs):
    from carecall.prompts import get_prompt_recording_path, get_prompt_text
    rec = get_prompt_recording_path(key)
    gp = dict(gather_params)
    if rec:
        gp['audio_url'] = f"{_public_url()}/uploads/{os.path.basename(rec)}"
        _telnyx_safe_command(ccid, 'gather_using_audio', gp)
    else:
        gp.update(payload=get_prompt_text(key, **kwargs), voice=_voice(), language='en-US')
        _telnyx_safe_command(ccid, 'gather_using_speak', gp)


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
    is_machine  = answered_by.startswith('machine')
    result = 'left_voicemail' if is_machine else 'reached_human'

    vr = VoiceResponse()

    if log:
        log.status = result
        if session and session.status == 'calling':
            session.status = result
            session.resolved_at = datetime.utcnow()

        schedule = log.schedule

        if is_machine:
            # Voicemail — play message without a Gather (can't press keys).
            if schedule and schedule.mp3_filename:
                vr.play(f"{_public_url()}/uploads/{schedule.mp3_filename}")
            else:
                _twiml_prompt(vr, 'reminder_message')
            vr.hangup()
        else:
            # Human answered — wrap in Gather so any key press is captured as
            # a positive acknowledgment (failsafe for noisy lines / TTS issues).
            gather = Gather(
                num_digits=1,
                action=f"{_public_url()}/webhook/reminder-keypress"
                       f"?log_id={log_id}&session_id={session_id}",
                method='POST',
                timeout=10,
            )
            if schedule and schedule.mp3_filename:
                gather.play(f"{_public_url()}/uploads/{schedule.mp3_filename}")
            else:
                _twiml_prompt(gather, 'reminder_message')
            vr.append(gather)
            # Fallback if the Gather times out without hitting its action URL
            # (observed on SignalWire — it doesn't always POST to action on
            # timeout the way Twilio does).
            _twiml_prompt(vr, 'reminder_unsuccessful_closing')
            vr.hangup()

        db.session.commit()
    else:
        _twiml_prompt(vr, 'reminder_message')
        vr.hangup()

    return _xml(vr)


@webhooks_bp.route('/reminder-keypress', methods=['POST'])
def reminder_keypress():
    from carecall.models import ReminderSession
    log_id     = request.args.get('log_id',     type=int)
    session_id = request.args.get('session_id', type=int)
    digits     = request.form.get('Digits', '')

    log     = db.session.get(CallLog,         log_id)     if log_id     else None
    session = db.session.get(ReminderSession, session_id) if session_id else None

    vr = VoiceResponse()

    if log:
        log.keypress_received = digits
        if digits == _required_keypress():
            log.status = 'acknowledged'
            if session:
                session.status = 'acknowledged'
                session.resolved_at = datetime.utcnow()
        db.session.commit()

    if digits == _required_keypress():
        _twiml_prompt(vr, 'success_goodbye')
    else:
        _twiml_prompt(vr, 'reminder_unsuccessful_closing')
    # SignalWire's compat API has been observed cutting the call as soon as a
    # <Say>/<Play> is queued rather than waiting for it to finish rendering —
    # a short pause before Hangup gives the closing message room to actually
    # play out instead of getting clipped.
    vr.pause(length=1)
    vr.hangup()
    return _xml(vr)


# ── Wellness calls ─────────────────────────────────────────────────────────────

def _wellness_voicemail_voiceresponse(session):
    """Shared voicemail TwiML for a wellness call, used both when AMD result
    arrives synchronously on the answer webhook (SignalWire) and when it
    arrives later via the async redirect (Twilio)."""
    vr = VoiceResponse()
    if session:
        client   = session.client
        schedule = session.schedule
        key      = _required_keypress()

        if schedule.mp3_filename:
            vr.play(f"{_public_url()}/uploads/{schedule.mp3_filename}")
        else:
            _twiml_prompt(vr, 'wellness_voicemail_message', first_name=client.first_name, key=key)
    else:
        _twiml_prompt(vr, 'session_not_found_closing')
    vr.hangup()
    return vr


@webhooks_bp.route('/wellness-answer', methods=['POST'])
def wellness_answer():
    """Fires when the call connects.

    On Twilio (asyncAmd mode) this fires immediately, before AMD has a
    result — AnsweredBy is absent here, and the result comes later via
    /wellness-amd-result, which redirects the live call to
    /wellness-voicemail if it's a machine.

    On SignalWire (no async AMD support) AMD already ran synchronously by
    the time this fires, so AnsweredBy is already present — handle the
    voicemail case directly here instead of waiting for a callback that
    will never come.
    """
    session_id = request.args.get('session_id', type=int)
    log_id     = request.args.get('log_id',     type=int)

    session = db.session.get(WellnessSession, session_id) if session_id else None
    log     = db.session.get(CallLog,         log_id)     if log_id     else None

    answered_by = request.form.get('AnsweredBy', '')
    is_machine  = answered_by.startswith('machine')

    if answered_by and is_machine:
        # AMD already resolved synchronously — go straight to voicemail.
        if log:
            log.status = 'left_voicemail'
            db.session.commit()
        return _xml(_wellness_voicemail_voiceresponse(session))

    if log:
        log.status = 'answered'
        db.session.commit()

    vr = VoiceResponse()

    if session:
        client   = session.client
        schedule = session.schedule
        key      = _required_keypress()

        gather = Gather(
            num_digits=1,
            action=f"{_public_url()}/webhook/wellness-keypress"
                   f"?session_id={session_id}&log_id={log_id}",
            method='POST',
            timeout=10,
        )

        if schedule.mp3_filename:
            gather.play(f"{_public_url()}/uploads/{schedule.mp3_filename}")
        else:
            _twiml_prompt(gather, 'wellness_message', first_name=client.first_name, key=key)

        vr.append(gather)
        _twiml_prompt(vr, 'wellness_unsuccessful_closing')
        vr.hangup()
    else:
        _twiml_prompt(vr, 'session_not_found_closing')
        vr.hangup()

    return _xml(vr)


@webhooks_bp.route('/wellness-amd-result', methods=['POST'])
def wellness_amd_result():
    """Async AMD status callback — fires when Twilio determines human vs machine.

    For humans the Gather in wellness_answer already handles everything.
    For voicemail we redirect the live call to /wellness-voicemail so the
    message plays cleanly after the beep (DetectMessageEnd already waited).
    """
    session_id  = request.args.get('session_id', type=int)
    log_id      = request.args.get('log_id',     type=int)
    call_sid    = request.form.get('CallSid', '')
    answered_by = request.form.get('AnsweredBy', 'human')
    is_machine  = answered_by.startswith('machine')

    logger.info(f"AMD result session={session_id} AnsweredBy={answered_by}")

    if is_machine and call_sid:
        # Interrupt the Gather and redirect to voicemail TwiML.
        try:
            from carecall.voice_client import get_client
            tw = get_client()
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

    return _xml(_wellness_voicemail_voiceresponse(session))


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
            _twiml_prompt(vr, 'success_goodbye')
        else:
            log.status = 'wrong-keypress'
            _twiml_prompt(vr, 'wellness_unsuccessful_closing')
        db.session.commit()
    else:
        _twiml_prompt(vr, 'session_not_found_closing')

    # See the matching comment in reminder_keypress() above — gives the
    # closing message room to finish playing before SignalWire hangs up.
    vr.pause(length=1)
    vr.hangup()
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
            _twiml_prompt(vr, 'emergency_voicemail_message',
                          contact_name=contact.name, client_name=client.full_name,
                          attempt=session.current_attempt)
            vr.hangup()
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
            _twiml_prompt(gather, 'emergency_message',
                          client_name=client.full_name, attempt=session.current_attempt, key=key)
            vr.append(gather)
            _twiml_prompt(vr, 'emergency_unsuccessful_closing')
            vr.hangup()
    else:
        _twiml_prompt(vr, 'session_not_found_closing')
        vr.hangup()

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
            _twiml_prompt(vr, 'emergency_ack_instruction', contact_name=contact.name)
        else:
            log.status = 'wrong-keypress'
            _twiml_prompt(vr, 'emergency_unsuccessful_closing')
        db.session.commit()
    else:
        _twiml_prompt(vr, 'session_not_found_closing')

    vr.hangup()
    return _xml(vr)


# ── Recording status callback ─────────────────────────────────────────────────

@webhooks_bp.route('/call-recording', methods=['POST'])
def call_recording():
    """Twilio posts here when a call recording is ready.

    Matches the log via log_id query param (set when the call was placed) and
    stores the RecordingSid + duration on the CallLog for later playback.
    """
    log_id            = request.args.get('log_id', type=int)
    recording_sid     = request.form.get('RecordingSid', '')
    recording_status  = request.form.get('RecordingStatus', '')
    recording_duration = request.form.get('RecordingDuration', '')

    if recording_status != 'completed' or not recording_sid:
        return '', 204

    log = db.session.get(CallLog, log_id) if log_id else None
    if log:
        log.recording_sid = recording_sid
        try:
            log.recording_duration = int(recording_duration)
        except (ValueError, TypeError):
            pass
        db.session.commit()
        logger.info(f"Recording {recording_sid} stored for log {log_id}")
    else:
        logger.warning(f"call-recording: no CallLog found for log_id={log_id}")

    return '', 204


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
    _twiml_prompt(vr, 'inbound_no_recording_closing')
    vr.hangup()
    return _xml(vr)


@webhooks_bp.route('/inbound-recording', methods=['POST'])
def inbound_recording():
    """Twilio posts here when the inbound recording is ready."""
    from carecall.models import InboundMessage, Client
    from datetime import datetime as _dt

    recording_sid = request.form.get('RecordingSid', '')
    recording_url = request.form.get('RecordingUrl', '')
    duration      = request.form.get('RecordingDuration', '0')
    call_sid      = request.form.get('CallSid', '')
    from_number   = request.form.get('From', '')

    # SignalWire's compatibility Record verb doesn't send RecordingSid — only
    # a direct RecordingUrl to the recording file. Derive an identifier from
    # the URL so we still have something to key on, and keep the full URL so
    # playback can fetch it directly instead of reconstructing a Twilio-style
    # REST lookup that doesn't exist for SignalWire inbound recordings.
    if not recording_sid and recording_url:
        recording_sid = recording_url.rsplit('/', 1)[-1].rsplit('.', 1)[0]

    try:
        duration_int = int(duration)
    except ValueError:
        duration_int = 0

    # Skip zero-length recordings (caller hung up immediately)
    if duration_int > 0 and (recording_sid or recording_url):
        norm = normalize_phone(from_number)
        matched = next(
            (c for c in Client.query.all() if normalize_phone(c.phone) == norm),
            None,
        )
        msg = InboundMessage(
            call_sid=call_sid,
            recording_sid=recording_sid,
            recording_url=recording_url,
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
    _twiml_prompt(vr, 'inbound_thanks_closing')
    vr.hangup()
    return _xml(vr)


# ── Telnyx Call Control event dispatcher ───────────────────────────────────────
#
# Telnyx delivers every event for a call (answered, AMD result, DTMF gather
# result, hangup, ...) as JSON POSTs to one webhook_url, set per-call in
# voice_client._telnyx_make_call(). That function rewrites whatever
# answer_url path scheduler.py built (e.g. /webhook/wellness-answer) into
# this single /telnyx route with call_type=wellness|reminder|emergency added
# to the query string, so the same session_id/log_id/contact_id params used
# by the TwiML routes above are preserved here.

# client_state Telnyx echoes back on the resulting call.gather.ended webhook
# when we cancel a gather ourselves (see call.machine.premium.detection.ended
# below) — lets us tell "we stopped this for an AMD redirect" apart from a
# real gather timeout/keypress. Telnyx requires this field to be base64.
_AMD_REDIRECT_STATE = base64.b64encode(b'amd_redirect').decode()

# client_state tag on the final closing speak/playback (voicemail message,
# post-keypress "thank you"/"unsuccessful" line, test-call message). When a
# keypress interrupts an in-progress gather_using_audio/gather_using_speak,
# Telnyx fires a call.playback.ended/call.speak.ended for THAT interrupted
# message too — untagged, so it doesn't match this and won't trigger a hangup
# before the real closing message (tagged, below) has had a chance to play.
_CLOSING_STATE = base64.b64encode(b'closing_message').decode()


def _telnyx_safe_command(ccid, action, payload=None):
    """telnyx_command(), but tolerant of the call having already ended
    (e.g. the person hung up before our gather/AMD-triggered response was
    ready) — that's a normal race, not a bug worth a 500 + Telnyx retry."""
    from carecall.voice_client import telnyx_command
    try:
        return telnyx_command(ccid, action, payload)
    except Exception as e:
        logger.warning(f"Telnyx command {action!r} on {ccid} failed (call likely already ended): {e}")
        return None


@webhooks_bp.route('/telnyx', methods=['POST'])
def telnyx_events():
    body       = request.get_json(silent=True) or {}
    data       = body.get('data', {})
    event_type = data.get('event_type', '')
    p          = data.get('payload', {})
    ccid       = p.get('call_control_id', '')

    call_type  = request.args.get('call_type', '')
    session_id = request.args.get('session_id', type=int)
    log_id     = request.args.get('log_id',     type=int)
    contact_id = request.args.get('contact_id', type=int)

    # Temporary diagnostic logging — trying to catch the exact event sequence
    # around a mid-message keypress hanging up early. Safe to remove once
    # that's root-caused.
    logger.info(
        f"Telnyx event={event_type!r} call_type={call_type!r} "
        f"session_id={session_id} ccid={ccid} "
        f"digits={p.get('digits')!r} result={p.get('result')!r} "
        f"client_state={p.get('client_state')!r}"
    )

    log = db.session.get(CallLog, log_id) if log_id else None

    if not call_type:
        # No call_type query param means this isn't a call we placed
        # ourselves (those always carry one via _telnyx_make_call's webhook
        # URL rewrite) — it's an inbound call to the Telnyx number, which has
        # no query string at all on its webhook_url. Handle entirely
        # separately from the outbound reminder/wellness/emergency/test flows
        # above, since the event sequence (answer → greet → record) differs.
        return _telnyx_inbound_events(event_type, ccid, p)

    if event_type == 'call.initiated':
        return '', 200

    if event_type == 'call.answered':
        if call_type in ('reminder', 'wellness', 'emergency'):
            # True async AMD: start the keypress gather immediately, assuming
            # a human, rather than waiting for the AMD result — this is what
            # eliminates the multi-second silence a human caller would
            # otherwise hear. If AMD later reports a machine, we interrupt
            # this gather (see call.machine.premium.detection.ended below).
            _telnyx_speak_gather(call_type, ccid, session_id, log, contact_id)
            return '', 200
        # No AMD requested for this call (e.g. /test-call) — nothing else will
        # fire to prompt a response, so speak immediately.
        _telnyx_prompt_speak(ccid, 'test_call_message')
        return '', 200

    if event_type in ('call.machine.premium.detection.ended', 'call.machine.detection.ended'):
        # Fires once Telnyx decides human vs. machine. For "human" the gather
        # started on call.answered above is already running — nothing to do.
        # For "machine", that gather is mid-flight talking over the answering
        # machine's own greeting, so stop it and wait for
        # call.machine.premium.greeting.ended (the actual beep) before
        # speaking the voicemail message.
        result = p.get('result', 'human')
        if result in ('machine', 'fax'):
            _telnyx_safe_command(ccid, 'gather_stop', {'client_state': _AMD_REDIRECT_STATE})
        return '', 200

    if event_type == 'call.machine.premium.greeting.ended':
        # Fires once the answering machine's greeting has actually finished
        # (result=beep_detected or prompt_ended) — NOW it's safe to speak the
        # voicemail message without it overlapping the greeting.
        _telnyx_speak_voicemail(call_type, ccid, session_id, log, contact_id)
        return '', 200

    if event_type == 'call.gather.ended':
        if p.get('client_state') == _AMD_REDIRECT_STATE:
            # This is the gather we deliberately cancelled above because AMD
            # detected a machine — not a real (non-)response from a human.
            # The voicemail message gets spoken separately, once the
            # greeting/beep event arrives.
            return '', 200
        digits = p.get('digits', '') or ''
        _telnyx_keypress(call_type, ccid, session_id, log, contact_id, digits)
        return '', 200

    if event_type in ('call.speak.ended', 'call.playback.ended'):
        if p.get('client_state') == _CLOSING_STATE:
            # Only the closing message we deliberately spoke/played should
            # end the call. A gather_using_audio/gather_using_speak message
            # interrupted by a keypress also fires one of these events (for
            # the original, untagged message) — ignoring it here stops that
            # from hanging up the call before the real closing message
            # (tagged, sent from _telnyx_keypress/_telnyx_speak_voicemail)
            # has had a chance to play.
            _telnyx_safe_command(ccid, 'hangup')
        return '', 200

    if event_type == 'call.hangup':
        _telnyx_handle_hangup(call_type, session_id, log, contact_id, p)
        return '', 200

    return '', 200


def _telnyx_speak_voicemail(call_type, ccid, session_id, log, contact_id):
    if call_type == 'reminder':
        from carecall.models import ReminderSession
        session = db.session.get(ReminderSession, session_id) if session_id else None
        if log:
            log.status = 'left_voicemail'
            if session and session.status == 'calling':
                session.status = 'left_voicemail'
                session.resolved_at = datetime.utcnow()
            db.session.commit()
        schedule = log.schedule if log else None
        if schedule and schedule.mp3_filename:
            _telnyx_safe_command(ccid, 'playback_start', {
                'audio_url': f"{_public_url()}/uploads/{schedule.mp3_filename}",
                'client_state': _CLOSING_STATE,
            })
        else:
            _telnyx_prompt_speak(ccid, 'reminder_message')
        return

    if call_type == 'wellness':
        session = db.session.get(WellnessSession, session_id) if session_id else None
        if log:
            log.status = 'left_voicemail'
            db.session.commit()
        if session:
            client, schedule, key = session.client, session.schedule, _required_keypress()
            if schedule.mp3_filename:
                _telnyx_safe_command(ccid, 'playback_start', {
                    'audio_url': f"{_public_url()}/uploads/{schedule.mp3_filename}",
                    'client_state': _CLOSING_STATE,
                })
            else:
                _telnyx_prompt_speak(ccid, 'wellness_voicemail_message', first_name=client.first_name, key=key)
        else:
            _telnyx_prompt_speak(ccid, 'session_not_found_closing')
        return

    if call_type == 'emergency':
        session = db.session.get(WellnessSession, session_id) if session_id else None
        contact = db.session.get(EmergencyContact, contact_id) if contact_id else None
        if log:
            log.status = 'left_voicemail'
            db.session.commit()
        if session and contact:
            _telnyx_prompt_speak(ccid, 'emergency_voicemail_message',
                                  contact_name=contact.name, client_name=session.client.full_name,
                                  attempt=session.current_attempt)
        else:
            _telnyx_prompt_speak(ccid, 'session_not_found_closing')
        return


def _telnyx_speak_gather(call_type, ccid, session_id, log, contact_id):
    if call_type == 'reminder':
        from carecall.models import ReminderSession
        session = db.session.get(ReminderSession, session_id) if session_id else None
        if log:
            log.status = 'reached_human'
            # A reminder is considered resolved the moment a human answers
            # (the keypress below only upgrades it to 'acknowledged') —
            # matching reminder_answer()'s TwiML behavior. Without this,
            # handle_reminder_no_response() would see session.status still
            # 'calling' and wrongly schedule a retry call.
            if session and session.status == 'calling':
                session.status = 'reached_human'
                session.resolved_at = datetime.utcnow()
            db.session.commit()
        schedule = log.schedule if log else None
        gp = {'minimum_digits': 1, 'maximum_digits': 1, 'timeout_millis': 10000, 'valid_digits': '0123456789'}
        if schedule and schedule.mp3_filename:
            gp['audio_url'] = f"{_public_url()}/uploads/{schedule.mp3_filename}"
            _telnyx_safe_command(ccid, 'gather_using_audio', gp)
        else:
            _telnyx_prompt_gather(ccid, 'reminder_message', gp)
        return

    if call_type == 'wellness':
        session = db.session.get(WellnessSession, session_id) if session_id else None
        if log:
            log.status = 'answered'
            db.session.commit()
        if not session:
            _telnyx_prompt_speak(ccid, 'session_not_found_closing')
            return
        client, schedule, key = session.client, session.schedule, _required_keypress()
        gp = {'minimum_digits': 1, 'maximum_digits': 1, 'timeout_millis': 10000, 'valid_digits': '0123456789'}
        if schedule.mp3_filename:
            gp['audio_url'] = f"{_public_url()}/uploads/{schedule.mp3_filename}"
            _telnyx_safe_command(ccid, 'gather_using_audio', gp)
        else:
            _telnyx_prompt_gather(ccid, 'wellness_message', gp, first_name=client.first_name, key=key)
        return

    if call_type == 'emergency':
        session = db.session.get(WellnessSession, session_id) if session_id else None
        contact = db.session.get(EmergencyContact, contact_id) if contact_id else None
        if log:
            log.status = 'answered'
            db.session.commit()
        if not (session and contact):
            _telnyx_prompt_speak(ccid, 'session_not_found_closing')
            return
        key = _required_keypress()
        gp = {'minimum_digits': 1, 'maximum_digits': 1, 'timeout_millis': 20000, 'valid_digits': '0123456789'}
        _telnyx_prompt_gather(ccid, 'emergency_message', gp,
                               client_name=session.client.full_name, attempt=session.current_attempt, key=key)
        return


def _telnyx_keypress(call_type, ccid, session_id, log, contact_id, digits):
    key = _required_keypress()
    matched = (digits == key)

    if call_type == 'reminder':
        from carecall.models import ReminderSession
        session = db.session.get(ReminderSession, session_id) if session_id else None
        prompt_key = 'reminder_unsuccessful_closing'
        if log:
            log.keypress_received = digits
            if matched:
                log.status = 'acknowledged'
                if session:
                    session.status = 'acknowledged'
                    session.resolved_at = datetime.utcnow()
                prompt_key = 'success_goodbye'
            db.session.commit()
        _telnyx_prompt_speak(ccid, prompt_key)
        return

    if call_type == 'wellness':
        session = db.session.get(WellnessSession, session_id) if session_id else None
        if session and log:
            log.keypress_received = digits
            if matched:
                log.status = 'acknowledged'
                session.status = 'acknowledged'
                session.resolved_at = datetime.utcnow()
                prompt_key = 'success_goodbye'
            else:
                log.status = 'wrong-keypress'
                prompt_key = 'wellness_unsuccessful_closing'
            db.session.commit()
        else:
            prompt_key = 'session_not_found_closing'
        _telnyx_prompt_speak(ccid, prompt_key)
        return

    if call_type == 'emergency':
        session = db.session.get(WellnessSession, session_id) if session_id else None
        contact = db.session.get(EmergencyContact, contact_id) if contact_id else None
        if session and log and contact:
            log.keypress_received = digits
            if matched:
                log.status = 'acknowledged'
                session.emergency_acknowledged = True
                session.acknowledged_by_contact_id = contact.id
                session.status = 'escalated'
                session.resolved_at = datetime.utcnow()
                db.session.commit()
                _telnyx_prompt_speak(ccid, 'emergency_ack_instruction', contact_name=contact.name)
            else:
                log.status = 'wrong-keypress'
                db.session.commit()
                _telnyx_prompt_speak(ccid, 'emergency_unsuccessful_closing')
        else:
            _telnyx_prompt_speak(ccid, 'session_not_found_closing')
        return


def _telnyx_handle_hangup(call_type, session_id, log, contact_id, payload):
    """Mirrors call_status() above, but triggered by Telnyx's call.hangup event
    instead of a Twilio/SignalWire-style status callback."""
    from carecall.scheduler import (
        handle_reminder_no_response, handle_wellness_no_response, handle_emergency_no_response,
    )

    if log and log.status in ('initiated', 'answered'):
        log.status = payload.get('hangup_cause') or 'completed'
        db.session.commit()

    if call_type == 'reminder' and session_id:
        from carecall.models import ReminderSession
        r_session = db.session.get(ReminderSession, session_id)
        if r_session and r_session.status == 'calling':
            Thread(target=handle_reminder_no_response, args=(session_id,), daemon=True).start()

    elif call_type == 'wellness' and session_id:
        session = db.session.get(WellnessSession, session_id)
        if session and session.status not in ('acknowledged', 'escalating', 'escalated', 'failed', 'cancelled'):
            Thread(target=handle_wellness_no_response, args=(session_id,), daemon=True).start()

    elif call_type == 'emergency' and session_id:
        session = db.session.get(WellnessSession, session_id)
        if session and not session.emergency_acknowledged and session.status != 'cancelled':
            Thread(target=handle_emergency_no_response, args=(session_id, contact_id), daemon=True).start()


# ── Telnyx inbound calls (mirrors inbound_call()/inbound_recording() above) ────
#
# Sequence: call.initiated -> answer -> call.answered -> speak/play greeting
# -> call.speak.ended/call.playback.ended -> record_start -> caller leaves a
# message -> call.recording.saved -> save it + speak a thank-you -> that
# speak's own call.speak.ended -> hangup.
#
# client_state carries a "tag|caller_number" string (base64, Telnyx's
# requirement) through this whole chain — the tag distinguishes which step a
# given speak.ended/playback.ended event belongs to (greeting vs. thank-you),
# and the caller's number rides along since call.recording.saved itself
# doesn't include a `from` field the way call.answered does.

def _telnyx_state_encode(tag, from_number=''):
    return base64.b64encode(f"{tag}|{from_number}".encode()).decode()


def _telnyx_state_decode(client_state):
    try:
        tag, _, from_number = base64.b64decode(client_state or '').decode().partition('|')
        return tag, from_number
    except Exception:
        return '', ''


def _telnyx_inbound_events(event_type, ccid, p):
    if event_type == 'call.initiated':
        _telnyx_safe_command(ccid, 'answer')
        return '', 200

    if event_type == 'call.answered':
        from flask import current_app
        from carecall.routes.api import _load_system_config, _INBOUND_GREETING_FILE

        from_number     = p.get('from', '')
        cfg             = _load_system_config()
        greeting_type   = cfg.get('inbound_greeting_type', 'script')
        greeting_script = cfg.get('inbound_greeting_script',
                                   "You have reached CareCall. "
                                   "Please leave a message after the tone and we will follow up with you.")
        greeting_path   = os.path.join(current_app.config['UPLOAD_FOLDER'], _INBOUND_GREETING_FILE)
        state           = _telnyx_state_encode('inbound_greeting', from_number)

        if greeting_type == 'recording' and os.path.isfile(greeting_path):
            _telnyx_safe_command(ccid, 'playback_start', {
                'audio_url': f"{_public_url()}/uploads/{_INBOUND_GREETING_FILE}",
                'client_state': state,
            })
        else:
            _telnyx_safe_command(ccid, 'speak', {
                'payload': greeting_script, 'voice': _voice(), 'language': 'en-US',
                'client_state': state,
            })
        return '', 200

    if event_type in ('call.speak.ended', 'call.playback.ended'):
        tag, from_number = _telnyx_state_decode(p.get('client_state'))
        if tag == 'inbound_greeting':
            # Greeting just finished — start recording the caller's message.
            _telnyx_safe_command(ccid, 'record_start', {
                'format': 'mp3', 'channels': 'single',
                'play_beep': True, 'max_length': 120, 'timeout_secs': 5,
                'client_state': _telnyx_state_encode('inbound_recording', from_number),
            })
        else:
            # This was the post-recording thank-you message finishing.
            _telnyx_safe_command(ccid, 'hangup')
        return '', 200

    if event_type == 'call.recording.saved':
        _, from_number = _telnyx_state_decode(p.get('client_state'))
        _telnyx_save_inbound_recording(ccid, p, from_number)
        return '', 200

    if event_type == 'call.hangup':
        return '', 200  # caller hung up mid-flow — nothing to clean up

    return '', 200


def _telnyx_save_inbound_recording(ccid, p, from_number):
    from carecall.models import InboundMessage, Client

    recording_id  = p.get('recording_id', '')
    recording_url = (p.get('recording_urls') or {}).get('mp3', '')
    started       = p.get('recording_started_at')
    ended         = p.get('recording_ended_at')

    duration_int = 0
    if started and ended:
        try:
            from datetime import datetime as _dt
            fmt = '%Y-%m-%dT%H:%M:%S.%fZ'
            duration_int = int((_dt.strptime(ended, fmt) - _dt.strptime(started, fmt)).total_seconds())
        except (ValueError, TypeError):
            pass

    if duration_int > 0 and (recording_id or recording_url):
        norm = normalize_phone(from_number)
        matched = next(
            (c for c in Client.query.all() if normalize_phone(c.phone) == norm),
            None,
        )
        msg = InboundMessage(
            call_sid=ccid,
            recording_sid=recording_id,
            recording_url=recording_url,
            from_number=from_number,
            duration_seconds=duration_int,
            received_at=datetime.utcnow(),
            matched_client_id=matched.id if matched else None,
        )
        db.session.add(msg)
        db.session.commit()
        logger.info(f"Inbound voicemail saved (Telnyx): {recording_id} from {from_number} ({duration_int}s)")
        _telnyx_prompt_speak(ccid, 'inbound_thanks_closing')
    else:
        logger.info(f"Inbound call from {from_number} — no recording (duration={duration_int}s)")
        _telnyx_prompt_speak(ccid, 'inbound_no_recording_closing')


# ── Test TwiML endpoint ────────────────────────────────────────────────────────

@webhooks_bp.route('/test', methods=['POST'])
def test_twiml():
    vr = VoiceResponse()
    _twiml_prompt(vr, 'test_call_message')
    vr.hangup()
    return _xml(vr)
