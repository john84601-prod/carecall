import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logger = logging.getLogger(__name__)

_scheduler = None
_app = None


def init_scheduler(app):
    global _scheduler, _app
    _app = app

    jobs_db = 'sqlite:///' + os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'carecall_jobs.db'
    )
    _scheduler = BackgroundScheduler(
        jobstores={'default': SQLAlchemyJobStore(url=jobs_db)},
        job_defaults={'misfire_grace_time': 300},
    )
    _scheduler.start()

    with app.app_context():
        _load_all_schedule_jobs()

    _register_midnight_rollover()
    _startup_missed_call_check()
    _register_sms_webhook()
    _init_backup_job()
    _register_recording_cleanup_job()
    logger.info("Scheduler started and jobs loaded")


# ── Schedule job management ────────────────────────────────────────────────────

def _load_all_schedule_jobs():
    from carecall.models import Schedule
    for schedule in Schedule.query.filter_by(active=True).all():
        _register_cron_job(schedule)


def _register_cron_job(schedule):
    hour, minute = schedule.time_of_day.split(':')
    func = _fire_reminder if schedule.call_type == 'reminder' else _fire_wellness_check
    job_id = f"sched_{schedule.id}"

    _scheduler.add_job(
        func=func,
        trigger='cron',
        hour=int(hour),
        minute=int(minute),
        day_of_week=schedule.days_of_week,
        args=[schedule.id],
        id=job_id,
        replace_existing=True,
    )
    logger.info(f"Registered cron job {job_id}: {schedule.call_type} at {schedule.time_of_day}")


def is_scheduler_running():
    """Return True if the APScheduler background thread is alive and running."""
    return _scheduler is not None and _scheduler.running


def activate_schedule(schedule):
    _register_cron_job(schedule)


def deactivate_schedule(schedule_id):
    job_id = f"sched_{schedule_id}"
    if _scheduler and _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info(f"Removed cron job {job_id}")


# ── Midnight rollover ─────────────────────────────────────────────────────────

def _register_sms_webhook():
    """Point the Twilio number's SMS webhook at our /webhook/sms-reply route."""
    try:
        from carecall.tunnel import get_public_url
        from carecall.voice_client import register_sms_webhook
        url = get_public_url()
        register_sms_webhook(f"{url}/webhook/sms-reply")
    except Exception as e:
        logger.warning(f"Could not register SMS webhook at startup (non-fatal): {e}")


def _register_midnight_rollover():
    """Register a daily cron job that closes any still-active wellness sessions at midnight."""
    _scheduler.add_job(
        func=_rollover_active_sessions,
        trigger='cron',
        hour=0,
        minute=0,
        id='midnight_rollover',
        replace_existing=True,
    )
    logger.info("Midnight rollover job registered")


def _rollover_active_sessions():
    """Close all in-progress wellness sessions at midnight with status=failed."""
    with _app.app_context():
        from carecall.models import WellnessSession, db
        active = WellnessSession.query.filter(
            WellnessSession.status.in_(['pending', 'calling', 'escalating'])
        ).all()
        now = datetime.utcnow()
        for session in active:
            session.status = 'failed'
            session.resolved_at = now
            _cancel_session_jobs(session.id)   # remove any queued retry/escalation jobs
        if active:
            db.session.commit()
            logger.info(f"Midnight rollover: closed {len(active)} active wellness session(s)")


def _cancel_session_jobs(session_id):
    """Remove all pending APScheduler jobs associated with a wellness session."""
    if not _scheduler:
        return
    prefixes = (
        f"wellness_retry_{session_id}_",
        f"emergency_next_{session_id}_",
    )
    for job in list(_scheduler.get_jobs()):
        if any(job.id.startswith(p) for p in prefixes):
            try:
                job.remove()
                logger.info(f"Midnight rollover: cancelled queued job {job.id}")
            except Exception:
                pass


# ── Startup missed-call audit ─────────────────────────────────────────────────

