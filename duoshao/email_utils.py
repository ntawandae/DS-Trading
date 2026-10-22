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
from Tea.exceptions import TeaException
from alibabacloud_tea_openapi.models import Config
from alibabacloud_dm20151123.client import Client as DmClient
from alibabacloud_dm20151123.models import SingleSendMailRequest


logger = logging.getLogger("duoshao.mail")

# DirectMail's public endpoints don't follow the usual dm.<region>.aliyuncs.com
# pattern for every region — cn-hangzhou (the default/home region) is served at
# the bare dm.aliyuncs.com host. See:
# https://www.alibabacloud.com/help/en/direct-mail/api-endpoints
_DM_ENDPOINTS = {
    "cn-hangzhou": "dm.aliyuncs.com",
    "ap-southeast-1": "dm.ap-southeast-1.aliyuncs.com",
    "ap-southeast-2": "dm.ap-southeast-2.aliyuncs.com",
    "us-east-1": "dm.us-east-1.aliyuncs.com",
    "eu-central-1": "dm.eu-central-1.aliyuncs.com",
}


def _dm_endpoint(region):
    return _DM_ENDPOINTS.get(region, f"dm.{region}.aliyuncs.com")


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
        config = Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            region_id=region,
            endpoint=_dm_endpoint(region),
        )
        client = DmClient(config)

        request = SingleSendMailRequest(
            account_name=account_name,
            # 1 = use the verified sender address
            address_type=1,
            # This field is a real bool in the new SDK, not "true"/"false"
            reply_to_address=reply_to_flag,
            to_address=to_address,
            subject=subject,
        )

        if from_alias:
            request.from_alias = from_alias

        # DirectMail requires a body
        if html_body:
            request.html_body = html_body
        elif text_body:
            request.text_body = text_body

        client.single_send_mail(request)

        logger.info(
            "Email sent via Alibaba DirectMail to %s: %s",
            to_address,
            subject,
        )

    except TeaException:
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

    # This field is a real bool in the new SDK
    reply_to_flag = str(
        cfg.get(
            "ALIBABA_DM_REPLY_TO",
            "false",
        )
    ).lower() == "true"

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
