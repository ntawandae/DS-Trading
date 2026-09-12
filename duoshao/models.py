from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """Login for all three tiers: admin (management), worker (staff), client (customer portal)."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)  # staff: chosen username / clients: their email
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default="worker")  # admin / finance_hr / worker / client
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)  # set only for role='client'
    email = db.Column(db.String(120))  # for staff: where to send notifications, if different from username
    active = db.Column(db.Boolean, default=True)
    email_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", backref=db.backref("user_account", uselist=False))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_finance(self):
        """HR / Finance team — payroll, reports. Admin always included."""
        return self.role in ("admin", "finance_hr")

    @property
    def is_worker(self):
        """Anyone allowed into the internal admin panel at all — cashiers included."""
        return self.role in ("admin", "finance_hr", "worker")

    @property
    def is_client(self):
        return self.role == "client"

    @property
    def role_label(self):
        return {
            "admin": "Management",
            "finance_hr": "Finance / HR",
            "worker": "Worker",
            "client": "Client",
        }.get(self.role, self.role.title())

    @property
    def notify_email(self):
        """Best email address to send this user notifications to."""
        if self.email:
            return self.email
        if "@" in self.username:  # client usernames are their email by convention
            return self.username
        return None


class Product(db.Model):
    """The single source of truth for anything we sell — public catalog listing AND
    internal stock record are the same row. No more entering an item twice: add it once,
    set a quantity (0 is fine), and it shows as 'In Stock' or 'Available on Request'
    automatically based on that number."""
    __tablename__ = "stock_item"  # kept as the original table name — no data migration needed

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    sku = db.Column(db.String(60), unique=True)
    category = db.Column(db.String(80))
    description = db.Column(db.Text)
    image_filename = db.Column(db.String(255))
    quantity = db.Column(db.Integer, default=0)
    reorder_level = db.Column(db.Integer, default=5)
    unit_cost_usd = db.Column(db.Float, default=0)  # internal cost — never shown publicly
    unit_price_usd = db.Column(db.Float, default=0)  # what the customer pays
    active = db.Column(db.Boolean, default=True)  # visible on the public catalog
    variant_label = db.Column(db.String(40))  # e.g. "Color", "Size" — shown above the picker on the product page
    variant_options = db.Column(db.Text)  # comma-separated choices, e.g. "Red, Blue, Black" — same price/stock for all
    gallery_images = db.Column(db.Text)  # comma-separated additional image filenames, shown alongside the main photo
    location = db.Column(db.String(20))  # Zambia / Zimbabwe — where this stock is physically held
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def variant_choices(self):
        """List of variant option strings, or [] if this product doesn't have variants."""
        if not self.variant_options:
            return []
        return [v.strip() for v in self.variant_options.split(",") if v.strip()]

    @property
    def gallery_filenames(self):
        """List of additional image filenames, or [] if none were uploaded."""
        if not self.gallery_images:
            return []
        return [f.strip() for f in self.gallery_images.split(",") if f.strip()]

    @property
    def all_image_filenames(self):
        """Main photo first, then gallery images — the full set for the product page."""
        files = []
        if self.image_filename:
            files.append(self.image_filename)
        files.extend(self.gallery_filenames)
        return files

    @property
    def availability(self):
        return "in_stock" if (self.quantity or 0) > 0 else "sourced_on_request"

    @property
    def low_stock(self):
        return self.quantity <= self.reorder_level

    @property
    def indicative_price_usd(self):
        """Backward-compatible alias — same value as unit_price_usd."""
        return self.unit_price_usd



