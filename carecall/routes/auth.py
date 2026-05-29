import hmac
import logging
import os

from flask import Blueprint, redirect, render_template, request, session

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
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
            session.permanent = True
            session['authenticated'] = True
            logger.info(f"Successful login from {request.remote_addr}")
            return redirect(request.args.get('next') or '/')

        logger.warning(f"Failed login attempt from {request.remote_addr}")
        error = 'Invalid username or password.'

    return render_template('login.html', error=error)


@auth_bp.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect('/login')
