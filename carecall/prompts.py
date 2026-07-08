"""Global, admin-editable TTS prompts used across reminder/wellness/emergency/
inbound call flows (both the TwiML provider path and the Telnyx Call Control
path in routes/webhooks.py). Each prompt can be left as text (read aloud via
the configured TTS voice) or replaced with a custom uploaded recording —
mirroring the pattern already used for the inbound caller greeting.

These are intentionally separate from a schedule's own per-client mp3
(Schedule.mp3_filename): a schedule recording replaces just that one
reminder/wellness message for that one client, while a prompt recording here
replaces that message everywhere, for every client, until changed.
"""

import os

PROMPT_DEFAULTS = {
    'reminder_message': "This is your scheduled reminder. Have a great day.",
    'reminder_unsuccessful_closing': "Thank you. Goodbye.",
    'success_goodbye': "Thank you, goodbye!",

    'wellness_message': "Hello {first_name}, this is your wellness check call. Please press {key} to confirm you are okay.",
    'wellness_voicemail_message': (
        "Hello {first_name}, this is a wellness check call. We were unable to reach you. "
        "Please call back or press {key} when we try again to confirm you are okay."
    ),
    'wellness_unsuccessful_closing': "We did not receive your response. Goodbye.",

    'emergency_message': (
        "This is an urgent wellness notification. {client_name} has not responded to "
        "{attempt} wellness check calls. As their emergency contact, please press {key} "
        "to confirm you will follow up with them immediately."
    ),
    'emergency_voicemail_message': (
        "Urgent message for {contact_name}. {client_name} has not responded to "
        "{attempt} wellness check calls. Please check on them as soon as possible."
    ),
    'emergency_ack_instruction': (
        "Thank you {contact_name}. Your acknowledgment has been recorded. "
        "Please follow up with the client as soon as possible."
    ),
    'emergency_unsuccessful_closing': "We did not receive your acknowledgment. Goodbye.",

    'session_not_found_closing': "Session not found. Goodbye.",
    'test_call_message': "CareCall test call successful. Your configuration is working correctly.",
    'inbound_no_recording_closing': "We did not receive a recording. Goodbye.",
    'inbound_thanks_closing': "Thank you for your message. Goodbye.",
}

PROMPT_LABELS = {
    'reminder_message': "Reminder call message",
    'reminder_unsuccessful_closing': "Reminder call — no/wrong keypress closing",
    'success_goodbye': "Successful call closing (reminder & wellness acknowledgment)",
    'wellness_message': "Wellness check message",
    'wellness_voicemail_message': "Wellness check — voicemail message",
    'wellness_unsuccessful_closing': "Wellness check — no/wrong keypress closing",
    'emergency_message': "Emergency contact alert message",
    'emergency_voicemail_message': "Emergency contact alert — voicemail message",
    'emergency_ack_instruction': "Emergency contact — acknowledgment confirmation",
    'emergency_unsuccessful_closing': "Emergency contact — no/wrong keypress closing",
    'session_not_found_closing': "Generic error closing (stale/invalid call link)",
    'test_call_message': "Test call message",
    'inbound_no_recording_closing': "Inbound voicemail — nothing recorded closing",
    'inbound_thanks_closing': "Inbound voicemail — thank-you closing",
}

# Prompts that include {placeholders} can't sensibly be replaced by a static
# recording without losing the personalization, but per-schedule mp3s already
# accept that same tradeoff — so it's allowed here too, the recording just
# plays in place of the whole personalized line.
PROMPT_KEYS = list(PROMPT_DEFAULTS.keys())


def prompt_recording_filename(key):
    return f"_prompt_{key}.mp3"


def get_prompt_text(prompt_key, **kwargs):
    # Named prompt_key, not key — some prompts (e.g. wellness_message,
    # emergency_message) take a `key` template kwarg for the DTMF digit to
    # press, which would otherwise collide with this parameter (TypeError:
    # got multiple values for argument 'key').
    from carecall.routes.api import _load_system_config
    cfg = _load_system_config()
    entry = cfg.get('prompts', {}).get(prompt_key, {})
    text = entry.get('script') or PROMPT_DEFAULTS.get(prompt_key, '')
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text


def get_prompt_recording_path(key):
    """Return the absolute path to key's custom recording if one is active, else None."""
    from flask import current_app
    from carecall.routes.api import _load_system_config
    cfg = _load_system_config()
    entry = cfg.get('prompts', {}).get(key, {})
    if entry.get('type') != 'recording':
        return None
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], prompt_recording_filename(key))
    return path if os.path.isfile(path) else None
