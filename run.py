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
    print(f"\n CareCall running on {scheme}://0.0.0.0:{port}")
    print(f" Open your browser to {scheme}://localhost:{port}\n")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False,
            ssl_context=ssl_context)
