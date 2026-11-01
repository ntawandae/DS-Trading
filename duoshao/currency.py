"""Display-only currency conversion for the public catalog.

Everything is stored, quoted and CHARGED in USD. A visitor can choose another currency (e.g. ZMW)
to see prices converted at the rate an admin set — purely for reference.
"""
from datetime import datetime, timedelta

from flask import g, session, url_for
from .models import db, ExchangeRate


def _usable_rates():
    if not hasattr(g, "_usable_rates"):
        try:
            g._usable_rates = (
                ExchangeRate.query
                .filter(ExchangeRate.active.is_(True), ExchangeRate.rate_per_usd > 0)
                .order_by(ExchangeRate.code)
                .all()
            )
        except Exception:  # e.g. table not created yet — never break a page over a display feature
            db.session.rollback()
            g._usable_rates = []
    return g._usable_rates


def active_currencies():
    return _usable_rates()


def current_currency():
    """The ExchangeRate the visitor picked, or None for plain USD."""
    code = session.get("currency")
    if not code or code == "USD":
        return None
    return next((c for c in _usable_rates() if c.code == code), None)


def price(amount_usd):
    """Price as the visitor chose to see it: '$450.00' or, e.g., 'K12,375'."""
    amount = float(amount_usd or 0)
    cur = current_currency()
    return cur.format(amount) if cur else f"${amount:,.2f}"


def price_ref(amount_usd):
    """'≈ K12,375' when a non-USD currency is selected, otherwise ''. For showing next to USD amounts."""
    cur = current_currency()
    return f"≈ {cur.format(float(amount_usd or 0))}" if cur else ""


def currency_config():
    """What the browser needs to format prices the same way the server does."""
    cur = current_currency()
    if cur:
        return {"code": cur.code, "symbol": cur.symbol or cur.code + " ", "rate": cur.rate_per_usd, "decimals": cur.decimals or 0}
    return {"code": "USD", "symbol": "$", "rate": 1, "decimals": 2}


def stale_currencies():
    """Active currencies whose rate hasn't been confirmed within their reminder window."""
    now = datetime.utcnow()
    stale = []
    for cur in ExchangeRate.query.filter(ExchangeRate.active.is_(True), ExchangeRate.rate_per_usd > 0).all():
        limit = timedelta(days=cur.remind_after_days or 7)
        if cur.updated_at is None or now - cur.updated_at > limit:
            stale.append(cur)
    return stale


def send_stale_rate_reminders():
    """Email admin/finance about stale rates, at most once every 24 hours per currency.
    The 'claim' is a conditional UPDATE, so several server workers can't send duplicates."""
    from .email_utils import mail_enabled
    from .notify import notify_finance

    if not mail_enabled():
        return 0

    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)
    sent = 0
    for cur in stale_currencies():
        claimed = (
            ExchangeRate.query
            .filter(
                ExchangeRate.id == cur.id,
                db.or_(ExchangeRate.last_reminded_at.is_(None), ExchangeRate.last_reminded_at < cutoff),
            )
            .update({"last_reminded_at": now}, synchronize_session=False)
        )
        db.session.commit()
        if not claimed:
            continue
        age = f"{cur.days_since_update} days ago" if cur.updated_at else "never confirmed"
        notify_finance(
            f"Please update the USD to {cur.code} exchange rate",
            f"Hi,\n\nThe USD → {cur.code} rate shown on the Duoshao Trading catalog "
            f"(1 USD = {cur.symbol or ''}{cur.rate_per_usd:g}) was last updated {age}"
            + (f" by {cur.updated_by}" if cur.updated_by else "")
            + f".\n\nClients who choose {cur.code} see prices based on this rate, so please check it "
            f"against today's rate and save it (even if it hasn't changed):\n"
            f"{url_for('admin.currency_settings', _external=True)}\n\n"
            f"You'll get this reminder once a day until it's updated.\n\nDuoshao Trading Co., Ltd.",
        )
        sent += 1
    return sent
