import os
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


def make_call(to_number, answer_url, status_callback_url,
              machine_detection=False, amd_status_callback_url=None):
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
