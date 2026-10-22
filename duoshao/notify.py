"""
Central place for "something happened, tell someone" logic.

notify_client()  -> creates the in-app Notification (bell icon in the client portal)
                     AND emails the client, if we have a usable email address and
                     email is configured. Never raises — a failed email should never
                     break the request that triggered it.

notify_staff()   -> emails STAFF_NOTIFICATION_EMAIL (if set) plus any admin/worker
                     User with their own `email` set, for things that need a
                     worker/admin to go do something (new order, new sourcing
                     request, a client accepted or declined a quotation, etc).
"""
from flask import current_app, url_for
from .models import db, User, Notification
from .email_utils import send_email, mail_enabled


def notify_client(user, message, link=None, email_subject=None, email_body=None):
    if not user:
        return
    note = Notification(user_id=user.id, message=message, link=link)
    db.session.add(note)

    if mail_enabled() and user.notify_email:
        subject = email_subject or f"Duoshao Trading — {message}"
        body = email_body or (
            f"Hi {user.full_name},\n\n{message}\n\n"
            + (f"View it here: {link}\n\n" if link else "")
            + "Duoshao Trading Co., Ltd."
        )
        send_email(user.notify_email, subject, body)


def notify_staff(subject, body):
    if not mail_enabled():
        return

    recipients = set()
    staff_default = current_app.config.get("STAFF_NOTIFICATION_EMAIL")
    if staff_default:
        for addr in staff_default.split(","):
            addr = addr.strip()
            if addr:
                recipients.add(addr)

    for u in User.query.filter(User.role.in_(["admin", "worker"]), User.email.isnot(None)).all():
        recipients.add(u.email)

    for addr in recipients:
        send_email(addr, subject, body)
