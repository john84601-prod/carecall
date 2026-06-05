import os
from datetime import datetime, timedelta

from flask import Flask, redirect, request, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix

db = SQLAlchemy()


def create_app():
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static'),
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates'),
    )

    app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-key-change-this')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'carecall.db'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'uploads'
    )
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload limit
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
    app.config['START_TIME'] = datetime.now()

    # Trust X-Forwarded-Proto / X-Forwarded-Host from ngrok so that
    # request.url reflects the public HTTPS URL (required for Twilio signature validation).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    db.init_app(app)

    from carecall.routes.api import api_bp
    from carecall.routes.auth import auth_bp
    from carecall.routes.webhooks import webhooks_bp
    from carecall.routes.ui import ui_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(webhooks_bp, url_prefix='/webhook')
    app.register_blueprint(ui_bp)

    # ── Login guard ───────────────────────────────────────────────────────
    # Exempt: static assets, webhook callbacks (Twilio), audio uploads (Twilio fetches
    # MP3s directly), and the login/logout pages.
    _EXEMPT = ('/static/', '/webhook/', '/uploads/', '/login', '/logout')

    @app.before_request
    def _require_login():
        if any(request.path.startswith(p) for p in _EXEMPT):
            return
        if not session.get('authenticated'):
            return redirect(f'/login?next={request.path}')

    with app.app_context():
        db.create_all()
        _migrate_client_name_split()
        _migrate_client_name_nullable()
        _migrate_client_address_fields()
        _migrate_client_birthday()
        _migrate_call_log_reminder_session()
        _migrate_emergency_contact_can_text()
        _migrate_wellness_session_acknowledged_by()
        _migrate_inbound_messages()
        _migrate_audio_files_register(app)

    return app


def _migrate_inbound_messages():
    """Create inbound_messages table if it doesn't exist yet."""
    from sqlalchemy import text, inspect
    engine = db.engine
    if 'inbound_messages' not in inspect(engine).get_table_names():
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE inbound_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_sid VARCHAR(50) DEFAULT '',
                    recording_sid VARCHAR(50) DEFAULT '',
                    from_number VARCHAR(20) DEFAULT '',
                    duration_seconds INTEGER DEFAULT 0,
                    received_at DATETIME,
                    listened BOOLEAN NOT NULL DEFAULT 0,
                    notes TEXT DEFAULT '',
                    matched_client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL
                )
            """))
            conn.commit()


def _migrate_audio_files_register(app):
    """Register any existing MP3 files in uploads/ that are not yet in the audio_files table."""
    from carecall.models import AudioFile
    upload_folder = app.config['UPLOAD_FOLDER']
    if not os.path.isdir(upload_folder):
        return
    try:
        existing = {af.filename for af in AudioFile.query.all()}
        added = False
        for fname in os.listdir(upload_folder):
            if fname.lower().endswith('.mp3') and fname not in existing:
                display = fname.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ').title()
                db.session.add(AudioFile(filename=fname, display_name=display, client_id=None))
                added = True
        if added:
            db.session.commit()
    except Exception:
        db.session.rollback()


def _migrate_emergency_contact_can_text():
    """Add can_text column to emergency_contacts if it doesn't exist yet."""
    from sqlalchemy import text, inspect
    engine = db.engine
    existing = [c['name'] for c in inspect(engine).get_columns('emergency_contacts')]
    if 'can_text' not in existing:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE emergency_contacts ADD COLUMN can_text BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()


def _migrate_wellness_session_acknowledged_by():
    """Add acknowledged_by_contact_id column to wellness_sessions if it doesn't exist yet."""
    from sqlalchemy import text, inspect
    engine = db.engine
    existing = [c['name'] for c in inspect(engine).get_columns('wellness_sessions')]
    if 'acknowledged_by_contact_id' not in existing:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE wellness_sessions ADD COLUMN acknowledged_by_contact_id INTEGER "
                "REFERENCES emergency_contacts(id)"
            ))
            conn.commit()


def _migrate_call_log_reminder_session():
    """Add reminder_session_id column to call_logs if it doesn't exist yet."""
    from sqlalchemy import text, inspect
    engine = db.engine
    existing = [c['name'] for c in inspect(engine).get_columns('call_logs')]
    if 'reminder_session_id' not in existing:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE call_logs ADD COLUMN reminder_session_id INTEGER "
                "REFERENCES reminder_sessions(id)"
            ))
            conn.commit()


def _migrate_client_birthday():
    """Add birthday column if it doesn't exist yet."""
    from sqlalchemy import text, inspect
    engine = db.engine
    existing = [c['name'] for c in inspect(engine).get_columns('clients')]
    if 'birthday' not in existing:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE clients ADD COLUMN birthday DATE"))
            conn.commit()


def _migrate_client_address_fields():
    """Add address columns if they don't exist yet."""
    from sqlalchemy import text, inspect
    engine = db.engine
    existing = [c['name'] for c in inspect(engine).get_columns('clients')]
    new_cols = {
        'address1': "VARCHAR(100) NOT NULL DEFAULT ''",
        'address2': "VARCHAR(100) NOT NULL DEFAULT ''",
        'city':     "VARCHAR(60)  NOT NULL DEFAULT ''",
        'state':    "VARCHAR(2)   NOT NULL DEFAULT ''",
        'zip_code': "VARCHAR(10)  NOT NULL DEFAULT ''",
    }
    with engine.connect() as conn:
        for col, definition in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE clients ADD COLUMN {col} {definition}"))
        conn.commit()


def _migrate_client_name_nullable():
    """Make the legacy 'name' column nullable so INSERTs that omit it don't fail.

    Databases restored from before the first_name/last_name split may still have
    'name VARCHAR(100) NOT NULL'. SQLite can't ALTER COLUMN constraints directly,
    so we patch the stored schema via writable_schema.
    """
    from sqlalchemy import text, inspect
    engine = db.engine
    cols = {c['name']: c for c in inspect(engine).get_columns('clients')}
    if 'name' not in cols or cols['name']['nullable']:
        return  # column gone or already nullable — nothing to do
    with engine.connect() as conn:
        conn.execute(text("PRAGMA writable_schema = ON"))
        conn.execute(text(
            "UPDATE sqlite_master "
            "SET sql = REPLACE(sql, 'name VARCHAR(100) NOT NULL', 'name VARCHAR(100)') "
            "WHERE type = 'table' AND name = 'clients'"
        ))
        conn.execute(text("PRAGMA writable_schema = OFF"))
        conn.execute(text("VACUUM"))
        conn.commit()


def _migrate_client_name_split():
    """One-time migration: add first_name/last_name columns and populate from legacy name column."""
    from sqlalchemy import text, inspect
    engine = db.engine
    existing = [c['name'] for c in inspect(engine).get_columns('clients')]

    if 'first_name' in existing:
        return  # already migrated

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE clients ADD COLUMN first_name VARCHAR(60) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE clients ADD COLUMN last_name  VARCHAR(60) NOT NULL DEFAULT ''"))

        # Split legacy `name` on the first space
        conn.execute(text("""
            UPDATE clients SET
                first_name = CASE
                    WHEN INSTR(name, ' ') > 0 THEN SUBSTR(name, 1, INSTR(name, ' ') - 1)
                    ELSE name
                END,
                last_name = CASE
                    WHEN INSTR(name, ' ') > 0 THEN SUBSTR(name, INSTR(name, ' ') + 1)
                    ELSE ''
                END
        """))
        conn.commit()
