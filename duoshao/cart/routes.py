from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import current_user
from ..models import db, Product, Sale, SaleItem, Customer, Receipt, SaleStatusEvent
from .. import payments
from ..notify import notify_staff
from ..services import expire_stale_online_orders
from datetime import datetime, date

cart_bp = Blueprint("cart", __name__)

CART_SESSION_KEY = "cart"


def _get_cart():
    return session.get(CART_SESSION_KEY, {})


def _save_cart(cart):
    session[CART_SESSION_KEY] = cart
    session.modified = True


def _make_line_key(item_id, variant=""):
    return f"{item_id}|{variant or ''}"


def _parse_line_key(line_key):
    """Returns (item_id, variant). Tolerates old carts saved before variants existed
    (plain numeric keys with no '|')."""
    if "|" in line_key:
        item_id_str, variant = line_key.split("|", 1)
    else:
        item_id_str, variant = line_key, ""
    return int(item_id_str), variant


def _cart_items_detailed():
    """Returns list of dicts {item (Product), variant, qty, line_total, line_key}
    for everything in the session cart."""
    cart = _get_cart()
    detailed = []
    for line_key, qty in cart.items():
        item_id, variant = _parse_line_key(line_key)
        item = Product.query.get(item_id)
        if item and item.active:
            price = item.unit_price_usd or 0
            detailed.append({
                "item": item, "variant": variant, "qty": qty,
                "line_total": round(price * qty, 2), "line_key": line_key,
            })
    return detailed


def cart_count():
    """Used in nav badge — total number of line items (not quantity) in cart."""
    return len(_get_cart())


@cart_bp.route("/cart/add", methods=["POST"])
def add_to_cart():
    item_id = request.form.get("item_id")
    variant = request.form.get("variant", "").strip()
    qty = int(request.form.get("qty", 1) or 1)
    action = request.form.get("action", "add")  # "add" or "buy_now"
    item = Product.query.get_or_404(int(item_id))

    # Redirect back to wherever the person was, scrolled to this exact product instead of
    # jumping to the top of the page — lets them keep browsing and adding more items.
    ref_base = (request.referrer or url_for("public.home")).split("#")[0]
    anchor_redirect = redirect(f"{ref_base}#product-{item.id}")

    if item.availability != "in_stock" or not item.unit_price_usd:
        flash(f"{item.name} isn't available for direct purchase — please submit a sourcing request instead.", "error")
        return anchor_redirect

    cart = _get_cart()
    line_key = _make_line_key(item.id, variant)
    cart[line_key] = cart.get(line_key, 0) + qty
    _save_cart(cart)

    if action == "buy_now":
        return redirect(url_for("cart.checkout"))

    flash(f"Added {item.name} to your cart.", "success")
    return anchor_redirect


@cart_bp.route("/cart/update", methods=["POST"])
def update_cart():
    line_key = request.form.get("line_key")
    qty = int(request.form.get("qty", 1) or 1)
    cart = _get_cart()
    if qty <= 0:
        cart.pop(line_key, None)
    else:
        cart[line_key] = qty
    _save_cart(cart)
    return redirect(request.form.get("next") or url_for("cart.view_cart"))


@cart_bp.route("/cart/remove", methods=["POST"])
def remove_from_cart():
    line_key = request.form.get("line_key")
    cart = _get_cart()
    cart.pop(line_key, None)
    _save_cart(cart)
    return redirect(request.form.get("next") or url_for("cart.view_cart"))


@cart_bp.route("/cart")
def view_cart():
    detailed = _cart_items_detailed()
    subtotal = round(sum(d["line_total"] for d in detailed), 2)
    return render_template("public/cart.html", cart_items=detailed, subtotal=subtotal)


