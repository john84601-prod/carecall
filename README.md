# CareCall

Automated calling system for reminders and wellness checks. Built with Flask and Twilio.

## What it does

- **Reminder calls** — plays an MP3 to the client at a scheduled time
- **Wellness checks** — calls the client and waits for a key press to confirm they're okay; retries if unanswered, then escalates to emergency contacts

## Requirements

- Python 3.10+
- A [Twilio](https://www.twilio.com) account with a phone number
- Optionally: [ngrok](https://ngrok.com) if running behind NAT

## Setup

```bash
cp .env.example .env
# Fill in your Twilio credentials and other values in .env

pip install -r requirements.txt
python run.py
```

The web UI is available at `http://localhost:5000`.

## Running as a service (Linux)

A systemd unit file is included:

```bash
sudo cp carecall.service /etc/systemd/system/
sudo systemctl enable --now carecall
```

## Environment variables

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | From the Twilio console |
| `TWILIO_AUTH_TOKEN` | From the Twilio console |
| `TWILIO_FROM_NUMBER` | Your Twilio phone number (E.164 format) |
| `FLASK_SECRET_KEY` | Random string for Flask session signing |
| `PORT` | Port to listen on (default: 5000) |
| `PUBLIC_URL` | Your server's public URL for Twilio webhooks; leave blank to auto-detect via ngrok |
| `NGROK_AUTH_TOKEN` | Required if using ngrok for webhook tunneling |
