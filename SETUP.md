# First Milk Venture v8 — independent backend setup

This repository contains the backend source-code upgrade. Customer, administrator and delivery portals remain in the independent `raseenup/firstmilk-customer`, `raseenup/firstmilk-admin` and `raseenup/firstmilk-delivery` repositories. Backend filenames are at this repository root; do not introduce a `Backend/` wrapper or copy the portals into it. Screenshots and archive-only integration tooling are intentionally excluded from these public runtime repositories.

This runbook documents a later deployment process; preparing this source branch/PR does not execute a migration, catalog import, invitation, provider change, production merge or deployment. Before production cutover, resolve historical data mapping (section 6), verify backup/restore, coordinate all four releases and review automatic deploy settings. Reverting code alone cannot undo database changes.

## What is connected

The customer, administrator and delivery-driver portals call the same backend. Products, prices, stock, orders, subscriptions, assignments, delivery status, cash collection, shifts, expenses, support tickets, shared documents and notifications are stored there. Browsers check a revision every six seconds and reload affected records. An empty delivery list stays empty; a network failure never becomes a successful order or delivery.

All financial transactions use INR. Addresses default to Kerala, India. Dates and work shifts use Asia/Kolkata. The administrator sets the applicable GST rate per product; no universal tax rate or statutory compliance certification is assumed.

## 1. Install and configure the backend

Use Python 3.12 in a disposable environment for validation. Confirm the currently configured hosting interpreter before any production change. From the backend repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Only for a new local checkout with no existing .env:
cp -n .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

On Windows activate with `.venv\Scripts\activate`. Put the generated unique value in `JWT_SECRET` in `.env`. Preserve any existing `.env` and hosting settings; the template is not an overwrite instruction. Do not use the example placeholder or any key from the old source. The application refuses to start with an invalid/default JWT secret.

Set `RESEND_API_KEY` and use a verified sending domain for `AUTH_EMAIL_SENDER`. OTP is delivered by email; SMS delivery is not implemented. Delivery failure produces an error and does not create an account or issue a token. Production source does not expose verification codes in API responses, browser hints or logs.

For initial local work, leave `DATABASE_URL` blank. SQLite persists data in `data/firstmilk.sqlite3`. Do not run local development with production database credentials.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Serve each independent portal repository over HTTP/HTTPS and configure its public `apiBase` in `config.js` to this backend. The existing production domains are preserved: customer `https://firstmilkventure.com`, administrator `https://admin.firstmilkventure.com` and driver `https://delivery.firstmilkventure.com`. For local portal servers, add their exact origins to backend `ALLOWED_ORIGINS` and review the portal CSP for the chosen backend. Do not use real production credentials for local development.

Optional backend routes `/customer/`, `/admin-portal/` and `/driver-portal/` support an explicit temporary integration layout with sibling portal folders. They return 404 when those assets are absent in this standalone checkout. They are not required for the separately hosted portals.

Do not open the HTML files using `file://`. Authentication, file APIs and browser storage work correctly when the files are served over HTTP/HTTPS.

## 2. Set up the first administrator

There is no default administrator account and the old shared passwords/driver keys do not authorize the active API.

From the backend host, using the same environment/database as the running service:

```bash
python manage.py owner@example.com --role super_admin
```

Replace the example address with the real administrator email. This outputs one private, named invitation that expires after 24 hours. On the administrator portal choose **Sign up**, enter that email, the invitation, name, Indian mobile number, address and a password with at least 12 characters including letters and numbers. Verify the six-digit email code.

An authenticated super administrator can create further invitations through **RBAC / staff invitations**. Staff roles are `super_admin`, `operations_manager`, `finance` and `customer_support`. Role checks run on the server; changing browser UI or headers does not grant permissions.

## 3. Register customers and drivers

Customers choose **Sign up**, supply contact details and a password, then verify the email code. Sign-in also requires a new email code after the password is verified. Email or the registered Indian mobile number can identify the account; codes are always sent to its registered email.

Drivers use the same process at the driver portal. Successful email verification creates a pending driver application. In Admin → Manage drivers, approve the registered driver. The driver can then sign in with password and OTP. An invitation-only profile created by the legacy driver form does not itself grant access.

The email code expires after five minutes, is limited to five incorrect attempts, cannot be reused to sign in twice, and has a 60-second resend delay. Password reset also requires a verified email code and invalidates older sessions. Sessions expire after 30 minutes and are revoked when signed out.

## 4. Check the connected order flow