def _startup_missed_call_check():
    """On startup, create a 'system_down' CallLog entry for every schedule that
    should have fired today but has no session or call log record at all.

    This covers the case where the Pi was off, the service crashed, or systemd
    hadn't started yet when the scheduled time passed.  Staff can then review
    Today's Calls and manually follow up with any flagged clients.
    """
    with _app.app_context():
        from datetime import date as _date, datetime as _dt, time as _t
        from carecall.models import Schedule, WellnessSession, ReminderSession, CallLog, db

        now_local   = _dt.now()
        today       = now_local.date()
        utc_offset  = _dt.utcnow() - now_local          # timedelta: how far ahead UTC is
        today_start = _dt.combine(today, _t.min) + utc_offset  # local midnight as UTC

        # APScheduler and Python weekday() both use 0=Mon … 6=Sun
        today_dow = today.weekday()

        missed = 0
        for schedule in Schedule.query.filter_by(active=True).all():
            if not schedule.client or not schedule.client.active:
                continue

            # Does this schedule run today?
            try:
                sched_days = [int(d) for d in schedule.days_of_week.split(',')]
                sched_h, sched_m = map(int, schedule.time_of_day.split(':'))
            except (ValueError, AttributeError):
                continue
            if today_dow not in sched_days:
                continue

            # Has the scheduled time already passed?
            sched_local = _dt.combine(today, _t(sched_h, sched_m))
            if sched_local >= now_local:
                continue  # hasn't fired yet today — nothing missed

            # Is there already any session or log for this schedule today?
            if schedule.call_type == 'wellness':
                has_record = WellnessSession.query.filter(
                    WellnessSession.schedule_id == schedule.id,
                    WellnessSession.started_at  >= today_start,
                ).first()
            else:
                has_record = ReminderSession.query.filter(
                    ReminderSession.schedule_id == schedule.id,
                    ReminderSession.started_at  >= today_start,
                ).first()

            if not has_record:
                # Also check for an existing call log (e.g. from a previous startup)
                has_record = CallLog.query.filter(
                    CallLog.schedule_id == schedule.id,
                    CallLog.timestamp   >= today_start,
                ).first()

            if has_record:
                continue  # already recorded — nothing to do

            # Create a system_down entry timestamped at the missed scheduled time
            sched_utc = sched_local + utc_offset
            db.session.add(CallLog(
                schedule_id    = schedule.id,
                client_id      = schedule.client_id,
                call_type      = schedule.call_type,
                attempt_number = 1,
                status         = 'system_down',
                timestamp      = sched_utc,
                notes          = 'Scheduled call missed — CareCall service was not running.',
            ))
            missed += 1
            logger.warning(
                f"Startup audit: system_down logged for {schedule.call_type} "
                f"schedule {schedule.id} (client {schedule.client_id}) "
                f"missed at {schedule.time_of_day}"
            )

        if missed:
            db.session.commit()
            logger.warning(f"Startup audit: {missed} missed call(s) logged as system_down")
        else:
            logger.info("Startup audit: no missed calls detected")


def _classify_call_error(exc, base_url_obtained):
    """Return 'internet_down' for network-level failures, 'failed' for everything else.

    base_url_obtained=False means get_public_url() itself failed, which is also
    a connectivity problem (ngrok couldn't reach the internet).
    """
    if not base_url_obtained:
        return 'internet_down'
    try:
        import requests as _r
        if isinstance(exc, (_r.exceptions.ConnectionError, _r.exceptions.Timeout)):
            return 'internet_down'
    except ImportError:
        pass
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError)):
        return 'internet_down'
    return 'failed'


# ── Recording helpers ─────────────────────────────────────────────────────────

def _should_record(client):
    """Return True if this client's calls should be recorded."""
    from carecall.routes.api import _load_system_config
    try:
        cfg = _load_system_config()
    except Exception:
        cfg = {}
    return cfg.get('record_all_calls', False) or bool(getattr(client, 'record_calls', False))


