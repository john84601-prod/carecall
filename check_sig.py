import os
from twilio.request_validator import RequestValidator

os.environ.setdefault('FLASK_SECRET_KEY', 'x')  # not needed, just isolating import

SIGNING_KEY = 'PSK_HEp1n7xaGuUcHMEJVgd7QP6y'
URL = 'https://debit-tuesday-eskimo.ngrok-free.dev/webhook/call-status?call_type=reminder&log_id=125&session_id=24'
RECEIVED = 'f8TaT9zM161Nzfm12kn7I0LlBsc='

ALL_PARAMS = {
    'CallSid': '45998e41-1360-4034-b8c7-227c945ef389',
    'AccountSid': 'cf6941f3-55db-4207-ae48-96279ef4c088',
    'ApiVersion': '2010-04-01',
    'Direction': 'outbound-api',
    'From': '+17754107230',
    'To': '+17755270674',
    'Timestamp': 'Thu, 25 Jun 2026 20:11:01 +0000',
    'CallStatus': 'completed',
    'CallbackSource': 'call-progress-events',
    'CallDuration': '58',
    'AudioInMos': '4.5',
    'AudioInAveragePtime': '20.01',
    'AudioInMediaPacketCount': '2873',
    'AudioInDtmfPacketCount': '0',
    'AudioInSkipPacketCount': '0',
    'AudioInFlushPacketCount': '0',
    'AudioInLargestJbSize': '0',
    'AudioInJitterMinVariance': '0.36',
    'AudioInJitterMaxVariance': '1.53',
    'AudioOutMediaPacketCount': '2686',
    'AudioOutDtmfPacketCount': '0',
    'AudioOutLost': '0',
    'HangupDirection': 'outbound',
    'HangupBy': '+17754107230',
    'SipCallId': '45998e41-1360-4034-b8c7-227c945ef389',
}

CORE_KEYS = {'CallSid', 'AccountSid', 'ApiVersion', 'Direction', 'From', 'To', 'CallStatus'}

validator = RequestValidator(SIGNING_KEY)

print("Target (received):", RECEIVED)
print()
print("All params:        ", validator.compute_signature(URL, ALL_PARAMS))
print("Core only:          ", validator.compute_signature(URL, {k: v for k, v in ALL_PARAMS.items() if k in CORE_KEYS}))
print("No Timestamp:       ", validator.compute_signature(URL, {k: v for k, v in ALL_PARAMS.items() if k != 'Timestamp'}))
print("No CallbackSource:  ", validator.compute_signature(URL, {k: v for k, v in ALL_PARAMS.items() if k != 'CallbackSource'}))
print("No audio fields:    ", validator.compute_signature(URL, {k: v for k, v in ALL_PARAMS.items() if not k.startswith('Audio')}))
print("Core + CallDuration:", validator.compute_signature(URL, {k: v for k, v in ALL_PARAMS.items() if k in CORE_KEYS or k == 'CallDuration'}))

print()
print("Also trying base_url (no query string) for each:")
from urllib.parse import urlsplit
BASE_URL = urlsplit(URL)._replace(query='').geturl()
print("base_url =", BASE_URL)
print("All params on base_url:", validator.compute_signature(BASE_URL, ALL_PARAMS))
print("Core only on base_url: ", validator.compute_signature(BASE_URL, {k: v for k, v in ALL_PARAMS.items() if k in CORE_KEYS}))
