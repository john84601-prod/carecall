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


def make_call(to_number, answer_url, status_callback_url, machine_detection=False):
    """Initiate an outbound call. Returns the Twilio call SID.

    machine_detection=True enables Twilio AMD — the answer webhook will receive
    an AnsweredBy parameter: 'human', 'machine_start', 'machine_end_beep', etc.
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
        # DetectMessageEnd waits for the voicemail beep before firing the
        # answer webhook, so the message plays after the greeting finishes.
        params['machine_detection'] = 'DetectMessageEnd'

    call = client.calls.create(**params)
    logger.info(f"Call initiated to {to_number} — SID: {call.sid}")
    return call.sid