def _register_recording_cleanup_job():
    _scheduler.add_job(
        func=_purge_old_recordings,
        trigger='cron',
        hour=3,
        minute=0,
        id='recording_cleanup',
        replace_existing=True,
    )
    logger.info("Recording cleanup job registered (daily at 03:00)")


def _purge_old_recordings():
    """Delete Twilio recordings older than recording_retention_days and clear their SIDs."""
    with _app.app_context():
        import os as _os
        from carecall.models import CallLog, db
        from carecall.routes.api import _load_system_config

        cfg  = _load_system_config()
        days = int(cfg.get('recording_retention_days', 7))
        cutoff = datetime.utcnow() - timedelta(days=days)

        logs = CallLog.query.filter(
            CallLog.recording_sid.isnot(None),
            CallLog.timestamp < cutoff,
        ).all()

        if not logs:
            logger.info(f"Recording cleanup: no recordings older than {days} days")
            return

        try:
            from carecall.voice_client import get_client
            tw = get_client()
        except Exception as e:
            logger.error(f"Recording cleanup: could not init voice provider client: {e}")
            return

        deleted = 0
        for log in logs:
            try:
                tw.recordings(log.recording_sid).delete()
            except Exception:
                pass  # already gone or inaccessible — still clear the SID
            log.recording_sid      = None
            log.recording_duration = None
            deleted += 1

        db.session.commit()
        logger.info(f"Recording cleanup: purged {deleted} recording(s) older than {days} days")


# ── Reminder calls ─────────────────────────────────────────────────────────────

def _fire_reminder(schedule_id):
    """Scheduler entry point — creates a ReminderSession and fires the first attempt."""
    with _app.app_context():
        from carecall.models import Schedule, ReminderSession, db
        from carecall.routes.api import _load_system_config
        if _load_system_config().get('calls_paused', False):
            logger.info(f"Calls paused — skipping reminder for schedule {schedule_id}")
            return

        schedule = db.session.get(Schedule, schedule_id)
        if not schedule or not schedule.active or not schedule.client.active:
            return

        # Skip if a session for this schedule is already in progress
        in_progress = ReminderSession.query.filter(
            ReminderSession.schedule_id == schedule_id,
            ReminderSession.status.in_(['pending', 'calling']),
        ).first()
        if in_progress:
            logger.info(f"Reminder session {in_progress.id} still active for schedule {schedule_id} — skipping")
            return

        session = ReminderSession(schedule_id=schedule_id, client_id=schedule.client_id)
        db.session.add(session)
        db.session.commit()
        _attempt_reminder_call(session.id)


def _attempt_reminder_call(session_id):
    """Make one reminder call attempt with AMD so we know if a human or voicemail answered."""
    with _app.app_context():
        from carecall.models import ReminderSession, CallLog, db
        from carecall.voice_client import make_call
        from carecall.tunnel import get_public_url

        session = db.session.get(ReminderSession, session_id)
        if not session:
            return

        session.current_attempt += 1
        session.status = 'calling'

        log = CallLog(
            schedule_id=session.schedule_id,
            client_id=session.client_id,
            reminder_session_id=session_id,
            call_type='reminder',
            attempt_number=session.current_attempt,
            status='initiated',
        )
        db.session.add(log)
        db.session.flush()

        base = None
        try:
            base = get_public_url()
            _record = _should_record(session.client)
            sid = make_call(
                session.client.phone,
                answer_url=(
                    f"{base}/webhook/reminder-answer"
                    f"?session_id={session_id}&log_id={log.id}"
                ),
                status_callback_url=(
                    f"{base}/webhook/call-status"
                    f"?call_type=reminder&session_id={session_id}&log_id={log.id}"
                ),
                machine_detection=True,
                record=_record,
                recording_status_callback=(
                    f"{base}/webhook/call-recording?log_id={log.id}" if _record else None
                ),
            )
            log.call_sid = sid
            logger.info(
                f"Reminder call: session={session_id} attempt={session.current_attempt} sid={sid}"
            )
        except Exception as e:
            err_status = _classify_call_error(e, base is not None)
            logger.error(f"Reminder call {err_status} for session {session_id}: {e}")
            log.status = err_status
            log.notes = str(e)
            session.status = 'pending'
            db.session.commit()
            handle_reminder_no_response(session_id)
            return

        db.session.commit()


