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
from email.mime.multipart import MIMEMultipart
from flask import current_app

logger = logging.getLogger("duoshao.mail")


def mail_enabled():
    return bool(current_app.config.get("MAIL_SERVER") and current_app.config.get("MAIL_USERNAME"))


def _send_sync(server_host, port, use_tls, use_ssl, username, password, sender, to_address, msg):
    try:
        if use_ssl:
            # Implicit SSL (e.g. Aliyun's smtp.qiye.aliyun.com:465) — the connection is
            # encrypted from the start, no STARTTLS handshake.
            server = smtplib.SMTP_SSL(server_host, port, timeout=10)
        else:
            server = smtplib.SMTP(server_host, port, timeout=10)
            if use_tls:
                server.starttls()
        server.login(username, password)
        server.sendmail(sender or username, [to_address], msg.as_string())
        server.quit()
        logger.info("Email sent to %s: %s", to_address, msg["Subject"])
    except Exception:
        logger.exception("Failed to send email to %s", to_address)


def _dispatch(to_address, msg):
    """Shared plumbing for both send_email and send_html_email: checks config, captures it
    now (current_app won't exist inside the background thread), and fires the send in the
    background so a slow/unreachable mail server never delays the page the user is waiting on."""
    if not mail_enabled():
        logger.warning("MAIL NOT CONFIGURED — would have sent to %s:\nSubject: %s", to_address, msg["Subject"])
        return False

    server_host = current_app.config["MAIL_SERVER"]
    port = int(current_app.config.get("MAIL_PORT", 587))
    # Port 465 is always implicit SSL by convention, so default MAIL_USE_SSL to true there
    # even if it isn't set explicitly.
    use_ssl = current_app.config.get("MAIL_USE_SSL", port == 465)
    use_tls = current_app.config.get("MAIL_USE_TLS", not use_ssl)
    username = current_app.config["MAIL_USERNAME"]
    password = current_app.config["MAIL_PASSWORD"]
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")
    msg["From"] = sender or username
    msg["To"] = to_address

    thread = threading.Thread(
        target=_send_sync,
        args=(server_host, port, use_tls, use_ssl, username, password, sender, to_address, msg),
        daemon=True,
    )
    thread.start()
    return True


def send_email(to_address, subject, body_text):
    """Plain-text email — fire-and-forget by design; check the logs to confirm delivery attempts."""
    msg = MIMEText(body_text)
    msg["Subject"] = subject
    return _dispatch(to_address, msg)


def send_html_email(to_address, subject, html_body, text_fallback=None):
    """HTML email (e.g. a formatted quotation or receipt), with a plain-text fallback part for
    email clients that don't render HTML. Same fire-and-forget behavior as send_email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg.attach(MIMEText(text_fallback or "This email contains an HTML document — please view it in an HTML-capable email client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return _dispatch(to_address, msg)
