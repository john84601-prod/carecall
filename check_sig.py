import hmac
import hashlib
import base64

URL = 'https://debit-tuesday-eskimo.ngrok-free.dev/webhook/call-status?call_type=reminder&log_id=127&session_id=26'

ALL_PARAMS = {
    'CallSid': '49ca9729-829b-4d97-b325-2dee20dab54c',
    'AccountSid': 'cf6941f3-55db-4207-ae48-96279ef4c088',
    'ApiVersion': '2010-04-01',
    'Direction': 'outbound-api',
    'From': '+17754107230',
    'To': '+17755270674',
    'Timestamp': 'Thu, 25 Jun 2026 20:26:02 +0000',
    'CallStatus': 'completed',
    'CallbackSource': 'call-progress-events',
    'CallDuration': '57',
    'AudioInMos': '4.5',
    'AudioInAveragePtime': '20.01',
    'AudioInMediaPacketCount': '2829',
    'AudioInDtmfPacketCount': '0',
    'AudioInSkipPacketCount': '1',
    'AudioInFlushPacketCount': '0',
    'AudioInLargestJbSize': '0',
    'AudioInJitterMinVariance': '0',
    'AudioInJitterMaxVariance': '85.95',
    'AudioOutMediaPacketCount': '2712',
    'AudioOutDtmfPacketCount': '0',
    'AudioOutLost': '0',
    'HangupDirection': 'outbound',
    'HangupBy': '+17754107230',
    'SipCallId': '49ca9729-829b-4d97-b325-2dee20dab54c',
}

CORE_KEYS = {'CallSid', 'AccountSid', 'ApiVersion', 'Direction', 'From', 'To', 'CallStatus'}

TARGET_SHA1   = '5acHHgjgygt2kb4Fi20IpBZzIvo='
TARGET_SHA256 = 'v5TfDRJddv8Cyj0mTjyy7VfURAK7UmeP4ZO2UMZkbLs='

SIGNING_KEY = 'PSK_HEp1n7xaGuUcHMEJVgd7QP6y'
API_TOKEN   = 'PTd5011997444a9a08171a0b170a2861576443ac8eb34f2d8d'


def concat(url, params, keys=None):
    data = url
    for key in sorted(params.keys() if keys is None else keys):
        data += key + params[key]
    return data


def sig(secret, data, algo):
    return base64.b64encode(hmac.new(secret.encode(), data.encode(), algo).digest()).decode()


param_sets = {
    'all':         ALL_PARAMS,
    'core_only':   {k: v for k, v in ALL_PARAMS.items() if k in CORE_KEYS},
    'no_audio':    {k: v for k, v in ALL_PARAMS.items() if not k.startswith('Audio')},
}

from urllib.parse import urlsplit
base_url = urlsplit(URL)._replace(query='').geturl()
url_variants = {'full_url': URL, 'base_url': base_url}

print(f"target sha1   = {TARGET_SHA1}")
print(f"target sha256 = {TARGET_SHA256}")
print()

for secret_name, secret in [('signing_key', SIGNING_KEY), ('api_token', API_TOKEN)]:
    for url_name, url in url_variants.items():
        for pset_name, pset in param_sets.items():
            data = concat(url, pset)
            s1 = sig(secret, data, hashlib.sha1)
            s256 = sig(secret, data, hashlib.sha256)
            match1 = ' <-- MATCH SHA1' if s1 == TARGET_SHA1 else ''
            match256 = ' <-- MATCH SHA256' if s256 == TARGET_SHA256 else ''
            print(f"{secret_name:12} {url_name:10} {pset_name:10} sha1={s1}{match1}")
            print(f"{secret_name:12} {url_name:10} {pset_name:10} sha256={s256}{match256}")
