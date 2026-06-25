import os
import re
import logging

import requests

logger = logging.getLogger(__name__)


def get_provider_name():
    return os.getenv('VOICE_PROVIDER', 'twilio').strip().lower()


# ── SignalWire: thin REST client over `requests` ───────────────────────────────
#
# SignalWire's LAML Compatibility API is intentionally Twilio-API-shaped
# (same endpoints, same form fields, same response JSON), but the official
# `signalwire` pip package hard-pins twilio==6.54.0, which conflicts with
# this project's twilio>=9.0.0 requirement. Talking to the HTTP API directly
# avoids that conflict and needs nothing beyond `requests`.

class _SWResult:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        # SignalWire/Twilio JSON keys are snake_case already (e.g. "sid", "duration")
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


class _SWNumber(_SWResult):
    def __init__(self, data, client):
        super().__init__(data)
        self._client = client

    def update(self, sms_url=None, sms_method='POST'):
        payload = {}
        if sms_url is not None:
            payload['SmsUrl'] = sms_url
            payload['SmsMethod'] = sms_method
        self._client._post(f"IncomingPhoneNumbers/{self._data['sid']}.json", payload)


class _SWCallHandle:
    def __init__(self, client, call_sid):
        self._client = client
        self._call_sid = call_sid

    def update(self, url=None, method='POST'):
        payload = {}
        if url is not None:
            payload['Url'] = url
            payload['Method'] = method
        self._client._post(f"Calls/{self._call_sid}.json", payload)


class _SWRecordingHandle:
    def __init__(self, client, recording_sid):
        self._client = client
        self._recording_sid = recording_sid

    def fetch(self):
        data = self._client._get(f"Recordings/{self._recording_sid}.json")
        return _SWResult(data)

    def delete(self):
        self._client._delete(f"Recordings/{self._recording_sid}.json")


class _SWMessages:
    def __init__(self, client):
        self._client = client

    def create(self, to, from_, body):
        data = self._client._post('Messages.json', {'To': to, 'From': from_, 'Body': body})
        return _SWResult(data)


class _SWCalls:
    def __init__(self, client):
        self._client = client

    def create(self, **params):
        # Map the camel-free kwarg names this codebase uses to the REST API's field names.
        field_map = {
            'to': 'To', 'from_': 'From', 'url': 'Url', 'method': 'Method',
            'status_callback': 'StatusCallback',
            'status_callback_event': 'StatusCallbackEvent',
            'status_callback_method': 'StatusCallbackMethod',
            'record': 'Record',
            'recording_status_callback': 'RecordingStatusCallback',
            'recording_status_callback_method': 'RecordingStatusCallbackMethod',
            'machine_detection': 'MachineDetection',
            'async_amd': 'AsyncAmd',
            'async_amd_status_callback': 'AsyncAmdStatusCallback',
            'async_amd_status_callback_method': 'AsyncAmdStatusCallbackMethod',
        }
        payload = {}
        for key, value in params.items():
            field = field_map.get(key, key)
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            payload[field] = value
        data = self._client._post('Calls.json', payload)
        return _SWResult(data)


class _SWIncomingNumbers:
    def __init__(self, client):
        self._client = client

    def list(self, phone_number=None):
        params = {'PhoneNumber': phone_number} if phone_number else {}
        data = self._client._get('IncomingPhoneNumbers.json', params=params)
        return [_SWNumber(n, self._client) for n in data.get('incoming_phone_numbers', [])]


class SignalWireClient:
    """Minimal Twilio-SDK-shaped client for SignalWire's LAML Compatibility API."""

    def __init__(self, project_id, token, space_url):
        self.project_id = project_id
        self.token = token
        self.base_url = f"https://{space_url}/api/laml/2010-04-01/Accounts/{project_id}"
        self.messages = _SWMessages(self)
        self.calls = _SWCalls(self)
        self.incoming_phone_numbers = _SWIncomingNumbers(self)

    def __call__(self, *args, **kwargs):
        raise TypeError("SignalWireClient is not callable directly")

    def recordings(self, recording_sid):
        return _SWRecordingHandle(self, recording_sid)

    # Note: `client.calls(sid)` (call as a function) is used for mid-call
    # redirects — implemented via a dedicated method below since `self.calls`
    # is already the create()-only collection above.

    def _auth(self):
        return (self.project_id, self.token)

    def _raise_with_body(self, r):
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            detail = r.text
            try:
                body = r.json()
                detail = body.get('message') or body.get('detail') or body
            except ValueError:
                pass
            raise requests.HTTPError(f"{e} — response body: {detail}", response=r) from None

    def _get(self, path, params=None):
        r = requests.get(f"{self.base_url}/{path}", params=params, auth=self._auth(), timeout=30)
        self._raise_with_body(r)
        return r.json()

    def _post(self, path, data):
        r = requests.post(f"{self.base_url}/{path}", data=data, auth=self._auth(), timeout=30)
        self._raise_with_body(r)
        return r.json() if r.content else {}

    def _delete(self, path):
        r = requests.delete(f"{self.base_url}/{path}", auth=self._auth(), timeout=30)
        self._raise_with_body(r)


