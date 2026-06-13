import os
import re
import logging
from twilio.rest import Client

logger = logging.getLogger(__name__)


def get_client():
    sid = os.getenv('TWILIO_ACCOUNT_SID', '').strip()
    token = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
    if not sid or not token:
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env"
        )
    return Client(sid, token)


def get_from_number():
    num = os.getenv('TWILIO_FROM_NUMBER', '').strip()
    if not num:
        raise RuntimeError("TWILIO_FROM_NUMBER must be set in .env")
    return num


def normalize_phone(phone):
    """Strip all non-digit characters and return the last 10 digits.
    Used to match Twilio E.164 numbers against locally-formatted DB numbers.
    """
    digits = re.sub(r'\D', '', phone or '')
    return digits[-10:] if len(digits) >= 10 else digits


def send_sms(to_number, body):
    """Send an outbound SMS. Returns the Twilio message SID."""
    client = get_client()
    msg = client.messages.create(
        to=to_number,
        from_=get_from_number(),
        body=body,
    )
    logger.info(f"SMS sent to {to_number} — SID: {msg.sid}")
    return msg.sid


def register_sms_webhook(sms_url):
    """Update the Twilio phone number's inbound SMS webhook to sms_url.
    Called at startup so the dynamic ngrok/public URL is always current.
    """
    try:
        client     = get_client()
        from_num   = get_from_number()
        numbers    = client.incoming_phone_numbers.list(phone_number=from_num)
        if not numbers:
            logger.warning(f"register_sms_webhook: number {from_num} not found in account")
            return
        numbers[0].update(sms_url=sms_url, sms_method='POST')
        logger.info(f"SMS webhook registered: {sms_url}")
    except Exception as e:
        logger.warning(f"register_sms_webhook failed (non-fatal): {e}")


def make_call(to_number, answer_url, status_callback_url,
              machine_detection=False, amd_status_callback_url=None,
              record=False, recording_status_callback=None):
    """Initiate an outbound call. Returns the Twilio call SID.

    machine_detection=True enables Twilio AMD.

    When amd_status_callback_url is also provided, asyncAmd mode is used:
    the answer webhook fires IMMEDIATELY when the call connects (no delay),
    and the AMD result is delivered separately to amd_status_callback_url.
    This eliminates the 3-5 second silence that humans experience while
    Twilio performs its analysis.

    Without amd_status_callback_url, DetectMessageEnd mode is used:
    Twilio waits for the voicemail beep before firing the answer webhook.
    """
    client = get_client()
    params = dict(
        to=to_number,
        from_=get_from_number(),
        url=answer_url,
        method='POST',
        status_callback=status_callback_url,
        status_callback_event=['completed', 'no-answer', 'busy', 'failed'],
        status_callback_method='POST',
    )
    if record:
        params['record'] = True
        if recording_status_callback:
            params['recording_status_callback']        = recording_status_callback
            params['recording_status_callback_method'] = 'POST'

    if machine_detection:
        params['machine_detection'] = 'DetectMessageEnd'
        if amd_status_callback_url:
            # Async AMD: answer webhook fires immediately; AMD result comes
            # back on a separate callback so we can redirect voicemail calls.
            params['async_amd']                    = 'true'
            params['async_amd_status_callback']    = amd_status_callback_url
            params['async_amd_status_callback_method'] = 'POST'

    call = client.calls.create(**params)
    logger.info(f"Call initiated to {to_number} — SID: {call.sid}")
    return call.sid