def handle_reminder_no_response(session_id):
    """Called (in background thread) when a reminder call ends without an answer.
    Retries up to max_attempts (capped at 20), then marks the session failed.
    """
    with _app.app_context():
        from carecall.models import ReminderSession, db

        session = db.session.get(ReminderSession, session_id)
        if not session or session.status in ('reached_human', 'left_voicemail', 'failed', 'cancelled'):
            return  # Already resolved — nothing to do

        schedule = session.schedule
        if not schedule:
            session.status = 'failed'
            session.resolved_at = datetime.utcnow()
            db.session.commit()
            return

        max_att = min(schedule.max_attempts or 3, 20)
        if session.current_attempt < max_att:
            delay = schedule.attempt_interval_minutes or int(
                os.getenv('DEFAULT_ATTEMPT_INTERVAL_MINUTES', 10)
            )
            session.status = 'pending'
            db.session.commit()
            _schedule_reminder_retry(session_id, delay)
            logger.info(
                f"Reminder session {session_id}: attempt {session.current_attempt}/{max_att} "
                f"unanswered — retrying in {delay}m"
            )
        else:
            session.status = 'failed'
            session.resolved_at = datetime.utcnow()
            db.session.commit()
            logger.info(
                f"Reminder session {session_id}: max attempts ({max_att}) reached — session failed"
            )


def _schedule_reminder_retry(session_id, delay_minutes):
    job_id = f"reminder_retry_{session_id}"
    _scheduler.add_job(
        func=_attempt_reminder_call,
        trigger='date',
        run_date=datetime.now() + timedelta(minutes=delay_minutes),
        args=[session_id],
        id=job_id,
        replace_existing=True,
    )
    logger.info(f"Reminder session {session_id}: retry job scheduled in {delay_minutes}m")


# ── Wellness check calls ───────────────────────────────────────────────────────

def _fire_wellness_check(schedule_id):
    with _app.app_context():
        from carecall.models import Schedule, WellnessSession, db
        from carecall.routes.api import _load_system_config
        if _load_system_config().get('calls_paused', False):
            logger.info(f"Calls paused — skipping wellness check for schedule {schedule_id}")
            return

        schedule = db.session.get(Schedule, schedule_id)
        if not schedule or not schedule.active or not schedule.client.active:
            return

        # Skip if a session for this schedule is still in progress
        in_progress = WellnessSession.query.filter(
            WellnessSession.schedule_id == schedule_id,
            WellnessSession.status.in_(['pending', 'calling', 'escalating']),
        ).first()
        if in_progress:
            logger.info(f"Session {in_progress.id} still active for schedule {schedule_id} — skipping")
            return

        # Skip if today falls within a wellness blackout for this client
        from datetime import date as _date
        from carecall.models import WellnessBlackout
        _today = _date.today()
        blackout = WellnessBlackout.query.filter(
            WellnessBlackout.client_id == schedule.client_id,
            WellnessBlackout.start_date <= _today,
            WellnessBlackout.end_date   >= _today,
        ).first()
        if blackout:
            _note = f' ({blackout.note})' if blackout.note else ''
            logger.info(
                f"Wellness check for client {schedule.client_id} skipped "
                f"— blackout {blackout.start_date} to {blackout.end_date}{_note}"
            )
            return

        session = WellnessSession(schedule_id=schedule_id, client_id=schedule.client_id)
        db.session.add(session)
        db.session.commit()

        _attempt_wellness_call(session.id)


