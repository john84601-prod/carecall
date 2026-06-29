import os
import re
import time
import logging
import threading
from urllib.parse import urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

_call_throttle_lock = threading.Lock()
_last_call_time = 0.0
_telnyx_record_warned = False


def get_provider_name():
    return os.getenv('VOICE_PROVIDER', 'twilio').strip().lower()


def _throttle_outbound_call():
    """Enforce a minimum gap between outbound calls account-wide, as a
    safety net against the voice provider's outbound call rate limit
    (observed on SignalWire: "Exceeded Outbound Call Rate" even on calls
    that weren't obviously back-to-back from this app's perspective).
    Override with MIN_CALL_SPACING_SECONDS in .env if needed.
    """
    global _last_call_time
    min_gap = float(os.getenv('MIN_CALL_SPACING_SECONDS', '2'))
    with _call_throttle_lock:
        wait = min_gap - (time.monotonic() - _last_call_time)
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.monotonic()


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


# ── Telnyx: Call Control API (JSON/event-driven, not TwiML) ────────────────────
#
# Telnyx's Call Control API is fundamentally different from Twilio/SignalWire's
# TwiML model: instead of returning an XML document describing what to do,
# you place a call, then react to webhook *events* (call.answered,
# call.machine.premium.detection.ended, call.gather.ended, call.hangup, ...)
# by issuing one-off commands (speak, gather_using_speak, playback_start,
# hangup) against the call's call_control_id. See routes/webhooks.py's
# `/telnyx` route for the event dispatcher built around this.

TELNYX_API_BASE = 'https://api.telnyx.com/v2'


def _telnyx_headers():
    key = os.getenv('TELNYX_API_KEY', '').strip()
    if not key:
        raise RuntimeError("TELNYX_API_KEY must be set in .env")
    return {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}


