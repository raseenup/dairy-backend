# First Milk Venture v10 — portal fixes and driver cash

Deploy this backend before updating the three portals. Keep the existing database,
JWT secret, origins, email-provider key and first-admin setup. Existing users,
orders, stock, original source modules and request schemas remain in place.

## Deployment

1. Install the updated `requirements.txt`; it adds `psycopg_pool`. The service
   start command remains `uvicorn main:app --host 0.0.0.0 --port 8000`.
2. Set the department senders below in the backend's private Environment settings.
   Each sender must be accepted by your email provider on your verified company
   domain. No email-provider key belongs in a portal or GitHub.
3. Deploy the backend change, then the admin, customer and driver portal changes.
   The portals now use `/portal/snapshot`; deploying a portal first will fail
   until the matching backend is available.
4. The additive `product_media` table is created at startup using the existing
   server database connection. Its schema must already exist from
   `migration_v8_security.sql`; the database role needs permission to create this
   table. Product image bytes are stored in PostgreSQL, alongside the existing
   durable application state, rather than on a free host's temporary filesystem.
5. Test one order, cancellation, driver approval and accounts receipt using your
   own test accounts. Check **Invitations → Mail delivery** for queued/retry/sent
   messages. A provider acceptance is not proof of inbox delivery.

No paid realtime service, Render Shell or scheduled external job is required.
The first-super-admin workflow in [FREE_RENDER_ADMIN.md](FREE_RENDER_ADMIN.md)
continues to work.

## Email routing

| Email purpose | Backend setting | Default sender |
| --- | --- | --- |
| Sign up, sign in, password reset | `AUTH_EMAIL_SENDER` | auth@firstmilkventure.com |
| Order placed or cancelled, subscription | `ORDER_EMAIL_SENDER` | orders@firstmilkventure.com |
| Shipped, out for delivery, delivered | `DELIVERY_EMAIL_SENDER` | delivery@firstmilkventure.com |
| Driver profile approval | `DRIVER_EMAIL_SENDER` | drivers@firstmilkventure.com |
| Administrator invitation | `ADMIN_EMAIL_SENDER` | admin@firstmilkventure.com |
| Accounts cash receipt, payment receipt | `FINANCE_EMAIL_SENDER` | accounts@firstmilkventure.com |
| Support updates | `SUPPORT_EMAIL_SENDER` | support@firstmilkventure.com |
| Offer messages | `MARKETING_EMAIL_SENDER` | offers@firstmilkventure.com |
| Security notices | `SECURITY_EMAIL_SENDER` | security@firstmilkventure.com |

The router covers all active mail-sending functions and supported template
purposes; it does not create new campaigns or new event triggers for unused
templates. Change these addresses if your designated departments use different
mailboxes. The old `ORDER_EMAIL_SENDER=...auth@...` setting is automatically
replaced by the orders default; department mail never falls back to auth@.
An invalid setting or provider rejection stays visible in delivery status and
is retried without rolling back an order or cash receipt.

Cancellation emails are queued once in the same transaction that releases
stock, including expired online-payment reservations. Driver approval queues one
email and a driver-only notification; repeating approval does not duplicate it.
Every rendered message includes its purpose and the existing company ad.

## Customer and product changes

- The header bell and search controls are removed. Search remains in the bottom
  navigation; notifications remain inside Profile. The mobile header and main
  content stay within the viewport.
- Successful COD checkout renders the returned order immediately and opens
  **Profile → My Orders**. Cancellation immediately replaces that order's status.
- In admin **Catalog**, choose **Edit details & image** on any product. Edit its
  name, description, unit, price, GST, stock and threshold; upload, replace or
  remove a JPEG, PNG, WebP or animated GIF. New products also accept an upload.
- Uploads are limited to 1.5 MB with decoded image/frame/pixel checks. SVG and
  executable files are rejected. Uploaded product images are intentionally
  public catalog assets. Delivery proof and customer documents stay private.
- Editors submit the version they opened. A concurrent sale/edit causes a
  conflict instead of silently restoring old stock. Reload the product before
  retrying a conflicting edit.
- Verified driver signup shows a persistent application-submitted banner.
  Drivers must wait for approval before signing in.

## Order sections and counts

Admin **Logistics** has Unassigned, Assigned, Cancelled and Completed sections,
plus Awaiting payment and Failed sections so every status remains accessible.
Picked up, in transit and arrived are grouped under Assigned; Delivered is
grouped under Completed. Closed orders have no assignment controls.

The header **Unassigned** button and Logistics badge show the total across all
dates/zones. Queue sections respect the visible date/zone filters. Clicking the
header button clears the date and opens the Unassigned section. These counts
and order lists are available only to roles with order-management access.

## Driver cash handover

Balances carry across dates and shifts:

**Outstanding = confirmed COD collected − approved expenses − accounts cash receipts.**

1. The driver records actual shift expenses. New expenses and existing expenses
   that have no review status await accounts review; they do not automatically
   clear accountable cash.
2. Finance or Super Admin opens **Finance → Driver cash & accounts receipts**
   and approves or rejects each expense.
3. The driver may choose **Cash flow → Submit cash handover**, enter the amount
   handed over and a reference. Submission stays pending and does not clear cash.
4. After receiving the money, Finance/Super Admin chooses **Confirm cash
   received**, or records a direct receipt for the driver. Enter the actual
   amount and accounts reference, confirm physical receipt, then save.
5. The outstanding balance decreases by that receipt. Partial receipts are
   supported; the driver sees the receipt, updated balance and confirmation email.
   Accounts can reject an incorrect pending handover without reducing cash.

Receipts and expense decisions retain the responsible account and timestamp.
Driver accounts and operations managers cannot confirm accounts receipts.
Retries use idempotency keys, and simultaneous receipts cannot over-clear the
balance. No API deletes the collection or receipt history. If approved expenses
exceed collections, the screen shows an amount owed to the driver separately;
recording a receipt is an accounting action, not a bank transfer or reimbursement.

## Faster updates and validation

Routine reads use committed snapshots without an exclusive application-state
lock or rewrite. Each portal batches its main data into one authenticated
snapshot. PostgreSQL reuses a small TLS-verified connection pool. Product image
bytes live outside the polled state row and receive immutable public caching.

Visible portals check the authenticated revision endpoint every two seconds,
without overlapping polls. Own changes trigger immediate refreshes; duplicate
GETs and refreshes are combined. Hidden pages pause polling and refresh on
return. Failed connections back off. OTP mail and password hashing run outside
the state-write lock; ownership and account changes are rechecked on commit.
Stock changes, receipts and other writes remain transactional. A sleeping host,
slow network or mail-provider delay can still affect response time.

Validation: 91 isolated backend tests, all portal JavaScript syntax checks and
diff checks pass. Coverage includes email events/routing, image validation,
scope/authorization, reservation release, optimistic stock conflicts, partial
and concurrent receipts, and reads/checkouts during slow OTP mail. Providers
are stubbed; live inbox delivery, the production PostgreSQL connection and a
complete mobile/desktop visual pass still require deployment verification.
