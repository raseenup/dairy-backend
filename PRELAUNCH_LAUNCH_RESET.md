# First Milk Venture — one-time pre-public-launch test-data reset

The Super Admin dashboard contains **Launch Reset — Dispatch & Accounts** on **Overview**. This feature is intended to remove pre-launch **test** transactions before releasing the application publicly. It is **not** an accounting purge, a way to delete real transactions, or a recurring monthly/year-end reset.

## Permissions and server activation

Only a signed-in **Super Admin** (individual account authenticated with password and email OTP) can preview, export, prepare, or execute. Frontend role checks are not trusted: the backend performs authorization and safety checks for every request.

The function is **disabled by default**. Once the code is deployed to the backend and admin portal, the operator must deliberately set these backend environment variables for the launch-cleanup window:

- `FMV_ENABLE_PRELAUNCH_RESET=true`
- `FMV_LAUNCH_STAGE=prelaunch`

Use the correct, backed-up pre-launch workspace and database. Do not set these variables on an already-live public deployment.

Immediately after the one-time reset, **remove `FMV_ENABLE_PRELAUNCH_RESET` or set it to `false` and set `FMV_LAUNCH_STAGE=production`**. A server-side `prelaunch_reset_completed` record prevents re-running the reset even if the flags are accidentally left enabled.

## Workflow

1. Sign in as Super Admin. In the Admin Portal **Overview**, open **Launch Reset — Dispatch & Accounts**.
2. Select **Preview records**. Inspect the scope, number of records, inventory restoration quantities and any blocking safety conditions.
3. Select **Export test-data backup**. Verify that the exported JSON file is saved in a **private secure location**. The export may contain driver/customer contact information, order details, and invoice information. **Never commit the archive to GitHub or serve it as a public website asset.**
4. Select **Prepare final reset**. This creates a session-bound, single-use authorization valid for five minutes. Any intervening application-state change invalidates the authorization; start again after reviewing fresh data.
5. Confirm both the saved archive and any displayed product-stock restoration. Type exactly `RESET FIRST MILK VENTURE TEST DATA`, select the **Reset test Dispatch & Accounts data** action, and accept the browser's final confirmation.
6. Verify the empty dispatch/accounting dashboards, stock quantities, staff/driver/customer accounts, and system audit entry. Disable the reset environment variables before public launch.

## Cleared test-data categories

- Orders and subscriptions, driver shift logs, route incidents, SOS records, return pickups/claims, driver incentives, order conversations/attachments and related operational records
- Unpaid test invoices, pending driver expense submissions, unposted test accounting data, outstanding test cash/earning/request records (only if safety checks permit their removal)
- Checkout idempotency test keys, test-order emails still queued, and notifications that explicitly reference cleared operation IDs
- Reserved product stock is restored **only for non-cancelled test orders**. Verify displayed quantities first because prior manual stock edits can change the expected inventory balance.

## Protected and preserved records

The reset does **not** remove individual login accounts or credentials, customers, drivers, staff roles, OTP setup, catalog/prices/product media, company branding, tax configuration, contact/address details, vehicle/document files, pay rules, company website contents, audit history or persistent database configuration.

Safety gates automatically **block the reset** when there are real payment or capture references, collected COD, progressed deliveries, active shifts, scheduled return pickups, paid/settled invoices, registered cash receipts or handovers, driver payout/earnings records, approved expenses, refund liabilities, or currently sending transactional email. In such cases, preserve records for accounting, audit, and legal retention; do not bypass the checks to achieve an empty dashboard.

## Integration and validation

Backend: `prelaunch_reset.py` registers four authenticated routes:

| Endpoint | Description |
| --- | --- |
| `GET /admin/prelaunch-reset/preview` | Read-only counts, scope, safeguards and inventory preview |
| `GET /admin/prelaunch-reset/export` | Super Admin-only confidential JSON backup |
| `POST /admin/prelaunch-reset/prepare` | Expiring, session-bound authorization |
| `POST /admin/prelaunch-reset/execute` | One-time transactional cleanup after confirmations |

Frontend: `prelaunch-reset.js`, `prelaunch-reset.css` and `index.html` render the Admin Overview panel. No existing dashboard modules were removed.

Unit regression tests are in `test_prelaunch_reset.py`. These checks need to run in CI, followed by an isolated disposable staging-database test, before the change is merged or deployed. The PR is intentionally **draft** until safety verification is complete.
