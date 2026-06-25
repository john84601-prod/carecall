import os
import logging

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

from carecall import create_app
from carecall.scheduler import init_scheduler

app = create_app()
init_scheduler(app)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))

    # Optional SSL — set CERT_FILE and KEY_FILE in .env to enable HTTPS
    cert_file = os.getenv('CERT_FILE', '')
    key_file  = os.getenv('KEY_FILE', '')
    ssl_context = (cert_file, key_file) if cert_file and key_file else None

    scheme = 'https' if ssl_context else 'http'

    # Eagerly initialize the public URL (starts ngrok if needed) so it's
    # ready before the first scheduled call fires and shows in logs/settings.
    with app.app_context():
        try:
            from carecall.tunnel import get_public_url
            public_url = get_public_url(port)
            print(f"\n CareCall running on {scheme}://0.0.0.0:{port}")
            print(f" Public URL for Twilio: {public_url}")
            print(f" Open your browser to: {public_url}\n")
        except Exception as e:
            public_url = None
            print(f"\n CareCall running on {scheme}://0.0.0.0:{port}")
            print(f" WARNING: Could not determine public URL: {e}\n")

    from carecall.lcd_display import start as start_lcd
    from carecall.voice_client import get_from_number as _get_from_number
    try:
        from_number = _get_from_number()
    except Exception:
        from_number = None
    start_lcd(public_url, from_number)

    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False,
            ssl_context=ssl_context)