class SourcingRequest(db.Model):
    """Client inquiry submitted from the public site."""
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)  # set if a logged-in client submitted it
    customer_name = db.Column(db.String(150), nullable=False)
    contact = db.Column(db.String(150), nullable=False)  # phone / whatsapp / email
    country = db.Column(db.String(80))
    product_name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80))
    quantity = db.Column(db.String(60))
    notes = db.Column(db.Text)
    status = db.Column(db.String(30), default="new")  # new / in_progress / quoted / closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer")
    quotations = db.relationship("Quotation", backref="sourcing_request", lazy=True)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    company = db.Column(db.String(150))
    phone = db.Column(db.String(60))
    email = db.Column(db.String(120))
    country = db.Column(db.String(80))
    address = db.Column(db.Text)  # default delivery address, prefilled at checkout
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sales = db.relationship("Sale", backref="customer", lazy=True)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    customer_name_freetext = db.Column(db.String(150))  # fallback if no customer record
    date = db.Column(db.Date, default=datetime.utcnow)
    status = db.Column(db.String(30), default="pending")  # pending / paid / partially_paid
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Online storefront fields
    source = db.Column(db.String(20), default="admin")  # admin / online
    fulfillment_method = db.Column(db.String(20))  # delivery / pickup
    fulfillment_location = db.Column(db.String(20))  # Zambia / Zimbabwe — which country this order is served from
    delivery_address = db.Column(db.Text)
    payment_method = db.Column(db.String(30))  # paypal / card / bank_transfer / cash_on_delivery / cash_on_pickup
    guest_email = db.Column(db.String(120))
    guest_phone = db.Column(db.String(60))
    gateway_reference = db.Column(db.String(120))  # PayPal order id / Stripe session id

    # Shipment / fulfillment tracking
    fulfillment_status = db.Column(db.String(30), default="order_confirmed")
    carrier_name = db.Column(db.String(120))
    tracking_number = db.Column(db.String(120))

    items = db.relationship("SaleItem", backref="sale", lazy=True, cascade="all, delete-orphan")
    receipts = db.relationship("Receipt", backref="sale", lazy=True)
    status_events = db.relationship("SaleStatusEvent", backref="sale", lazy=True,
                                     order_by="SaleStatusEvent.created_at", cascade="all, delete-orphan")

    @property
    def total(self):
        return sum(item.line_total for item in self.items)

    @property
    def amount_received(self):
        return sum(r.amount for r in self.receipts)

    @property
    def balance_due(self):
        return round(self.total - self.amount_received, 2)

    @property
    def display_customer(self):
        if self.customer:
            return self.customer.name
        return self.customer_name_freetext or "Walk-in / Unnamed"

    @property
    def fulfillment_status_label(self):
        return dict(FULFILLMENT_STAGES).get(self.fulfillment_status, (self.fulfillment_status or "").replace("_", " ").title())


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("stock_item.id"), nullable=True)  # set for online-store items, so stock can be restored if the order expires unpaid
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Float, default=1)
    unit_price_usd = db.Column(db.Float, default=0)

    product = db.relationship("Product")

    @property
    def line_total(self):
        return round((self.quantity or 0) * (self.unit_price_usd or 0), 2)


class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(40), unique=True, nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(40), default="bank_transfer")  # cash / bank_transfer / mobile_money / other
    issued_date = db.Column(db.Date, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Expense(db.Model):
    """Company payments & expenses."""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, default=datetime.utcnow)
    category = db.Column(db.String(80), nullable=False)  # sourcing/logistics/office/utilities/other
    description = db.Column(db.String(200))
    paid_to = db.Column(db.String(150))
    amount_usd = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(40), default="bank_transfer")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_code = db.Column(db.String(30), unique=True)
    name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(100))
    department = db.Column(db.String(100))
    id_number = db.Column(db.String(60))  # national ID / passport number
    phone = db.Column(db.String(60))
    date_joined = db.Column(db.Date)
    bank_name = db.Column(db.String(120))
    bank_account_number = db.Column(db.String(60))
    monthly_salary_usd = db.Column(db.Float, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    payroll_runs = db.relationship("PayrollRun", backref="employee", lazy=True)


class PayrollRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    period = db.Column(db.String(20), nullable=False)  # e.g. "2026-08"
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)

    basic_salary_usd = db.Column(db.Float, default=0)
    housing_allowance_usd = db.Column(db.Float, default=0)
    transport_allowance_usd = db.Column(db.Float, default=0)
    other_allowance_usd = db.Column(db.Float, default=0)

    tax_deduction_usd = db.Column(db.Float, default=0)
    pension_deduction_usd = db.Column(db.Float, default=0)
    other_deduction_usd = db.Column(db.Float, default=0)

    amount_usd = db.Column(db.Float, nullable=False)  # net pay
    status = db.Column(db.String(20), default="pending")  # pending / paid
    payment_method = db.Column(db.String(40), default="bank_transfer")
    payment_reference = db.Column(db.String(80))
    paid_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def gross_pay(self):
        return round((self.basic_salary_usd or 0) + (self.housing_allowance_usd or 0) +
                     (self.transport_allowance_usd or 0) + (self.other_allowance_usd or 0), 2)

    @property
    def total_deductions(self):
        return round((self.tax_deduction_usd or 0) + (self.pension_deduction_usd or 0) +
                     (self.other_deduction_usd or 0), 2)