def _telnyx_request(method, path, json=None):
    r = requests.request(method, f"{TELNYX_API_BASE}{path}",
                          headers=_telnyx_headers(), json=json, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        detail = r.text
        try:
            detail = r.json()
        except ValueError:
            pass
        raise requests.HTTPError(f"{e} — response body: {detail}", response=r) from None
    return r.json() if r.content else {}


def telnyx_command(call_control_id, action, payload=None):
    """Issue a Call Control command (speak, gather_using_speak, gather_using_audio,
    playback_start, hangup, ...) against a live call."""
    return _telnyx_request('POST', f'/calls/{call_control_id}/actions/{action}', json=payload or {})


def _telnyx_make_call(to_number, answer_url, machine_detection, record):
    """Place an outbound call via Telnyx Call Control.

    answer_url is the TwiML-style path/query this codebase already builds for
    Twilio/SignalWire (e.g. ".../webhook/wellness-answer?session_id=1&log_id=2").
    Its path tells us which flow (reminder/wellness/emergency) this call
    belongs to; the query string (session_id/log_id/contact_id) is reused
    as-is. Telnyx delivers ALL events for a call to one webhook_url, so we
    rebuild the URL to point at the single /webhook/telnyx dispatcher with
    call_type added.
    """
    global _telnyx_record_warned
    if record and not _telnyx_record_warned:
        logger.warning("Call recording is not yet implemented for the Telnyx provider — ignoring record=True")
        _telnyx_record_warned = True

    parts = urlsplit(answer_url)
    if 'reminder' in parts.path:
        call_type = 'reminder'
    elif 'wellness' in parts.path:
        call_type = 'wellness'
    elif 'emergency' in parts.path:
        call_type = 'emergency'
    else:
        call_type = 'test'

    query = f"call_type={call_type}"
    if parts.query:
        query += f"&{parts.query}"
    webhook_url = urlunsplit((parts.scheme, parts.netloc, '/webhook/telnyx', query, ''))

    connection_id = os.getenv('TELNYX_CONNECTION_ID', '').strip()
    if not connection_id:
        raise RuntimeError("TELNYX_CONNECTION_ID must be set in .env")

    payload = {
        'connection_id': connection_id,
        'to': to_number,
        'from': get_from_number(),
        'webhook_url': webhook_url,
    }
    if machine_detection:
        payload['answering_machine_detection'] = 'premium'

    _throttle_outbound_call()
    data = _telnyx_request('POST', '/calls', json=payload)
    return data['data']['call_control_id']


def _telnyx_send_sms(to_number, body):
    payload = {'to': to_number, 'from': get_from_number(), 'text': body}
    profile_id = os.getenv('TELNYX_MESSAGING_PROFILE_ID', '').strip()
    if profile_id:
        payload['messaging_profile_id'] = profile_id
    data = _telnyx_request('POST', '/messages', json=payload)
    return data['data']['id']


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

    if provider == 'telnyx':
        raise RuntimeError(
            "get_client() is not supported for the Telnyx provider — its REST API "
            "shape is too different from the Twilio SDK. Use make_call()/send_sms() "
            "or telnyx_command() directly instead."
        )

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
    key_map = {'signalwire': 'SIGNALWIRE_FROM_NUMBER', 'telnyx': 'TELNYX_FROM_NUMBER'}
    key = key_map.get(provider, 'TWILIO_FROM_NUMBER')
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
    if get_provider_name() == 'telnyx':
        msg_id = _telnyx_send_sms(to_number, body)
        logger.info(f"SMS sent to {to_number} — SID: {msg_id}")
        return msg_id

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

    Telnyx routes inbound SMS at the Messaging Profile level (shared across
    numbers), not per-number via a REST call like Twilio/SignalWire — this
    has to be set once, by hand, in the Telnyx portal.
    """
    if get_provider_name() == 'telnyx':
        logger.info(
            "Telnyx: inbound SMS routing is not auto-registered — set it once in "
            "the portal under Messaging -> Messaging Profiles -> (your profile) -> "
            f"Inbound -> Webhook URL = {sms_url}"
        )
        return
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

    Telnyx is handled separately via _telnyx_make_call(): its Call Control
    API has no TwiML/status-callback concept, so status_callback_url and
    amd_status_callback_url are ignored there — all events (including AMD
    results) arrive on the single /webhook/telnyx dispatcher instead.
    """
    provider = get_provider_name()
    if provider == 'telnyx':
        sid = _telnyx_make_call(to_number, answer_url, machine_detection, record)
        logger.info(f"Call initiated to {to_number} via telnyx — call_control_id: {sid}")
        return sid

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

    _throttle_outbound_call()
    call = client.calls.create(**params)
    logger.info(f"Call initiated to {to_number} via {provider} — SID: {call.sid}")
    return call.sid


def validate_webhook_signature(request):
    """Validate the inbound webhook signature for the configured provider.
    Returns True if valid (or if validation is skipped due to missing
    credentials), False if the signature is present and invalid.
    """
    provider = get_provider_name()

    if provider == 'telnyx':
        # Telnyx webhooks are JSON, signed with Ed25519 over "{timestamp}|{raw_body}",
        # verified against the account's Public Key (NOT the API key) from the
        # portal — a completely different scheme from Twilio/SignalWire's
        # form-encoded HMAC signatures.
        public_key = os.getenv('TELNYX_PUBLIC_KEY', '').strip()
        if not public_key:
            logger.warning('TELNYX_PUBLIC_KEY not set — skipping webhook signature validation')
            return True
        signature = request.headers.get('Telnyx-Signature-Ed25519', '')
        timestamp = request.headers.get('Telnyx-Timestamp', '')
        if not signature or not timestamp:
            return False
        try:
            import nacl.signing
            import nacl.encoding
            verify_key = nacl.signing.VerifyKey(public_key, encoder=nacl.encoding.Base64Encoder)
            signed_payload = f"{timestamp}|".encode() + request.get_data()
            verify_key.verify(signed_payload, nacl.encoding.Base64Encoder.decode(signature))
            return True
        except ImportError:
            logger.warning('pynacl not installed — skipping Telnyx webhook signature validation')
            return True
        except Exception as e:
            logger.warning(f'Telnyx webhook signature validation failed: {e}')
            return False

    params = request.form.to_dict() if request.method == 'POST' else {}

    from twilio.request_validator import RequestValidator

    if provider == 'signalwire':
        # Webhook signatures are signed with the Space's Signing Key
        # (PSK_...), NOT the PT... API token used for REST auth — those are
        # two separate credentials in the SignalWire dashboard.
        signing_key = os.getenv('SIGNALWIRE_SIGNING_KEY', '').strip()
        if not signing_key:
            logger.warning('SIGNALWIRE_SIGNING_KEY not set — skipping webhook signature validation')
            return True
        signature = request.headers.get('X-SignalWire-Signature', '')
        validator = RequestValidator(signing_key)
        valid = validator.validate(request.url, params, signature)
        if not valid:
            if params.get('CallbackSource') == 'call-progress-events':
                # SignalWire's "call progress events" callback (carries audio
                # QoS telemetry alongside CallStatus) doesn't produce a
                # signature that matches any combination of secret/hash/URL
                # we could derive (confirmed by brute-forcing SHA1/SHA256
                # against both the signing key and API token) — likely a
                # quirk/bug in their compatibility layer for this specific
                # event type. The route params (session_id/log_id) already
                # scope this to a known session, so accept it rather than
                # silently dropping every call's completion notification.
                logger.warning(
                    'SignalWire call-progress-events callback has an '
                    'unverifiable signature — accepting anyway (see voice_client.py)'
                )
                return True
            sig_headers = {k: v for k, v in request.headers.items() if 'signature' in k.lower()}
            logger.warning(
                f"SignalWire sig debug — path={request.path!r} url={request.url!r} "
                f"params={params!r} "
                f"computed={validator.compute_signature(request.url, params)!r} "
                f"all_signature_headers={sig_headers!r}"
            )
        return valid

    token = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
    if not token:
        logger.warning('TWILIO_AUTH_TOKEN not set — skipping webhook signature validation')
        return True
    signature = request.headers.get('X-Twilio-Signature', '')
    return RequestValidator(token).validate(request.url, params, signature)
