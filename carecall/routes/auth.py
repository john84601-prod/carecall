import hmac
import logging
import os
import threading
import time

from flask import Blueprint, redirect, render_template, request, session

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# ── Brute-force throttle ────────────────────────────────────────────────────
# In-memory per-IP lockout (mirrors the throttle style in voice_client.py) —
# fine for a single-process deployment, no external store needed.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 15 * 60
_login_lock = threading.Lock()
_login_failures = {}  # ip -> (fail_count, first_failure_monotonic)


def _login_blocked(ip):
    with _login_lock:
        entry = _login_failures.get(ip)
        if not entry:
            return False
        count, first_failure = entry
        if time.monotonic() - first_failure > _LOGIN_LOCKOUT_SECONDS:
            del _login_failures[ip]
            return False
        return count >= _LOGIN_MAX_ATTEMPTS


def _record_login_failure(ip):
    with _login_lock:
        count, first_failure = _login_failures.get(ip, (0, time.monotonic()))
        if time.monotonic() - first_failure > _LOGIN_LOCKOUT_SECONDS:
            count, first_failure = 0, time.monotonic()
        _login_failures[ip] = (count + 1, first_failure)


def _clear_login_failures(ip):
    with _login_lock:
        _login_failures.pop(ip, None)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        ip = request.remote_addr

        if _login_blocked(ip):
            logger.warning(f"Login rejected (too many recent failures) from {ip}")
            return render_template(
                'login.html',
                error='Too many failed attempts. Try again in a few minutes.',
            )

        username = request.form.get('username', '')
        password = request.form.get('password', '')

        exp_user = os.getenv('UI_USERNAME', 'admin')
        exp_pass = os.getenv('UI_PASSWORD', '')

        # Require a non-empty password to be configured; timing-safe compare
        ok = (
            bool(exp_pass) and
            hmac.compare_digest(username, exp_user) and
            hmac.compare_digest(password, exp_pass)
        )

        if ok:
            _clear_login_failures(ip)
            session.permanent = True
            session['authenticated'] = True
            logger.info(f"Successful login from {ip}")
            return redirect(request.args.get('next') or '/')

        _record_login_failure(ip)
        logger.warning(f"Failed login attempt from {ip}")
        error = 'Invalid username or password.'

    return render_template('login.html', error=error)


@auth_bp.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect('/login')