def _attempt_wellness_call(session_id):
    with _app.app_context():
        from carecall.models import WellnessSession, CallLog, db
        from carecall.voice_client import make_call
        from carecall.tunnel import get_public_url

        session = db.session.get(WellnessSession, session_id)
        if not session or session.status in ('cancelled', 'failed'):
            return

        session.current_attempt += 1
        session.status = 'calling'

        log = CallLog(
            schedule_id=session.schedule_id,
            client_id=session.client_id,
            wellness_session_id=session_id,
            call_type='wellness',
            attempt_number=session.current_attempt,
            status='initiated',
        )
        db.session.add(log)
        db.session.flush()

        base = None
        try:
            base = get_public_url()
            _record = _should_record(session.client)
            sid = make_call(
                session.client.phone,
                answer_url=f"{base}/webhook/wellness-answer?session_id={session_id}&log_id={log.id}",
                status_callback_url=f"{base}/webhook/call-status?session_id={session_id}&log_id={log.id}&call_type=wellness",
                machine_detection=True,
                amd_status_callback_url=f"{base}/webhook/wellness-amd-result?session_id={session_id}&log_id={log.id}",
                record=_record,
                recording_status_callback=(
                    f"{base}/webhook/call-recording?log_id={log.id}" if _record else None
                ),
            )
            log.call_sid = sid
        except Exception as e:
            err_status = _classify_call_error(e, base is not None)
            logger.error(f"Wellness call {err_status} for session {session_id}: {e}")
            log.status = err_status
            log.notes = str(e)
            session.status = 'pending'
            _schedule_wellness_retry(session_id, session.schedule.attempt_interval_minutes)

        db.session.commit()


def handle_wellness_no_response(session_id):
    """Called (in background thread) when a wellness call ends without acknowledgment."""
    with _app.app_context():
        from carecall.models import WellnessSession, db

        session = db.session.get(WellnessSession, session_id)
        if not session or session.status in ('acknowledged', 'escalating', 'escalated', 'failed', 'cancelled'):
            return

        schedule = session.schedule
        if session.current_attempt < schedule.max_attempts:
            session.status = 'pending'
            db.session.commit()
            _schedule_wellness_retry(session_id, schedule.attempt_interval_minutes)
        else:
            session.status = 'escalating'
            db.session.commit()
            logger.info(f"Session {session_id}: max attempts reached — escalating to emergency contacts")
            # Small delay before dialing — placing this call immediately,
            # in the same instant as the just-completed wellness call's
            # final status callback, can trip the voice provider's
            # outbound call rate limit (observed: SignalWire "Exceeded
            # Outbound Call Rate").
            _scheduler.add_job(
                func=_call_next_emergency_contact,
                trigger='date',
                run_date=datetime.now() + timedelta(seconds=5),
                args=[session_id],
                id=f"emergency_start_{session_id}_{int(datetime.now().timestamp())}",
            )


def _schedule_wellness_retry(session_id, delay_minutes):
    job_id = f"wellness_retry_{session_id}_{int(datetime.now().timestamp())}"
    _scheduler.add_job(
        func=_attempt_wellness_call,
        trigger='date',
        run_date=datetime.now() + timedelta(minutes=delay_minutes),
        args=[session_id],
        id=job_id,
    )
    logger.info(f"Session {session_id}: retry scheduled in {delay_minutes} min")


# ── Emergency escalation ───────────────────────────────────────────────────────

