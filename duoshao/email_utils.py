"""
Outbound email — used for signup verification and password reset links.

Sent via Alibaba DirectMail's HTTP API, not SMTP.

Required environment variables:
    ALIBABA_ACCESS_KEY_ID="..."
    ALIBABA_ACCESS_KEY_SECRET="..."
    ALIBABA_REGION_ID="cn-hangzhou"
    ALIBABA_DM_ACCOUNT_NAME="info@duoshaotrading.com"
    ALIBABA_DM_FROM_ALIAS="Duoshao Trading"
    ALIBABA_DM_REPLY_TO="false"

If these aren't configured, emails are logged instead of sent.
"""

import logging
import threading

from flask import current_app
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkdm.request.v20151123.SingleSendMailRequest import SingleSendMailRequest


logger = logging.getLogger("duoshao.mail")


def mail_enabled():
    cfg = current_app.config

    return bool(
        cfg.get("ALIBABA_ACCESS_KEY_ID")
        and cfg.get("ALIBABA_ACCESS_KEY_SECRET")
        and cfg.get("ALIBABA_DM_ACCOUNT_NAME")
    )


def _send_sync(
    access_key_id,
    access_key_secret,
    region,
    account_name,
    from_alias,
    reply_to_flag,
    to_address,
    subject,
    html_body,
    text_body,
):
    try:
        client = AcsClient(
            access_key_id,
            access_key_secret,
            region,
        )

        request = SingleSendMailRequest()
        request.set_accept_format("json")

        # Verified DirectMail sender
        request.set_AccountName(account_name)

        # 1 = use the verified sender address
        request.set_AddressType(1)

        # "true" or "false"
        request.set_ReplyToAddress(reply_to_flag)

        request.set_ToAddress(to_address)
        request.set_Subject(subject)

        if from_alias:
            request.set_FromAlias(from_alias)

        # DirectMail requires a body
        if html_body:
            request.set_HtmlBody(html_body)
        elif text_body:
            request.set_TextBody(text_body)

        client.do_action_with_exception(request)

        logger.info(
            "Email sent via Alibaba DirectMail to %s: %s",
            to_address,
            subject,
        )

    except (ClientException, ServerException):
        logger.exception(
            "Failed to send email to %s via Alibaba DirectMail",
            to_address,
        )

    except Exception:
        logger.exception(
            "Unexpected error while sending email to %s",
            to_address,
        )


def _dispatch(
    to_address,
    subject,
    html_body=None,
    text_body=None,
):
    """
    Shared sending logic for plain-text and HTML emails.

    The actual API call runs in a background thread so a slow
    DirectMail request does not hold up the user's page request.
    """

    cfg = current_app.config

    if not mail_enabled():
        logger.warning(
            "MAIL NOT CONFIGURED — would have sent to %s:\nSubject: %s",
            to_address,
            subject,
        )
        return False

    access_key_id = cfg["ALIBABA_ACCESS_KEY_ID"]
    access_key_secret = cfg["ALIBABA_ACCESS_KEY_SECRET"]

    region = cfg.get(
        "ALIBABA_REGION_ID",
        "cn-hangzhou",
    )

    account_name = cfg["ALIBABA_DM_ACCOUNT_NAME"]

    from_alias = cfg.get(
        "ALIBABA_DM_FROM_ALIAS"
    )

    reply_to_flag = (
        "true"
        if str(
            cfg.get(
                "ALIBABA_DM_REPLY_TO",
                "false",
            )
        ).lower()
        == "true"
        else "false"
    )

    thread = threading.Thread(
        target=_send_sync,
        args=(
            access_key_id,
            access_key_secret,
            region,
            account_name,
            from_alias,
            reply_to_flag,
            to_address,
            subject,
            html_body,
            text_body,
        ),
        daemon=True,
    )

    thread.start()

    return True


def send_email(
    to_address,
    subject,
    body_text,
):
    """Send a plain-text email through Alibaba DirectMail."""

    return _dispatch(
        to_address,
        subject,
        text_body=body_text,
    )


def send_html_email(
    to_address,
    subject,
    html_body,
    text_fallback=None,
):
    """Send an HTML email through Alibaba DirectMail."""

    return _dispatch(
        to_address,
        subject,
        html_body=html_body,
        text_body=text_fallback,
    )
