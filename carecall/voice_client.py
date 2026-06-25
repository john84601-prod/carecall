import os
import re
import logging

logger = logging.getLogger(__name__)


def get_provider_name():
    return os.getenv('VOICE_PROVIDER', 'twilio').strip().lower()


def get_client():
    """Return a REST client for the configured provider.

    SignalWire's Compatibility API SDK (`signalwire` package) mirrors the
    twilio-python client shape (calls.create, messages.create, etc.), so
    call sites that already use the Twilio SDK shape work unchanged.
    """
    provider = get_provider_name()
    if provider == 'signalwire':
        from signalwire.rest import Client
        project_id = os.getenv('SIGNALWIRE_PROJECT_ID', '').strip()
        token      = os.getenv('SIGNALWIRE_AUTH_TOKEN', '').strip()
        space_url  = os.getenv('SIGNALWIRE_SPACE_URL', '').strip()
        if not project_id or not token or not space_url:
            raise RuntimeError(
                "SIGNALWIRE_PROJECT_ID, SIGNALWIRE_AUTH_TOKEN and "
                "SIGNALWIRE_SPACE_URL must be set in .env"
            )
        return Client(project_id, token, signalwire_space_url=space_url)

    from twilio.rest import Client
    sid   = os.getenv('TWILIO_ACCOUNT_SID', '').strip()
    token = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
    if not sid or not token:
        raise RuntimeError(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env"
        )
    return Client(sid, token)


def get_from_number():
    provider = get_provider_name()
    key = 'SIGNALWIRE_FROM_NUMBER' if provider == 'signalwire' else 'TWILIO_FROM_NUMBER'
    num = os.getenv(key, '').strip()
    if not num:
        raise RuntimeError(f"{key} must be set in .env")
    return num


def normalize_phone(phone):
    """Strip all non-digit characters and return the last 10 digits.
    Used to match provider E.164 numbers against locally-formatted DB numbers.
    """
    digits = re.sub(r'\D', '', phone or '')
    return digits[-10:] if len(digits) >= 10 else digits


def send_sms(to_number, body):
    """Send an outbound SMS. Returns the provider's message SID."""
    client = get_client()
    msg = client.messages.create(
        to=to_number,
        from_=get_from_number(),
        body=body,
    )
    logger.info(f"SMS sent to {to_number} — SID: {msg.sid}")
    return msg.sid


def register_sms_webhook(sms_url):
    """Update the provider phone number's inbound SMS webhook to sms_url.
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
    """Initiate an outbound call. Returns the provider's call SID.

    machine_detection=True enables AMD.

    On Twilio, when amd_status_callback_url is also provided, asyncAmd mode
    is used: the answer webhook fires IMMEDIATELY when the call connects,
    and the AMD result is delivered separately to amd_status_callback_url.
    This eliminates the 3-5 second silence that humans would otherwise hear
    while AMD analysis runs.

    SignalWire's Compatibility API does not support async AMD: AMD there is
    always synchronous (the answer webhook is held until AMD resolves), so
    amd_status_callback_url is ignored on that provider and a warning is
    logged once per call so this difference isn't silently invisible.
    """
    provider = get_provider_name()
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
            if provider == 'signalwire':
                logger.warning(
                    "async AMD requested but not supported on SignalWire — "
                    "falling back to synchronous AMD (answer webhook will "
                    "wait for the AMD result)"
                )
            else:
                # Async AMD: answer webhook fires immediately; AMD result
                # comes back on a separate callback so we can redirect
                # voicemail calls.
                params['async_amd']                        = 'true'
                params['async_amd_status_callback']        = amd_status_callback_url
                params['async_amd_status_callback_method'] = 'POST'

    call = client.calls.create(**params)
    logger.info(f"Call initiated to {to_number} via {provider} — SID: {call.sid}")
    return call.sid


def validate_webhook_signature(request):
    """Validate the inbound webhook signature for the configured provider.
    Returns True if valid (or if validation is skipped due to missing
    credentials), False if the signature is present and invalid.
    """
    provider = get_provider_name()
    params = request.form.to_dict() if request.method == 'POST' else {}

    if provider == 'signalwire':
        token = os.getenv('SIGNALWIRE_AUTH_TOKEN', '').strip()
        if not token:
            logger.warning('SIGNALWIRE_AUTH_TOKEN not set — skipping webhook signature validation')
            return True
        from signalwire.request_validator import RequestValidator
        signature = request.headers.get('X-SignalWire-Signature', '')
        return RequestValidator(token).validate(request.url, params, signature)

    token = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
    if not token:
        logger.warning('TWILIO_AUTH_TOKEN not set — skipping webhook signature validation')
        return True
    from twilio.request_validator import RequestValidator
    signature = request.headers.get('X-Twilio-Signature', '')
    return RequestValidator(token).validate(request.url, params, signature)
