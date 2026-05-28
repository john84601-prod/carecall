from datetime import datetime
from carecall import db
import json


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(60), nullable=False, default='')
    last_name = db.Column(db.String(60), nullable=False, default='')
    phone = db.Column(db.String(20), nullable=False)
    address1 = db.Column(db.String(100), default='')
    address2 = db.Column(db.String(100), default='')
    city = db.Column(db.String(60), default='')
    state = db.Column(db.String(2), default='')
    zip_code = db.Column(db.String(10), default='')
    birthday = db.Column(db.Date, nullable=True)
    active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    emergency_contacts = db.relationship(
        'EmergencyContact', back_populates='client',
        order_by='EmergencyContact.priority', cascade='all, delete-orphan'
    )
    schedules = db.relationship('Schedule', back_populates='client', cascade='all, delete-orphan')
    call_logs = db.relationship('CallLog', back_populates='client', cascade='all, delete-orphan')
    wellness_sessions = db.relationship('WellnessSession', back_populates='client', cascade='all, delete-orphan')
    reminder_sessions = db.relationship('ReminderSession', back_populates='client', cascade='all, delete-orphan')

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def to_dict(self):
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'phone': self.phone,
            'address1': self.address1,
            'address2': self.address2,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'birthday': self.birthday.isoformat() if self.birthday else None,
            'active': self.active,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() + 'Z',
            'emergency_contacts': [c.to_dict() for c in self.emergency_contacts],
        }


class EmergencyContact(db.Model):
    __tablename__ = 'emergency_contacts'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    relationship = db.Column(db.String(50), default='')
    priority = db.Column(db.Integer, default=1)

    can_text = db.Column(db.Boolean, default=False)

    client = db.relationship('Client', back_populates='emergency_contacts')

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'name': self.name,
            'phone': self.phone,
            'relationship': self.relationship,
            'priority': self.priority,
            'can_text': self.can_text,
        }


class Schedule(db.Model):
    __tablename__ = 'schedules'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    name = db.Column(db.String(100), default='')
    # 'reminder' plays an mp3; 'wellness' requires a key press
    call_type = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, default=True)

    # Timing: HH:MM, days as comma-separated APScheduler day_of_week (0=Mon..6=Sun)
    time_of_day = db.Column(db.String(5), nullable=False)
    days_of_week = db.Column(db.String(20), default='0,1,2,3,4,5,6')

    # Reminder-specific
    mp3_filename = db.Column(db.String(255))

    # Wellness-specific
    required_keypress = db.Column(db.String(1), default='1')

    # Shared retry settings (both reminder and wellness)
    max_attempts = db.Column(db.Integer, default=3)
    attempt_interval_minutes = db.Column(db.Integer, default=10)

    client = db.relationship('Client', back_populates='schedules')
    call_logs = db.relationship('CallLog', back_populates='schedule')
    wellness_sessions = db.relationship('WellnessSession', back_populates='schedule')
    reminder_sessions = db.relationship('ReminderSession', back_populates='schedule')
    schedule_contacts = db.relationship(
        'ScheduleContact', back_populates='schedule',
        order_by='ScheduleContact.priority', cascade='all, delete-orphan'
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'client_name': self.client.full_name if self.client else '',
            'name': self.name,
            'call_type': self.call_type,
            'active': self.active,
            'time_of_day': self.time_of_day,
            'days_of_week': self.days_of_week,
            'mp3_filename': self.mp3_filename,
            'required_keypress': self.required_keypress,
            'max_attempts': self.max_attempts,
            'attempt_interval_minutes': self.attempt_interval_minutes,
            'schedule_contacts': [
                {
                    'id': sc.id,
                    'emergency_contact_id': sc.emergency_contact_id,
                    'name': sc.contact.name,
                    'phone': sc.contact.phone,
                    'relationship': sc.contact.relationship or '',
                    'can_text': sc.contact.can_text,
                    'priority': sc.priority,
                }
                for sc in self.schedule_contacts
            ],
        }


class ScheduleContact(db.Model):
    """Emergency contact assigned to a specific wellness schedule with a per-schedule call order."""
    __tablename__ = 'schedule_emergency_contacts'
    id = db.Column(db.Integer, primary_key=True)
    schedule_id          = db.Column(db.Integer, db.ForeignKey('schedules.id'),          nullable=False)
    emergency_contact_id = db.Column(db.Integer, db.ForeignKey('emergency_contacts.id'), nullable=False)
    priority = db.Column(db.Integer, default=1)

    schedule = db.relationship('Schedule', back_populates='schedule_contacts')
    contact  = db.relationship('EmergencyContact')