def _call_next_emergency_contact(session_id):
    with _app.app_context():
        from carecall.models import WellnessSession, EmergencyContact, ScheduleContact, CallLog, db
        from carecall.voice_client import make_call
        from carecall.tunnel import get_public_url

        session = db.session.get(WellnessSession, session_id)
        if not session or session.status in ('cancelled', 'failed'):
            return

        already_called = session.get_contacts_called()

        # Use the schedule's own ordered contact list if configured; fall back
        # to all client contacts (old behaviour) so existing sessions still work.
        sched_contacts = (ScheduleContact.query
                          .filter_by(schedule_id=session.schedule_id)
                          .order_by(ScheduleContact.priority)
                          .all())
        if sched_contacts:
            next_sc = next(
                (sc for sc in sched_contacts if sc.emergency_contact_id not in already_called),
                None,
            )
            next_contact = next_sc.contact if next_sc else None
        else:
            query = EmergencyContact.query.filter_by(client_id=session.client_id)
            if already_called:
                query = query.filter(EmergencyContact.id.notin_(already_called))
            next_contact = query.order_by(EmergencyContact.priority).first()

        if not next_contact:
            session.status = 'failed'
            session.resolved_at = datetime.utcnow()
            db.session.commit()
            logger.warning(f"Session {session_id}: all emergency contacts exhausted — session FAILED")
            return

        session.add_contact_called(next_contact.id)

        log = CallLog(
            schedule_id=session.schedule_id,
            client_id=session.client_id,
            wellness_session_id=session_id,
            emergency_contact_id=next_contact.id,
            call_type='emergency',
            attempt_number=1,
            status='initiated',
        )
        db.session.add(log)
        db.session.flush()

        base = get_public_url()
        _record = _should_record(session.client)
        try:
            sid = make_call(
                next_contact.phone,
                answer_url=(
                    f"{base}/webhook/emergency-answer"
                    f"?session_id={session_id}&contact_id={next_contact.id}&log_id={log.id}"
                ),
                status_callback_url=(
                    f"{base}/webhook/call-status"
                    f"?session_id={session_id}&log_id={log.id}&call_type=emergency&contact_id={next_contact.id}"
                ),
                machine_detection=True,
                record=_record,
                recording_status_callback=(
                    f"{base}/webhook/call-recording?log_id={log.id}" if _record else None
                ),
            )
            log.call_sid = sid
            logger.info(f"Session {session_id}: calling emergency contact {next_contact.name} ({next_contact.phone})")
        except Exception as e:
            log.status = 'failed'
            log.notes = str(e)
            logger.error(f"Emergency call to {next_contact.id} failed: {e}")
            _schedule_next_emergency(session_id, _emergency_interval())

        # Also send an SMS if this contact has can_text enabled
        if next_contact.can_text:
            try:
                from carecall.voice_client import send_sms
                client_name = session.client.full_name if session.client else 'Your client'
                sms_body = (
                    f"URGENT – CareCall Alert: {client_name} has not responded to "
                    f"{session.current_attempt} wellness check call(s). "
                    f"Reply OK to acknowledge you will follow up. "
                    f"Reply STOP to unsubscribe."
                )
                send_sms(next_contact.phone, sms_body)
                logger.info(f"Session {session_id}: SMS alert sent to {next_contact.name}")
            except Exception as sms_err:
                logger.warning(f"Session {session_id}: SMS to {next_contact.name} failed (non-fatal): {sms_err}")

        db.session.commit()


def handle_emergency_no_response(session_id, contact_id):
    """Called when an emergency contact call ends without acknowledgment.

    Calls all emergency contacts in order with no wait between them.
    After the last contact in the list has been tried, waits
    EMERGENCY_CONTACT_INTERVAL_MINUTES then resets and repeats the cycle.
    """
    with _app.app_context():
        from carecall.models import WellnessSession, EmergencyContact, ScheduleContact, db

        session = db.session.get(WellnessSession, session_id)
        if not session or session.emergency_acknowledged or session.status in ('cancelled', 'failed'):
            return

        already_called = set(session.get_contacts_called())

        # Build the full ordered contact list for this schedule/client
        sched_contacts = (ScheduleContact.query
                          .filter_by(schedule_id=session.schedule_id)
                          .order_by(ScheduleContact.priority).all())
        if sched_contacts:
            all_contact_ids = [sc.emergency_contact_id for sc in sched_contacts]
        else:
            all_contacts = (EmergencyContact.query
                            .filter_by(client_id=session.client_id)
                            .order_by(EmergencyContact.priority).all())
            all_contact_ids = [c.id for c in all_contacts]

        remaining = [cid for cid in all_contact_ids if cid not in already_called]

        if remaining:
            # More contacts left in this round — call the next one immediately
            logger.info(
                f"Session {session_id}: EC {contact_id} no response — "
                f"calling next EC immediately ({len(remaining)} remaining in round)"
            )
            _call_next_emergency_contact(session_id)
        else:
            # All contacts called this round — wait interval, then restart cycle
            interval = _emergency_interval()
            logger.info(
                f"Session {session_id}: all ECs tried with no acknowledgment — "
                f"waiting {interval}m then restarting cycle"
            )
            session.emergency_contacts_called = '[]'
            db.session.commit()
            _schedule_next_emergency(session_id, interval)