@cart_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    expire_stale_online_orders()  # release any abandoned PayPal/Card reservations first

    detailed = _cart_items_detailed()
    if not detailed:
        flash("Your cart is empty.", "error")
        return redirect(url_for("public.home"))
    subtotal = round(sum(d["line_total"] for d in detailed), 2)

    paypal_available = payments.paypal_enabled()
    stripe_available = payments.stripe_enabled()

    if request.method == "POST":
        fulfillment = request.form.get("fulfillment_method", "pickup")
        fulfillment_location = request.form.get("fulfillment_location", "").strip() or None
        payment_method = request.form.get("payment_method", "bank_transfer")

        if not fulfillment_location:
            flash("Please select a location (Zambia or Zimbabwe).", "error")
            return render_template("public/checkout.html", cart_items=detailed, subtotal=subtotal,
                                    paypal_available=paypal_available, stripe_available=stripe_available)

        # Validate stock availability up-front, so the online store can never oversell.
        for d in detailed:
            if d["item"].quantity < d["qty"]:
                flash(
                    f"Sorry — only {d['item'].quantity} of \"{d['item'].name}\" left in stock "
                    f"(you have {d['qty']} in your cart). Please adjust the quantity in your cart.",
                    "error",
                )
                return redirect(url_for("cart.view_cart"))

        # Resolve customer: logged-in client -> their Customer record; else guest details
        customer = None
        guest_email = guest_phone = None
        customer_name_freetext = None

        if current_user.is_authenticated and current_user.is_client:
            customer = current_user.customer
        else:
            name = request.form.get("guest_name", "").strip()
            guest_email = request.form.get("guest_email", "").strip()
            guest_phone = request.form.get("guest_phone", "").strip()
            if not name or not guest_email:
                flash("Please provide your name and email to check out as a guest.", "error")
                return render_template("public/checkout.html", cart_items=detailed, subtotal=subtotal,
                                        paypal_available=paypal_available, stripe_available=stripe_available)
            customer_name_freetext = name
            # Reuse an existing Customer record by email if one exists (keeps history tidy)
            customer = Customer.query.filter_by(email=guest_email).first()
            if not customer:
                customer = Customer(name=name, email=guest_email, phone=guest_phone)
                db.session.add(customer)
                db.session.flush()

        delivery_address = request.form.get("delivery_address", "").strip() if fulfillment == "delivery" else None
        if fulfillment == "delivery" and not delivery_address:
            flash("Please provide a delivery address.", "error")
            return render_template("public/checkout.html", cart_items=detailed, subtotal=subtotal,
                                    paypal_available=paypal_available, stripe_available=stripe_available)

        sale = Sale(
            customer_id=customer.id if customer else None,
            customer_name_freetext=customer_name_freetext,
            date=date.today(),
            status="pending",
            source="online",
            fulfillment_method=fulfillment,
            fulfillment_location=fulfillment_location,
            delivery_address=delivery_address,
            payment_method=payment_method,
            guest_email=guest_email,
            guest_phone=guest_phone,
            notes="Placed via online store.",
        )
        db.session.add(sale)
        db.session.flush()
        for d in detailed:
            db.session.add(SaleItem(
                sale_id=sale.id,
                product_id=d["item"].id,
                description=d["item"].name + (f" — {d['variant']}" if d.get("variant") else ""),
                quantity=d["qty"],
                unit_price_usd=d["item"].unit_price_usd or 0,
            ))
            # Decrement stock now that the order is confirmed placed.
            d["item"].quantity = max(0, d["item"].quantity - d["qty"])
        db.session.add(SaleStatusEvent(sale_id=sale.id, status="order_confirmed", note="Order placed via online store."))
        db.session.commit()

        if payment_method == "paypal" and paypal_available:
            # Don't touch the cart or notify staff yet — nothing has actually been paid.
            # The cart only clears and staff only hear about this once paypal_capture()
            # confirms the payment actually went through.
            return redirect(url_for("cart.paypal_pay", sale_id=sale.id))
        if payment_method == "card" and stripe_available:
            # Same reasoning — Stripe redirects off-site, so we wait for stripe_success().
            return redirect(url_for("cart.stripe_pay", sale_id=sale.id))

        # Bank transfer / cash on delivery / cash on pickup — no gateway involved, so this
        # order is genuinely placed right now and staff need to act on it (confirm the
        # bank transfer, prep for cash collection, etc).
        _save_cart({})
        notify_staff(
            f"New online order #{sale.id}",
            f"New order placed via the online store.\n\n"
            f"Customer: {sale.display_customer}\n"
            f"Total: ${sale.total:.2f}\n"
            f"Fulfillment: {sale.fulfillment_method or '—'}\n"
            + (f"Delivery address: {sale.delivery_address}\n" if sale.delivery_address else "")
            + f"Payment method: {(sale.payment_method or '').replace('_',' ').title()}\n\n"
            f"View it in the admin panel under Sales.",
        )
        return redirect(url_for("cart.order_confirmation", sale_id=sale.id))

    return render_template("public/checkout.html", cart_items=detailed, subtotal=subtotal,
                            paypal_available=paypal_available, stripe_available=stripe_available)


