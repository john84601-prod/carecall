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
    print(f"\n CareCall running on http://0.0.0.0:{port}")
    print(" Open your browser to http://localhost:{}\n".format(port))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
