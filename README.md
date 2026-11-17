# First Milk Venture v11 driver workspace

See [UPGRADE_V11.md](UPGRADE_V11.md) for customer order sections, subscription
emails, the driver profile menu, incentives, cash-aware earnings/payout approvals,
private vehicle documents, ratings, order chat and coordinate route planning.
Deploy the backend before all three matching portals. Existing v10 setup and
historical documentation remain below.

---

# First Milk Venture v10 portal fixes

See [UPGRADE_V10.md](UPGRADE_V10.md) for the coordinated backend/portal deployment,
department email senders, product photo/GIF editing, live order queues and
accounts-confirmed driver cash handovers. Deploy the backend first. Run
`python -m pytest -q` for the 91 isolated backend regressions.

The v9 and original project documentation is preserved below for reference.

---

# First Milk Venture v9 update

For the first Super Admin on **Render Free**, follow
[FREE_RENDER_ADMIN.md](FREE_RENDER_ADMIN.md). Private environment settings create
an invitation at startup; no paid Shell or public setup endpoint is needed.

See [UPGRADE_V9.md](UPGRADE_V9.md) for administrator invitations, customer status
emails, saved addresses, dedicated Profile navigation and the Ads & offers editor.
Deploy the backend before the portal changes. Run `python -m pytest -q` for the
64 isolated backend tests.

---

# First Milk Venture v8 — active backend

Read [SETUP.md](SETUP.md) before deployment. This is the current runbook; the original README is preserved below as historical package context.

All three portals use authenticated backend operations and persistent storage. Run `uvicorn main:app --host 0.0.0.0 --port 8000` from the repository root after installing `requirements.txt` and setting a unique JWT secret plus verified email delivery credentials. Production PostgreSQL setup uses the additive `migration_v8_security.sql`.

The active security/persistence routes are installed by `secure_backend.py` at the end of `main.py`. Every original request-schema class remains in source and OpenAPI. Old shared-key login routes and fake success responses are superseded. Individual staff invitations are created with `python manage.py your-email@example.com --role super_admin`; driver signup requires administrator approval.

Use `python -m pytest -q` for the isolated backend tests. Do not deploy test fixtures or enable test mail delivery in production. Online payments require configured Razorpay credentials and signature verification; unconfigured integrations do not pretend to succeed.

---

## Original README retained for reference (superseded instructions)

# First Milk Venture — Multi-Page Portals Package

## Files Included
1. `driver.html`: Standalone Driver Portal with 5-tab bottom navigation, working time attendance, delivery manifest, ePOD confirmation, cash flow ledger, and translator.
2. `customer.html`: Standalone Customer Portal displaying active orders, subscribed vs. single purchase details, and auto-generated 4-digit delivery OTPs.
3. `admin.html`: Standalone Admin Portal displaying all order OTPs, driver attendance logs, and one-click monthly working hours CSV report download.
4. `app-core.js`: Shared state management script connecting all three portals using LocalStorage for real-time synchronization.

## Instructions
Extract all files into the same folder and open `driver.html`, `customer.html`, or `admin.html` in your web browser.