class Quotation(db.Model):
    SERVICE_FEE_RATE = 0.10  # 10% sourcing service fee on product cost

    id = db.Column(db.Integer, primary_key=True)
    quote_number = db.Column(db.String(40), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    customer_name_freetext = db.Column(db.String(150))
    sourcing_request_id = db.Column(db.Integer, db.ForeignKey("sourcing_request.id"), nullable=True)
    date = db.Column(db.Date, default=datetime.utcnow)
    valid_until = db.Column(db.Date)
    status = db.Column(db.String(30), default="draft")  # draft / sent / accepted / rejected / expired
    notes = db.Column(db.Text)
    shipping_estimate_usd = db.Column(db.Float, default=0)
    service_fee_usd = db.Column(db.Float, default=0)  # locked in at creation time — 10% of items subtotal
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer")
    items = db.relationship("QuotationItem", backref="quotation", lazy=True, cascade="all, delete-orphan")

    @property
    def items_subtotal(self):
        return round(sum(item.line_total for item in self.items), 2)

    @property
    def total(self):
        """Grand total — what the client actually pays: products + sourcing fee + shipping estimate."""
        return round(self.items_subtotal + (self.service_fee_usd or 0) + (self.shipping_estimate_usd or 0), 2)

    @property
    def display_customer(self):
        if self.customer:
            return self.customer.name
        return self.customer_name_freetext or "Prospective Client"

    @property
    def status_badge_class(self):
        return {
            "draft": "quoted", "sent": "quoted",
            "accepted": "paid",
            "rejected": "pending",
            "expired": "closed",
        }.get(self.status, "closed")


class QuotationItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey("quotation.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Float, default=1)
    unit_price_usd = db.Column(db.Float, default=0)

    @property
    def line_total(self):
        return round((self.quantity or 0) * (self.unit_price_usd or 0), 2)


class FloatTransaction(db.Model):
    """Manual cash-on-hand ledger (owner capital, till top-ups, cash withdrawals, corrections)."""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, default=datetime.utcnow)
    description = db.Column(db.String(200), nullable=False)
    amount_usd = db.Column(db.Float, nullable=False)  # positive = cash in, negative = cash out
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# Ordered stages for the manual shipment-tracking timeline.
FULFILLMENT_STAGES = [
    ("order_confirmed", "Order Confirmed"),
    ("processing", "Processing"),
    ("shipped", "Shipped from Haikou"),
    ("in_transit", "In Transit"),
    ("arrived_destination", "Arrived in Destination Country"),
    ("out_for_delivery", "Out for Delivery"),
    ("ready_for_pickup", "Ready for Pickup"),
    ("delivered", "Delivered"),
    ("cancelled", "Cancelled"),
]


class SaleStatusEvent(db.Model):
    """One entry in a sale's fulfillment timeline — what the client sees as tracking history."""
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def status_label(self):
        return dict(FULFILLMENT_STAGES).get(self.status, self.status.replace("_", " ").title())


class Notification(db.Model):
    """In-app notification for a client (order status changes, etc.)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="notifications")
