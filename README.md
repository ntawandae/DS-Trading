# Duoshao Trading — Web Platform

A three-tier Flask web app for Haikou Duoshao Trading Co., Ltd.:

- **Public site** — catalog, sourcing-request form, client sign-up. Footer links to WhatsApp/Facebook/Instagram/TikTok/WeChat.
- **Client portal** (`/account`) — customers log in to track their own orders, quotations, and submit sourcing requests.
- **Admin panel** (`/admin`) — two internal roles:
  - **Management (admin)** — full access: sales, quotations, receipts, payments & expenses, payroll/payslips, stock, float/cash, reports, and user/role management.
  - **Worker** — operational access only (sourcing requests, catalog, customers, quotations, sales, receipts, stock). No visibility into expenses, payroll, float, reports, or financial dashboard figures.

Branding (navy #16233F / gold #C9A15A, logo, fonts) matches the Duoshao brand identity system throughout, including the client portal.

## 1. Install

Requires Python 3.10+.

```bash
cd duoshao_platform
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. First-time setup

```bash
python seed.py
```

This creates:
- **Management login** → username: `admin` / password: `changeme123`
- **Worker login** → username: `worker` / password: `changeme123`
- **Sample client login** → username: `tendai@example.com` / password: `changeme123`
- A few sample catalog items, one stock item, one employee (edit/delete these anytime from the admin panel)

**Change these passwords immediately** — either via the Users & Roles page in the admin panel (management-only), or for the initial admin account via a Python shell:

```bash
python
>>> from duoshao import create_app
>>> from duoshao.models import db, User
>>> app = create_app()
>>> with app.app_context():
...     u = User.query.filter_by(username="admin").first()
...     u.set_password("your-new-strong-password")
...     db.session.commit()
```


## 2b. Editing credentials later — the `.env` file

All API keys and credentials (email, PayPal, Stripe, WhatsApp/Twilio) live in a single `.env` file
in the project root — **that's the only file to edit** when adding or changing a credential. No code
changes, no re-running `seed.py`. Just edit `.env`, then restart the app (stop it with Ctrl+C, run
`python run.py` again) for the change to take effect.

A safe template with no real secrets is in `.env.example` for reference. The real `.env` is already
excluded from version control via `.gitignore` — never commit it or share it publicly.

Email is already configured and ready to use (Gmail SMTP). If you ever need to change the email
account or password (e.g. after rotating the Gmail App Password), just update the `MAIL_USERNAME`
and `MAIL_PASSWORD` lines in `.env`.

## 3. Run it

```bash
python run.py
```

Visit:
- Public site: http://127.0.0.1:5000/
- Client sign-up: http://127.0.0.1:5000/account/register
- Login (all roles, redirects automatically): http://127.0.0.1:5000/login

## 4. Who can do what

| Area | Management | Worker | Client |
|---|---|---|---|
| Dashboard | Full financial view | Operational counts only | — |
| Sourcing Requests, Catalog, Customers | ✅ | ✅ | submit own requests only |
| Quotations, Sales, Receipts, Stock | ✅ | ✅ | view own only, read-only |
| Payments & Expenses | ✅ | ❌ (403) | — |
| Payroll & Payslips | ✅ | ❌ (403) | — |
| Float / Cash | ✅ | ❌ (403) | — |
| Reports | ✅ | ❌ (403) | — |
| Users & Roles | ✅ | ❌ (403) | — |

Client accounts are self-service (sign up at `/account/register`); management creates worker/management accounts from **Users & Roles** in the admin panel.

## 5. Online store: cart, checkout, delivery/pickup, payments

Anyone — logged in or not — can add "In Stock" catalog items to a cart and check out. Items marked "Sourced on
Request" don't get a cart button; they route to the sourcing request form instead (that's a manual quote process,
not an instant-buy one).

- **Cart** works via session for guests; no account needed to add items or view the cart.
- **Checkout** asks for name/email/phone if not logged in (guest checkout — reuses an existing Customer record by
  email if one exists), or uses the logged-in client's account automatically.
- **Fulfillment**: Pickup (at the Haikou Free Trade Port location) or Delivery (captures a delivery address).
- **Payment methods always available, no setup needed**: Bank Transfer, Cash on Pickup, Cash on Delivery — these
  just create the order as "pending" for staff to confirm once payment is received.
- **PayPal and Card are opt-in** and only appear at checkout once configured (see below) — nothing breaks if you
  skip this.

Every order — however it was placed — lands in **Sales** in the admin panel exactly like an in-person sale, with
source/fulfillment/delivery address visible on the sale detail page, and (if logged in) shows up in the client's
own **My Orders** dashboard immediately.

### Enabling PayPal
1. Create a free app at https://developer.paypal.com (sandbox is fine for testing, switch to a live app for real money).
2. Set these environment variables before running the app:
   ```bash
   export PAYPAL_CLIENT_ID="your-client-id"
   export PAYPAL_SECRET="your-secret"
   export PAYPAL_MODE="sandbox"   # or "live" when ready
   ```
3. Restart the app. The PayPal option will now appear at checkout automatically.

### Enabling Card payments (via Stripe Checkout)
1. Create a Stripe account at https://dashboard.stripe.com and grab your API keys.
2. Set these environment variables:
   ```bash
   export STRIPE_SECRET_KEY="sk_live_or_test_..."
   export STRIPE_PUBLISHABLE_KEY="pk_live_or_test_..."
   ```
3. Restart the app. The "Credit / Debit Card" option will now appear at checkout, redirecting to a hosted Stripe Checkout page.

**Neither of these can be turned on by me** — they need a real PayPal Business account and/or Stripe account
registered in your name/company, since payments settle into accounts only you control.

## 6. What's inside
### Public side
- `/` — catalog grid with category filter tabs, "In Stock" vs "Sourced on Request" badges
- `/request` — sourcing request form (feeds straight into the admin inbox)
- `/about` — company blurb + contact info
- `/account/register` — client sign-up

### Client portal (`/account`)
- Dashboard listing their own orders and quotations
- Order detail — itemized, with full payment history
- Quotation detail — itemized, read-only
- Submit a sourcing request tied to their account

### Admin side (`/admin`)
- **Dashboard** — full financials for management; operational counts only for workers
- **Sourcing Requests** — inbox from the public form + client portal, filter by status, update inline
- **Catalog** — add/hide items shown on the public site
- **Customers** — simple customer records, reusable across sales and quotations
- **Quotations** — build from catalog items (auto-fills price) or any custom item; print-ready; one-click **Convert to Sale**
- **Sales** — same catalog-or-custom item picker; tracks balance due as receipts come in
- **Receipts** — auto-numbered (DS-RCT-YYYY-####), itemized ("For: ...") so it's clear what was paid for, not just a sale reference; printable; a **combined receipt** shows every payment on a sale as one document
- **Payments & Expenses** *(management only)* — categorized expense log
- **Payroll** *(management only)* — employees with bank details, itemized payroll runs (basic + allowances − deductions = net pay), and a bank-standard printable **payslip** per run
- **Stock Control** — quantity, reorder level with low-stock flags, cost/price, quick +/- adjustment
- **Float / Cash** *(management only)* — running cash-on-hand ledger; cash-method receipts/expenses post automatically, plus manual entries
- **Reports** *(management only)* — revenue, expenses, payroll paid, net profit for any Day / Week / Month / Quarter / Year
- **Users & Roles** *(management only)* — create worker/management accounts, reset passwords, deactivate accounts

## 7. Recent fixes & additions (this update)

- **Shared navigation bar.** Every page — public catalog, cart, checkout, about, and every client account page — now includes one single nav partial (`duoshao/templates/_nav.html`). Logged-in clients see the exact same links (My Account, My Profile, Notifications, Request Sourcing, Cart, Log Out) no matter which page they're on, so browsing the catalog while logged in no longer drops half the menu. This also means the nav can't drift out of sync again — there's only one file to edit if it ever needs to change.
- **My Profile page** for clients — separate from orders/quotations — with editable personal details (name, company, country, phone, default delivery address), email change (password-confirmed, re-triggers verification), password change, and a manual "Resend Verification Email" button if the original signup verification didn't go through.
- **Saved delivery address** now auto-fills at checkout once a client has set one in My Profile.
- **Stale online orders expire automatically.** A PayPal/Card order that never completes payment within 30 minutes is cancelled and its reserved stock is given back — checked whenever the checkout page or admin dashboard loads, no scheduler needed. Bank transfer and cash orders are exempt (those are real placed orders awaiting manual follow-up, not abandoned checkouts). The shopping cart itself is never affected by this — it was already left untouched until a payment actually succeeds, so nothing is lost either way.
- **Verified quotations, sales, and receipts all still work correctly** after the Products/Stock merge — the catalog-item price autofill on both the Quotation and Sale forms now pulls from the unified Products table, and combined receipts / payment tracking behave exactly as before.


- **Real email notifications, both directions.**
  - **Clients** get emailed (and get the existing in-app notification) whenever: a quotation is marked "Sent" and ready for their review, their order's fulfillment status changes, or a payment is recorded against their order.
  - **Staff** get emailed at `STAFF_NOTIFICATION_EMAIL` (set in `.env`) whenever: a new sourcing request comes in (from the public site or a logged-in client), a new online order is placed, or a client accepts/declines a quotation.
  - Staff can also each have their own `email` set (distinct from their login username) for more targeted routing later — not required, `STAFF_NOTIFICATION_EMAIL` alone covers the common case of one inbox catching everything.
  - **Email sending runs in a background thread**, so a slow or temporarily unreachable mail server never delays the page the person is waiting on — the request completes immediately either way, and any failure just gets logged rather than surfaced to the user.


- **Login page renamed** from "Staff Login" to just "Login" — it always served all three roles, the label was just stale.
- **Forgot password.** A "Forgot password?" link on the login page sends a time-limited reset link (1 hour) to the account's email/username. If no email server is configured yet, the person is told plainly to contact an admin instead of a broken flow pretending to work.
- **Email verification scaffolding for client signup.** New client accounts get a verification email if email is configured; if it isn't, accounts are auto-marked verified so nobody gets locked out waiting on an email that was never going to arrive. See "Enabling outbound email" below to turn this on for real.
- **Quotations now show true landed cost.** Every quotation automatically adds a **10% sourcing service fee** on the item subtotal (calculated and locked in when the quote is created — it won't silently change later if the rate changes), plus an admin-entered **shipping/delivery estimate**. Clients see the full breakdown — Items Subtotal → Sourcing Service Fee (10%) → Estimated Shipping — before deciding.
- **Clients can accept or decline a quotation themselves.** From their quotation view, "Accept & Place Order" instantly converts it into a real order (with the service fee and shipping carried over as their own line items so the total matches exactly what was quoted) — no separate sync step, since it's the same database the admin panel reads. "Decline" marks it rejected. Both actions are one click, with a confirmation prompt first.

### Enabling outbound email (verification + password reset)

Set these environment variables to turn on real email sending:
```bash
export MAIL_SERVER="smtp.gmail.com"          # or your provider's SMTP host
export MAIL_PORT="587"
export MAIL_USE_TLS="true"
export MAIL_USERNAME="you@yourdomain.com"
export MAIL_PASSWORD="your-smtp-password-or-app-password"
export MAIL_DEFAULT_SENDER="Duoshao Trading <you@yourdomain.com>"
```
Without these, verification/reset emails are written to the server log instead of sent — useful for
testing (the link still works, you just have to go find it in the log), but obviously not for real use.


- **Sourcing request → quotation, in one click.** From the Sourcing Requests page, click "Create Quotation" on any request and it opens a pre-filled quotation form: customer matched automatically (if they have an account or an existing Customer record with the same phone/email), product name, quantity, and notes carried straight across. Saving it links the quotation back to the original request, auto-advances the request's status, and both records cross-reference each other — no re-typing or re-searching needed.

- **Fixed:** the public nav used to show "Login" even when a client was already logged in (it worked, just looked wrong) — now correctly shows "My Account" / "Log Out".
- **Fixed a real money bug:** cashiers could accidentally record more than a sale's balance due, inflating revenue. Now:
  - Non-cash methods (bank transfer, mobile money, etc.) **reject** any amount above the balance due outright.
  - Cash payments treat the entered amount as *cash tendered* — the receipt only ever records the balance due, and the UI tells the cashier exactly how much change to hand back if the customer paid with a larger note.
- **Inventory linkage:** Catalog items can now be linked to a Stock Control item. Once linked, the online store shows real remaining quantity, blocks checkout if someone tries to order more than is in stock, and automatically decrements stock when an online order is placed. (Admin-created in-person sales still manage stock manually, by design — free-text sale items can't be reliably auto-matched to a stock record.)
- **Order status & manual parcel tracking:** every sale now has a fulfillment timeline — Order Confirmed → Processing → Shipped → In Transit → Arrived → Out for Delivery / Ready for Pickup → Delivered (or Cancelled). Staff update it from the sale detail page (with carrier name + tracking number captured at "Shipped"); clients see the same timeline, with tracking info, on their own order page.
- **In-app notifications:** clients get a notification (bell-style link with an unread count) whenever their order's fulfillment status changes. This is in-app only for now — no email/SMS/WhatsApp integration yet (see "Suggested next additions").
- **Product images:** catalog items can have a photo uploaded from the admin Catalog page; shows on the public catalog grid.
- **Search:** the public catalog page now has a search box (matches name + description) alongside the category tabs.

## 8. What "fully functional e-commerce" still needs

Honest list of what's genuinely missing before this is a complete, production-grade store:

- **Real-time carrier tracking.** What's built is *manual* status tracking — staff update the stage themselves. Automatic tracking numbers that self-update from the carrier's own system would need either direct API integration with your freight forwarder (if they expose one — many smaller China→Africa forwarders don't) or a tracking aggregator service like 17TRACK / AfterShip (paid, needs its own API key, same pattern as PayPal/Stripe).
- **Email / SMS / WhatsApp notifications.** Right now all notifications are in-app (client has to log in to see them). Real email needs an SMTP or transactional email provider (e.g. SendGrid, Mailgun); WhatsApp needs the WhatsApp Business API (a paid, approval-gated Meta product).
- **Shipping cost calculation.** Delivery orders don't add a shipping fee yet — only the item subtotal is charged.
- **Order cancellation / refunds.** Clients can't cancel a pending order themselves; there's no refund workflow if a captured payment needs reversing.
- **Password reset.** No "forgot password" flow yet (needs the same email service as above).
- **CSRF protection.** Forms don't yet include CSRF tokens — worth adding (Flask-WTF makes this straightforward) before this handles real payment volume.

## 9. Notes before you rely on this day-to-day

- The dev server (`python run.py`) is fine for testing but **not for production**. For real use, deploy behind
  a proper WSGI server (e.g. gunicorn) and a reverse proxy, and move `SECRET_KEY` into an environment variable.
- The database is SQLite (`instance/duoshao.db`) — fine for one company at low-to-moderate volume. Back this
  file up regularly.
- "Float / Cash" is a manually-assisted ledger, not a bank feed — it only knows about cash movements you record.
- Update the social media links in `duoshao/templates/public/_footer.html` — they're placeholders right now.
- Cart checkout does not auto-decrement Stock Control inventory (Catalog and Stock Control are separate lists) — reconcile stock manually for now, or link them by SKU in a future iteration.
- PayPal/Stripe webhooks aren't set up — payment confirmation happens synchronously when the customer completes checkout in the same browser session. If they close the tab mid-payment, check the order manually and record a receipt once you confirm the money arrived.
- All money fields are USD only for now.

## 10. Suggested next additions

- Email/WhatsApp notification when a new sourcing request comes in, or when a client's order status changes
- PDF export (not just browser print) for quotations/receipts/payslips
- Multi-currency support (USD/RMB/ZWL)
- Client-side "Accept Quotation" button that notifies management, rather than relying on the admin to check
- Deployment guide for a real host (e.g. a small VPS with gunicorn + nginx + HTTPS)