1. In Admin, edit a product's INR price, available quantity and GST rate together. Saving checks its version so stale stock forms cannot overwrite a concurrent sale.
2. Sign in as a customer, add products and choose **Cash on delivery** or the configured **Online** payment option. Stock limits apply to the whole cart, including duplicate product lines.
3. Checkout reads product IDs and quantities. The server calculates prices/tax and deducts all stock in one transaction. An idempotency key prevents the same submitted checkout from deducting twice.
4. Admin's date/zone dispatch view shows the order. Assign an approved driver.
5. Driver completes the four pre-trip checks, supplies vehicle/odometer details and clocks in. The assigned order appears in Manifest.
6. At delivery, the driver supplies a real photo, the customer's four-digit delivery PIN and the actual COD amount collected. The server checks assignment, shift, PIN, photo and amount before confirming. The driver cannot view the customer's delivery PIN.
7. Confirmed delivery updates the customer's order status and invoice, the driver's Delivered/Cash Flow tabs, and Admin. Attendance, break events, expenses and SOS alerts are also available to Admin. Monthly attendance exports as CSV.

Recurring subscriptions support daily, weekly and alternate-day schedules. Admin's **Generate dispatches** action generates eligible deliveries for the selected date once per subscription/date and deducts stock then. It is intentionally explicit; a background scheduler is not silently enabled.

Unpaid online orders reserve stock for 30 minutes. Expired reservations are cancelled and stock returned on the next API request. A customer can cancel an unassigned/unpaid order; stock is restored once. Paid or already assigned orders require support handling.

## 5. Online payment configuration

Set server-only values:

```text
RAZORPAY_KEY_ID=your_merchant_key_id
RAZORPAY_KEY_SECRET=your_merchant_key_secret
RAZORPAY_WEBHOOK_SECRET=your_separate_webhook_secret
```

Configure the Razorpay webhook to the backend's `/webhooks/payment` endpoint and subscribe to `payment.captured`. Use test merchant credentials and test-mode transactions first.

Checkout creates the gateway order from the server total. The browser opens Razorpay's hosted payment window, supporting the methods enabled for the merchant account. The backend verifies the payment signature, fetches the payment from Razorpay, and checks order ID, captured status, amount and INR currency. Webhooks require an HMAC signature over the exact raw body and are protected against repeated events. An unsigned `payment.succeeded` JSON payload cannot mark anything paid.

Payment is never reported successful merely because the customer clicked a button. Without credentials, Online checkout reports that configuration is required; COD remains available. A paid event after stock reservation expiry requires support review rather than silently overselling. Refund review records an administrator's decision; it does not falsely claim to have transferred money. Gateway refunds and COD reimbursements need the merchant's normal financial process.

## 6. Supabase/PostgreSQL and the existing database

The supplied screenshots show Supabase, Render environment variables and existing public tables. No actual database export or credentials were included in the ZIP. This package has not connected to or changed a live account.

For production:

1. Back up the database through its normal administration process and verify restoration. Review/map historical accounts, orders, subscriptions, assignments and balances before cutover; the bundled SQL and catalog importer do not migrate them.
2. Apply `migration_v8_security.sql` as the database owner in Supabase SQL Editor.
3. Set `DATABASE_URL` to the server-side PostgreSQL connection URI and `APP_ENV=production`.
4. Use HTTPS and set `ALLOWED_ORIGINS` to the exact customer/admin/driver origins. The supplied domains are present as defaults. Do not use a wildcard.

The migration is additive: it creates `fmv_secure.application_state` and `fmv_secure.rate_limits`. It retains existing tables, columns and records. It enables RLS and removes anonymous/authenticated direct access to the known legacy public business tables shown in the screenshot. The active API uses trusted PostgreSQL transactions, not browser PostgREST access. Use a server database role with the necessary privileges and keep its credentials out of all portal files. Transaction-pooler prepared statements are disabled in the Python client. Production connections verify both TLS certificates and the database hostname. Set `PGSSLROOTCERT` to the CA certificate downloaded from the database dashboard if it is not trusted by the system certificate store.

The canonical v8 transactional state is separate from the legacy public tables. Historical accounts, orders, subscriptions, assignments and balances are not automatically assumed to have been migrated. Their schemas and rows must be mapped and reviewed before using v8 with existing business history. Do not run both old and new portals against different sources of stock.

An optional safe catalog import is included:

```bash
python import_legacy_catalog.py
# Review actual product prices as INR before the next command:
python import_legacy_catalog.py --apply --prices-are-inr
```

This imports product IDs/prices/stock without changing the original `public.products` table. It retains starter entries and refuses to import once v8 orders exist. Review duplicates in Admin. Currency labels do not perform foreign-exchange conversion.

If PostgreSQL fails, production does not silently fall back to another database. Explicit SQLite production use requires `ALLOW_PRODUCTION_SQLITE=true`, a durable `FMV_DB_PATH`, backups and one deployment instance. Avoid ephemeral Render disks for persistent SQLite data.

