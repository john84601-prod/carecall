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


def activate_schedule(schedule):
    _register_cron_job(schedule)


def deactivate_schedule(schedule_id):
    job_id = f"sched_{schedule_id}"
    if _scheduler and _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info(f"Removed cron job {job_id}")


# ── Reminder calls ─────────────────────────────────────────────────────────────

def _fire_reminder(schedule_id):
    """Scheduler entry point — creates a ReminderSession and fires the first attempt."""
    with _app.app_context():
        from carecall.models import Schedule, ReminderSession, db

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
        from carecall.twilio_client import make_call
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

        base = get_public_url()
        try:
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
            )
            log.call_sid = sid
            logger.info(
                f"Reminder call: session={session_id} attempt={session.current_attempt} sid={sid}"
            )
        except Exception as e:
            logger.error(f"Reminder call failed to initiate for session {session_id}: {e}")
            log.status = 'failed'
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
        if not session or session.status in ('reached_human', 'left_voicemail', 'failed'):
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

        session = WellnessSession(schedule_id=schedule_id, client_id=schedule.client_id)
        db.session.add(session)
        db.session.commit()

        _attempt_wellness_call(session.id)


def _attempt_wellness_call(session_id):
    with _app.app_context():
        from carecall.models import WellnessSession, CallLog, db
        from carecall.twilio_client import make_call
        from carecall.tunnel import get_public_url

        session = db.session.get(WellnessSession, session_id)
        if not session:
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

        base = get_public_url()
        try:
            sid = make_call(
                session.client.phone,
                answer_url=f"{base}/webhook/wellness-answer?session_id={session_id}&log_id={log.id}",
                status_callback_url=f"{base}/webhook/call-status?session_id={session_id}&log_id={log.id}&call_type=wellness",
            )
            log.call_sid = sid
        except Exception as e:
            log.status = 'failed'
            log.notes = str(e)
            logger.error(f"Wellness call failed for session {session_id}: {e}")
            session.status = 'pending'
            _schedule_wellness_retry(session_id, session.schedule.attempt_interval_minutes)

        db.session.commit()


def handle_wellness_no_response(session_id):
    """Called (in background thread) when a wellness call ends without acknowledgment."""
    with _app.app_context():
        from carecall.models import WellnessSession, db

        session = db.session.get(WellnessSession, session_id)
        if not session or session.status in ('acknowledged', 'escalating', 'escalated', 'failed'):
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
            _call_next_emergency_contact(session_id)


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
        from carecall.models import WellnessSession, EmergencyContact, CallLog, db
        from carecall.twilio_client import make_call
        from carecall.tunnel import get_public_url

        session = db.session.get(WellnessSession, session_id)
        if not session:
            return

        already_called = session.get_contacts_called()
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
            )
            log.call_sid = sid
            logger.info(f"Session {session_id}: calling emergency contact {next_contact.name} ({next_contact.phone})")
        except Exception as e:
            log.status = 'failed'
            log.notes = str(e)
            logger.error(f"Emergency call to {next_contact.id} failed: {e}")
            _schedule_next_emergency(session_id, 2)

        db.session.commit()


def handle_emergency_no_response(session_id, contact_id):
    """Called (in background thread) when emergency contact call ends without acknowledgment."""
    with _app.app_context():
        from carecall.models import WellnessSession, db

        session = db.session.get(WellnessSession, session_id)
        if not session or session.emergency_acknowledged:
            return

        logger.info(f"Session {session_id}: emergency contact {contact_id} did not acknowledge — trying next")
        _schedule_next_emergency(session_id, 2)


def _schedule_next_emergency(session_id, delay_minutes):
    job_id = f"emergency_next_{session_id}_{int(datetime.now().timestamp())}"
    _scheduler.add_job(
        func=_call_next_emergency_contact,
        trigger='date',
        run_date=datetime.now() + timedelta(minutes=delay_minutes),
        args=[session_id],
        id=job_id,
    )
