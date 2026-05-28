import os
import logging
import requests

logger = logging.getLogger(__name__)
_public_url = None


def _try_ngrok(port):
    try:
        from pyngrok import ngrok, conf
        token = os.getenv('NGROK_AUTH_TOKEN', '').strip()
        if token:
            conf.get_default().auth_token = token
        # Use https protocol locally if SSL certs are configured so ngrok
        # can reach Flask when it's serving HTTPS on the same port.
        cert_file = os.getenv('CERT_FILE', '').strip()
        proto = 'https' if cert_file else 'http'
        tunnel = ngrok.connect(port, proto)
        url = tunnel.public_url
        # Always use https for the public-facing URL
        return url.replace('http://', 'https://')
    except Exception as e:
        logger.warning(f"ngrok unavailable: {e}")
        return None


def _try_public_ip(port):
    try:
        ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
        return f"http://{ip}:{port}"
    except Exception:
        return None


def get_public_url(port=None):
    if port is None:
        port = int(os.getenv('PORT', 5000))
    global _public_url
    if _public_url:
        return _public_url

    configured = os.getenv('PUBLIC_URL', '').strip()
    if configured:
        _public_url = configured.rstrip('/')
        logger.info(f"Using configured PUBLIC_URL: {_public_url}")
        return _public_url

    logger.info("PUBLIC_URL not set — trying ngrok...")
    url = _try_ngrok(port)
    if url:
        _public_url = url.rstrip('/')
        logger.info(f"ngrok tunnel active: {_public_url}")
        return _public_url

    logger.warning("ngrok failed — falling back to public IP (no TLS)")
    url = _try_public_ip(port)
    if url:
        _public_url = url.rstrip('/')
        logger.warning(f"Using public IP (Twilio requires TLS for production): {_public_url}")
        return _public_url

    raise RuntimeError(
        "Cannot determine a public URL for Twilio webhooks.\n"
        "Options:\n"
        "  1. Set PUBLIC_URL=http://your-server:5000 in .env\n"
        "  2. Set NGROK_AUTH_TOKEN in .env (free at ngrok.com)\n"
        "  3. Configure port forwarding on your router"
    )