## 7. Existing separate portal domains

Each independent portal repository includes its `index.html`, role-specific controller, `config.js`, `portal-core.js`, `portal.css`, `tailwind.css` and existing `CNAME`. Keep these files together at that repository root. The duplicate archive `CNAME.txt` files are excluded; existing CNAME bytes and DNS/Pages settings remain unchanged.

For separately hosted customer/admin/driver domains, `config.js` defaults to `https://backend.firstmilkventure.com`. Change its public `apiBase` if your backend address differs, and allow the exact portal origins on the backend. In an explicit temporary combined integration layout only, optional backend-served portal paths use the same origin; independent repository hosting does not require that layout.

For this standalone backend, preserve the existing hosting root and `main:app` entrypoint. A root-level Render service would install `requirements.txt`, run from the repository root, and use a start command such as:

```bash
uvicorn main:app --host 0.0.0.0 --port "$PORT"
```

Copy every supporting backend module, not just `main.py`. Do not commit `.env`, databases, service-role keys or merchant secrets. The existing `.gitignore` is preserved with an appended private-runtime exclusion block. `.env.example` is a committable template; real environment files, databases, caches and virtual environments stay ignored.

## 8. Other retained portal sections

- Support tickets/replies, knowledge articles, help chat, human escalation tickets, community posts/votes, quotes converted to real COD orders, RMA review, surveys, notifications and branding preferences use persisted account-scoped records.
- Private document sharing and support attachments accept PDF, PNG, JPEG or UTF-8 text up to 1.5 MB. Only the owner customer can download them. A filename alone is not treated as an upload.
- Sales invoices download as PDF or JSON. Statements export as CSV. These are sales documents; statutory GST e-invoice/IRN registration is not claimed.
- Team records currently manage contacts/invitation preferences. Full multi-user company tenancy and delegated access to another customer's orders are not granted by these contact roles.
- Enterprise SSO, external AI, ERP/CRM adapters, autopay mandates, credit underwriting and statutory e-invoice services require real provider integrations. The controls/sections are retained and show configuration errors or clear status instead of fabricated successes. Saving a branding preference does not provision DNS or a custom domain.
- SOS is recorded for the administration portal. It is not a connection to emergency dispatch services.

The archive requirement-link files and screenshots remain outside these runtime repositories. The supplied package reported inaccessible external requirement links; their unseen contents are not claimed verified.

## 9. Verification and practical limits

Run the API regression suite:

```bash
python -m pytest -q
```

The archive also includes a DOM integration harness and Node package files. They remain outside the four runtime repositories. To run that optional harness, use a disposable integration workspace containing separate copies of the backend and each portal under the folder names expected by the harness; use an isolated Python process and mocked providers. Do not make a combined Git repository or add a Node runtime requirement to this backend.

Run tests in a clean child process with no real provider credentials or production `.env`. The test fixture forces an empty `DATABASE_URL`, a fresh temporary SQLite path and test-only signing settings before importing the application, and disables dotenv loading in that test process. Email is mocked; payment provider calls are mocked by tests that exercise them. A regression uses a harmless sentinel database URL to check isolation. Never run tests against the live database or change the parent/live environment for a test. No test-only behavior is enabled in production application startup.

The archive `VALIDATION_REPORT.md` describes historical package results, not validation of this branch; current PR evidence must report its own exact results. Browser rendering and physical iPhone/Safari checks still need a reachable deployed test environment. The driver overlap fixes use a normal-flow sticky header, measured height, mobile wrapping, safe-area padding and separate fixed bottom navigation. DOM tests verify structure/state; they cannot prove pixel-perfect appearance on every device.

The transaction state uses a locked JSON record for consistent small-deployment behavior. High-volume enterprise use should normalize business tables, separate file/object storage, add background jobs and perform load/security testing with the actual hosting environment. Passing isolated tests does not certify security or establish that live services are configured.

## 10. Local CSS build

Compiled Tailwind 3.4.17 styles and build sources belong to each independent portal repository. This backend does not build or serve those assets in production. The portals do not execute the Tailwind CDN runtime. For future style edits, run this from the affected portal repository root:

```bash
npx tailwindcss@3.4.17 -c tailwind.config.cjs -i styles/tailwind-input.css -o tailwind.css --minify
```

Build each portal independently; its Tailwind content paths cover its own root HTML/JavaScript files. The original icon-font stylesheet is decorative and does not authorize access.

The active Content Security Policy does not allow inline JavaScript. The shared controller converts the retained source event attributes into listeners for a fixed list of application actions, without `eval`. If the backend domain changes, update its allowed `connect-src` in each portal's CSP meta tag as well as `config.js` and the server origin list.