class _CallsAccessor:
    """Lets call sites write both client.calls.create(...) and client.calls(sid).update(...),
    matching the twilio-python SDK's dual-purpose `calls` accessor."""

    def __init__(self, sw_client):
        self._sw_client = sw_client

    def create(self, **params):
        return self._sw_client._calls_create(**params)

    def __call__(self, call_sid):
        return _SWCallHandle(self._sw_client, call_sid)


def _patch_calls_accessor(client):
    client._calls_create = client.calls.create
    client.calls = _CallsAccessor(client)
    return client


def get_client():
    """Return a REST client for the configured provider."""
    provider = get_provider_name()
    if provider == 'signalwire':
        project_id = os.getenv('SIGNALWIRE_PROJECT_ID', '').strip()
        token      = os.getenv('SIGNALWIRE_AUTH_TOKEN', '').strip()
        space_url  = os.getenv('SIGNALWIRE_SPACE_URL', '').strip()
        if not project_id or not token or not space_url:
            raise RuntimeError(
                "SIGNALWIRE_PROJECT_ID, SIGNALWIRE_AUTH_TOKEN and "
                "SIGNALWIRE_SPACE_URL must be set in .env"
            )
        return _patch_calls_accessor(SignalWireClient(project_id, token, space_url))

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


def get_recording_audio_url(recording_sid):
    """Build the authenticated REST URL to fetch a recording's audio bytes."""
    provider = get_provider_name()
    if provider == 'signalwire':
        space_url  = os.getenv('SIGNALWIRE_SPACE_URL', '').strip()
        project_id = os.getenv('SIGNALWIRE_PROJECT_ID', '').strip()
        return (f"https://{space_url}/api/laml/2010-04-01/Accounts/{project_id}"
                f"/Recordings/{recording_sid}.mp3")
    account_sid = os.getenv('TWILIO_ACCOUNT_SID', '').strip()
    return f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Recordings/{recording_sid}.mp3"


def get_recording_audio_auth():
    """Return the (user, password) tuple for fetching recording audio."""
    provider = get_provider_name()
    if provider == 'signalwire':
        return (os.getenv('SIGNALWIRE_PROJECT_ID', '').strip(),
                os.getenv('SIGNALWIRE_AUTH_TOKEN', '').strip())
    return (os.getenv('TWILIO_ACCOUNT_SID', '').strip(),
            os.getenv('TWILIO_AUTH_TOKEN', '').strip())


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
    # SignalWire's Compatibility API only accepts initiated/ringing/answered/
    # completed as event names (no no-answer/busy/failed). A single
    # 'completed' event still covers every terminal outcome — the actual
    # result (busy, no-answer, failed, completed) arrives in that callback's
    # CallStatus field on both providers.
    status_events = ['completed'] if provider == 'signalwire' else ['completed', 'no-answer', 'busy', 'failed']
    params = dict(
        to=to_number,
        from_=get_from_number(),
        url=answer_url,
        method='POST',
        status_callback=status_callback_url,
        status_callback_event=status_events,
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

    from twilio.request_validator import RequestValidator

    if provider == 'signalwire':
        token = os.getenv('SIGNALWIRE_AUTH_TOKEN', '').strip()
        if not token:
            logger.warning('SIGNALWIRE_AUTH_TOKEN not set — skipping webhook signature validation')
            return True
        # SignalWire signs with the exact Twilio algorithm and even sends an
        # X-Twilio-Signature alias header, so reuse Twilio's own validator.
        signature = request.headers.get('X-SignalWire-Signature', '')
        validator = RequestValidator(token)
        valid = validator.validate(request.url, params, signature)
        if not valid:
            logger.warning(
                f"SignalWire sig debug — token_len={len(token)} token_prefix={token[:4]!r} "
                f"url={request.url!r} base_url={request.base_url!r} "
                f"computed_on_url={validator.compute_signature(request.url, params)!r} "
                f"computed_on_base_url={validator.compute_signature(request.base_url, params)!r} "
                f"received={signature!r}"
            )
        return valid

    token = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
    if not token:
        logger.warning('TWILIO_AUTH_TOKEN not set — skipping webhook signature validation')
        return True
    signature = request.headers.get('X-Twilio-Signature', '')
    return RequestValidator(token).validate(request.url, params, signature)