def _emergency_interval():
    """Minutes to wait between emergency contact calls (from env, default 5)."""
    return int(os.getenv('EMERGENCY_CONTACT_INTERVAL_MINUTES', 5))


def _schedule_next_emergency(session_id, delay_minutes):
    job_id = f"emergency_next_{session_id}_{int(datetime.now().timestamp())}"
    _scheduler.add_job(
        func=_call_next_emergency_contact,
        trigger='date',
        run_date=datetime.now() + timedelta(minutes=delay_minutes),
        args=[session_id],
        id=job_id,
    )


# ── Scheduled Backup ──────────────────────────────────────────────────────────

def _init_backup_job():
    """Load backup schedule from backup_config.json on startup."""
    import json as _json
    root = os.path.dirname(os.path.dirname(__file__))
    config_path = os.path.join(root, 'backup_config.json')
    try:
        with open(config_path) as f:
            config = _json.load(f)
        update_backup_job(config)
    except FileNotFoundError:
        pass  # No config yet — no job to register
    except Exception as e:
        logger.warning(f"Could not init backup job: {e}")


def update_backup_job(config):
    """Register or remove the APScheduler cron job for scheduled backups."""
    if not _scheduler:
        return
    job_id = 'scheduled_backup'
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass

    if not config.get('enabled') or not config.get('destination'):
        return

    try:
        hour, minute = str(config.get('time', '02:00')).split(':')
        hour, minute = int(hour), int(minute)
    except Exception:
        hour, minute = 2, 0

    freq = config.get('frequency', 'daily')
    kwargs = dict(id=job_id, replace_existing=True, misfire_grace_time=3600)

    if freq == 'weekly':
        _scheduler.add_job(
            _run_scheduled_backup, 'cron',
            day_of_week=config.get('day_of_week', 'sun'),
            hour=hour, minute=minute, **kwargs
        )
    elif freq == 'monthly':
        _scheduler.add_job(
            _run_scheduled_backup, 'cron',
            day=int(config.get('day_of_month', 1)),
            hour=hour, minute=minute, **kwargs
        )
    else:  # daily
        _scheduler.add_job(
            _run_scheduled_backup, 'cron',
            hour=hour, minute=minute, **kwargs
        )
    logger.info(f"Scheduled backup job registered: {freq} at {hour:02d}:{minute:02d}")


def _run_scheduled_backup():
    """APScheduler callback — runs the backup to the configured destination."""
    import json as _json
    root = os.path.dirname(os.path.dirname(__file__))
    config_path = os.path.join(root, 'backup_config.json')
    try:
        with open(config_path) as f:
            config = _json.load(f)
    except Exception as e:
        logger.error(f"Scheduled backup: cannot read config: {e}")
        return

    destination = config.get('destination', '')
    if not destination or not os.path.isdir(destination):
        logger.error(f"Scheduled backup: destination not found: {destination!r}")
        return

    if _app:
        with _app.app_context():
            from carecall.routes.api import _do_backup
            result = _do_backup(destination)
            logger.info(f"Scheduled backup complete: {result['filename']} ({result['size']})")
    else:
        logger.error("Scheduled backup: no Flask app context available")
