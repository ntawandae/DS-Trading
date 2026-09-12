"""
Payment gateway integration.

Both gateways are OPT-IN via environment variables. If the relevant keys aren't set,
that payment method simply doesn't appear at checkout — the store still works fully
with Bank Transfer / Cash on Delivery / Cash on Pickup, which need no external account.

To enable PayPal:
    Set PAYPAL_CLIENT_ID and PAYPAL_SECRET (from a PayPal Developer app: https://developer.paypal.com).
    Set PAYPAL_MODE=live when ready for real money; defaults to "sandbox".

To enable Card payments (via Stripe Checkout):
    Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY (from https://dashboard.stripe.com/apikeys).
"""
import requests
from flask import current_app


# ---------------- PayPal ----------------
def paypal_enabled():
    return bool(current_app.config.get("PAYPAL_CLIENT_ID") and current_app.config.get("PAYPAL_SECRET"))


def _paypal_base_url():
    return "https://api-m.sandbox.paypal.com" if current_app.config.get("PAYPAL_MODE", "sandbox") == "sandbox" \
        else "https://api-m.paypal.com"


def _paypal_access_token():
    resp = requests.post(
        f"{_paypal_base_url()}/v1/oauth2/token",
        auth=(current_app.config["PAYPAL_CLIENT_ID"], current_app.config["PAYPAL_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def paypal_create_order(amount_usd, reference_id):
    token = _paypal_access_token()
    resp = requests.post(
        f"{_paypal_base_url()}/v2/checkout/orders",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": str(reference_id),
                "amount": {"currency_code": "USD", "value": f"{amount_usd:.2f}"},
            }],
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def paypal_capture_order(paypal_order_id):
    token = _paypal_access_token()
    resp = requests.post(
        f"{_paypal_base_url()}/v2/checkout/orders/{paypal_order_id}/capture",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status")
    captured_amount = None
    try:
        captured_amount = float(
            data["purchase_units"][0]["payments"]["captures"][0]["amount"]["value"]
        )
    except (KeyError, IndexError, ValueError):
        pass
    return status == "COMPLETED", captured_amount


# ---------------- Stripe (Cards) ----------------
def stripe_enabled():
    return bool(current_app.config.get("STRIPE_SECRET_KEY"))


def stripe_create_checkout_session(amount_usd, sale_id, success_url, cancel_url):
    import stripe
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"Duoshao Trading — Order #{sale_id}"},
                "unit_amount": int(round(amount_usd * 100)),
            },
            "quantity": 1,
        }],
        metadata={"sale_id": str(sale_id)},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session


def stripe_retrieve_session(session_id):
    import stripe
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe.checkout.Session.retrieve(session_id)
