from datetime import date, datetime, timedelta
from .models import db, Sale, SaleItem, SaleStatusEvent

# How long an online order can sit unpaid via PayPal/Card before we release the stock
# reservation and mark it cancelled. Bank transfer / cash orders are exempt — those are
# genuinely placed orders awaiting manual follow-up, not abandoned checkouts.
STALE_ORDER_MINUTES = 30


def convert_quotation_to_sale(quote, source="admin"):
    """Turn an accepted Quotation into a real Sale/order, carrying over the sourcing
    service fee and shipping estimate as their own line items so the sale total matches
    exactly what the client was quoted."""
    sale = Sale(
        customer_id=quote.customer_id,
        customer_name_freetext=quote.customer_name_freetext,
        date=date.today(),
        notes=f"Converted from quotation {quote.quote_number}",
        source=source,
    )
    db.session.add(sale)
    db.session.flush()

    for qi in quote.items:
        db.session.add(SaleItem(
            sale_id=sale.id,
            description=qi.description,
            quantity=qi.quantity,
            unit_price_usd=qi.unit_price_usd,
        ))

    if quote.service_fee_usd:
        db.session.add(SaleItem(
            sale_id=sale.id,
            description="Sourcing Service Fee (10%)",
            quantity=1,
            unit_price_usd=quote.service_fee_usd,
        ))

    if quote.shipping_estimate_usd:
        db.session.add(SaleItem(
            sale_id=sale.id,
            description="Estimated Shipping / Delivery",
            quantity=1,
            unit_price_usd=quote.shipping_estimate_usd,
        ))

    db.session.add(SaleStatusEvent(
        sale_id=sale.id,
        status="order_confirmed",
        note=f"Order created from accepted quotation {quote.quote_number}.",
    ))

    return sale


def expire_stale_online_orders():
    """Cancels online orders that chose PayPal/Card but never actually completed payment,
    and gives the reserved stock back. Safe to call often — already-cancelled orders are
    skipped, so nothing gets double-processed. The person's cart is untouched either way;
    it was never cleared for these in the first place (only a successful payment clears it),
    so they can simply check out again without having lost anything."""
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_ORDER_MINUTES)
    stale_sales = Sale.query.filter(
        Sale.source == "online",
        Sale.payment_method.in_(["paypal", "card"]),
        Sale.status == "pending",
        Sale.fulfillment_status != "cancelled",
        Sale.created_at < cutoff,
    ).all()

    for sale in stale_sales:
        for item in sale.items:
            if item.product_id and item.product:
                item.product.quantity += item.quantity
        sale.fulfillment_status = "cancelled"
        db.session.add(SaleStatusEvent(
            sale_id=sale.id,
            status="cancelled",
            note=f"Automatically cancelled — payment via {sale.payment_method} was not "
                 f"completed within {STALE_ORDER_MINUTES} minutes. Stock has been released.",
        ))

    if stale_sales:
        db.session.commit()

    return len(stale_sales)