@cart_bp.route("/order/<int:sale_id>/confirmation")
def order_confirmation(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template("public/order_confirmation.html", sale=sale)


# ---------------- PayPal flow ----------------
@cart_bp.route("/order/<int:sale_id>/pay/paypal")
def paypal_pay(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template("public/paypal_pay.html", sale=sale,
                            paypal_client_id=current_app.config.get("PAYPAL_CLIENT_ID"))


@cart_bp.route("/order/<int:sale_id>/pay/paypal/create", methods=["POST"])
def paypal_create(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    order_id = payments.paypal_create_order(sale.balance_due, reference_id=sale.id)
    return {"id": order_id}


@cart_bp.route("/order/<int:sale_id>/pay/paypal/capture", methods=["POST"])
def paypal_capture(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    data = request.get_json(force=True)
    paypal_order_id = data.get("orderID")
    success, captured_amount = payments.paypal_capture_order(paypal_order_id)
    if success:
        count = Receipt.query.count() + 1
        receipt = Receipt(
            receipt_number=f"DS-RCT-{datetime.utcnow().year}-{count:04d}",
            sale_id=sale.id,
            amount=captured_amount or sale.balance_due,
            method="paypal",
            issued_date=date.today(),
        )
        db.session.add(receipt)
        sale.gateway_reference = paypal_order_id
        sale.status = "paid" if sale.amount_received + (captured_amount or 0) >= sale.total else "partially_paid"
        db.session.commit()

        # Only now — payment is actually confirmed — clear the cart and tell staff.
        _save_cart({})
        notify_staff(
            f"New online order #{sale.id} (paid via PayPal)",
            f"New order placed and paid via PayPal.\n\n"
            f"Customer: {sale.display_customer}\n"
            f"Amount paid: ${receipt.amount:.2f}\n"
            f"Total: ${sale.total:.2f}\n"
            f"Fulfillment: {sale.fulfillment_method or '—'}\n"
            + (f"Delivery address: {sale.delivery_address}\n" if sale.delivery_address else "")
            + f"\nView it in the admin panel under Sales.",
        )
        return {"status": "success", "redirect": url_for("cart.order_confirmation", sale_id=sale.id)}
    return {"status": "failed"}, 400


# ---------------- Stripe (Card) flow ----------------
@cart_bp.route("/order/<int:sale_id>/pay/card")
def stripe_pay(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    checkout_session = payments.stripe_create_checkout_session(
        sale.balance_due, sale.id,
        success_url=url_for("cart.stripe_success", sale_id=sale.id, _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("cart.order_confirmation", sale_id=sale.id, _external=True),
    )
    return redirect(checkout_session.url, code=303)


@cart_bp.route("/order/<int:sale_id>/pay/card/success")
def stripe_success(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    session_id = request.args.get("session_id")
    checkout_session = payments.stripe_retrieve_session(session_id)
    if checkout_session.payment_status == "paid":
        count = Receipt.query.count() + 1
        amount = checkout_session.amount_total / 100.0
        receipt = Receipt(
            receipt_number=f"DS-RCT-{datetime.utcnow().year}-{count:04d}",
            sale_id=sale.id,
            amount=amount,
            method="card",
            issued_date=date.today(),
        )
        db.session.add(receipt)
        sale.gateway_reference = session_id
        sale.status = "paid" if sale.amount_received + amount >= sale.total else "partially_paid"
        db.session.commit()

        # Only now — payment is actually confirmed — clear the cart and tell staff.
        _save_cart({})
        notify_staff(
            f"New online order #{sale.id} (paid via card)",
            f"New order placed and paid via card.\n\n"
            f"Customer: {sale.display_customer}\n"
            f"Amount paid: ${amount:.2f}\n"
            f"Total: ${sale.total:.2f}\n"
            f"Fulfillment: {sale.fulfillment_method or '—'}\n"
            + (f"Delivery address: {sale.delivery_address}\n" if sale.delivery_address else "")
            + f"\nView it in the admin panel under Sales.",
        )
        flash("Payment received — thank you!", "success")
    return redirect(url_for("cart.order_confirmation", sale_id=sale.id))
