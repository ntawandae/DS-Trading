"""
Outbound email — used for signup verification and password reset links.

Opt-in via environment variables, same pattern as the payment gateways:

    export MAIL_SERVER="smtp.gmail.com"      # or your provider's SMTP host
    export MAIL_PORT="587"
    export MAIL_USE_TLS="true"
    export MAIL_USERNAME="you@yourdomain.com"
    export MAIL_PASSWORD="your-smtp-password-or-app-password"
    export MAIL_DEFAULT_SENDER="Duoshao Trading <you@yourdomain.com>"

If these aren't set, emails are simply logged to the console instead of sent —
the app keeps working (accounts still get created, links still get generated),
you just won't actually receive the email until SMTP is configured.
"""
import smtplib
import logging
import threading
from email.mime.text import MIMEText
from flask import current_app

logger = logging.getLogger("duoshao.mail")


def mail_enabled():
    return bool(current_app.config.get("MAIL_SERVER") and current_app.config.get("MAIL_USERNAME"))


def _send_sync(server_host, port, use_tls, username, password, sender, to_address, subject, body_text):
    msg = MIMEText(body_text)
    msg["Subject"] = subject
    msg["From"] = sender or username
    msg["To"] = to_address

    try:
        server = smtplib.SMTP(server_host, port, timeout=10)
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.sendmail(msg["From"], [to_address], msg.as_string())
        server.quit()
        logger.info("Email sent to %s: %s", to_address, subject)
    except Exception:
        logger.exception("Failed to send email to %s", to_address)


def send_email(to_address, subject, body_text):
    """Queues the email on a background thread so a slow/unreachable mail server
    never delays the page the user is waiting on. Fire-and-forget by design —
    check the logs if you need to confirm delivery attempts."""
    if not mail_enabled():
        logger.warning(
            "MAIL NOT CONFIGURED — would have sent to %s:\nSubject: %s\n\n%s",
            to_address, subject, body_text,
        )
        return False

    # Capture config values now, since current_app / the request context won't
    # exist anymore once we're running inside the background thread.
    server_host = current_app.config["MAIL_SERVER"]
    port = int(current_app.config.get("MAIL_PORT", 587))
    use_tls = current_app.config.get("MAIL_USE_TLS", True)
    username = current_app.config["MAIL_USERNAME"]
    password = current_app.config["MAIL_PASSWORD"]
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")

    thread = threading.Thread(
        target=_send_sync,
        args=(server_host, port, use_tls, username, password, sender, to_address, subject, body_text),
        daemon=True,
    )
    thread.start()
    return True