class WellnessSession(db.Model):
    """One triggered wellness check that may span multiple retry attempts."""
    __tablename__ = 'wellness_sessions'
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)

    # pending → calling → acknowledged | escalating → escalated | failed
    status = db.Column(db.String(30), default='pending')
    current_attempt = db.Column(db.Integer, default=0)

    # JSON list of emergency contact IDs already called
    emergency_contacts_called = db.Column(db.Text, default='[]')
    emergency_acknowledged = db.Column(db.Boolean, default=False)
    acknowledged_by_contact_id = db.Column(db.Integer, db.ForeignKey('emergency_contacts.id'), nullable=True)

    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    client = db.relationship('Client', back_populates='wellness_sessions')
    schedule = db.relationship('Schedule', back_populates='wellness_sessions')
    acknowledged_by_contact = db.relationship(
        'EmergencyContact', foreign_keys=[acknowledged_by_contact_id]
    )

    def get_contacts_called(self):
        return json.loads(self.emergency_contacts_called or '[]')

    def add_contact_called(self, contact_id):
        called = self.get_contacts_called()
        called.append(contact_id)
        self.emergency_contacts_called = json.dumps(called)

    def to_dict(self):
        ack_contact = self.acknowledged_by_contact
        return {
            'id': self.id,
            'schedule_id': self.schedule_id,
            'client_id': self.client_id,
            'client_name': self.client.full_name if self.client else '',
            'schedule_name': self.schedule.name if self.schedule else '',
            'status': self.status,
            'current_attempt': self.current_attempt,
            'emergency_acknowledged': self.emergency_acknowledged,
            'acknowledged_by_contact_id':   self.acknowledged_by_contact_id,
            'acknowledged_by_contact_name': ack_contact.name  if ack_contact else None,
            'acknowledged_by_contact_phone': ack_contact.phone if ack_contact else None,
            'started_at':  self.started_at.isoformat() + 'Z',
            'resolved_at': self.resolved_at.isoformat() + 'Z' if self.resolved_at else None,
        }


class ReminderSession(db.Model):
    """One triggered reminder call session that may span multiple retry attempts."""
    __tablename__ = 'reminder_sessions'
    id          = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'), nullable=False)
    client_id   = db.Column(db.Integer, db.ForeignKey('clients.id'),   nullable=False)

    # pending → calling → reached_human | left_voicemail | failed
    status          = db.Column(db.String(30), default='pending')
    current_attempt = db.Column(db.Integer, default=0)

    started_at  = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    client   = db.relationship('Client',   back_populates='reminder_sessions')
    schedule = db.relationship('Schedule', back_populates='reminder_sessions')

    def to_dict(self):
        return {
            'id':              self.id,
            'schedule_id':     self.schedule_id,
            'client_id':       self.client_id,
            'client_name':     self.client.full_name if self.client else '',
            'schedule_name':   self.schedule.name    if self.schedule else '',
            'status':          self.status,
            'current_attempt': self.current_attempt,
            'started_at':      self.started_at.isoformat()  + 'Z' if self.started_at  else None,
            'resolved_at':     self.resolved_at.isoformat() + 'Z' if self.resolved_at else None,
        }


class CallLog(db.Model):
    __tablename__ = 'call_logs'
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedules.id'))
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    wellness_session_id  = db.Column(db.Integer, db.ForeignKey('wellness_sessions.id'))
    reminder_session_id  = db.Column(db.Integer, db.ForeignKey('reminder_sessions.id'))
    emergency_contact_id = db.Column(db.Integer, db.ForeignKey('emergency_contacts.id'))

    call_sid = db.Column(db.String(50))
    call_type = db.Column(db.String(20))  # 'reminder', 'wellness', 'emergency'
    attempt_number = db.Column(db.Integer, default=1)
    status = db.Column(db.String(30), default='initiated')
    keypress_received = db.Column(db.String(1))

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, default='')

    client = db.relationship('Client', back_populates='call_logs')
    schedule = db.relationship('Schedule', back_populates='call_logs')

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'client_name': self.client.full_name if self.client else '',
            'schedule_id': self.schedule_id,
            'wellness_session_id':  self.wellness_session_id,
            'reminder_session_id':  self.reminder_session_id,
            'call_sid': self.call_sid,
            'call_type': self.call_type,
            'attempt_number': self.attempt_number,
            'status': self.status,
            'keypress_received': self.keypress_received,
            'timestamp': self.timestamp.isoformat() + 'Z',
            'notes': self.notes,
        }
