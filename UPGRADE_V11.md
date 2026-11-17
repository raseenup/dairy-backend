# First Milk Venture v11 — driver workspace and customer order upgrades

Deploy this backend before the coordinated customer, admin and driver portal
changes. Keep your existing JWT secret, database, email credentials and origins.
No additional paid service or new secret is needed for the local route planner.
Existing source modules, data and original request schemas remain available.

## Deployment and setup

1. Deploy the backend with the existing requirements and start command.
   Startup adds `fmv_secure.driver_private_files` in PostgreSQL, or the equivalent
   SQLite table. The trusted database role needs table-creation permission in
   the existing schema. Existing records are retained. Driver file bytes live
   outside the application-state row so snapshots do not load them.
2. Deploy each updated portal, including its new `workspace.js/css`,
   `workforce.js/css` (admin/driver) or `orders.js/css` (customer).
3. Confirm that `ORDER_EMAIL_SENDER` uses your verified orders address. It now
   sends subscription started/paused/resumed/cancelled emails as well as the
   existing order events. Other department senders from v10 are unchanged.
4. In admin **Drivers**, configure incentive instructions, route locations/costs
   and earnings rules as needed. No pay rates, tips, customer ratings or profit
   figures are invented when records/settings are missing.
5. Super Admin can use **Invitations → Designate driver / payout managers** to
   grant additional rights to an individual administrator. Driver management
   and accounts/payout approval are separate permissions.

The existing free-host first-admin setup remains in `FREE_RENDER_ADMIN.md`.
Use a persistent database; a temporary hosting filesystem is not durable storage.

## Customer orders and subscription emails

**Profile → My Orders** now has Recent, Delivered and Cancelled sections with
counts. Recent contains orders that are neither delivered nor cancelled,
including a failed order requiring follow-up. Subscriptions remain visible in
their own section. A cancelled subscription cannot be silently resumed; create
a new one if another schedule is wanted.

Creating a subscription and each actual pause, resume or cancellation queues a
purpose-specific email in the same transaction. Repeating an unchanged status
does not queue another email. Cancelling a subscription stops future generated
deliveries; already-created orders keep their recorded status. All messages
retain the existing company advertisement and use the orders sender.

Customers can open order conversations and rate their own delivered orders.
Updating a rating replaces that order's previous rating, preventing duplicate
ratings from inflating a driver's score.

## Driver profile navigation

The driver Profile now has an identity card and account menu similar to the
customer Profile:

| Area | Contents |
| --- | --- |
| Earnings & wallet | Base pay, verified tips, peak bonuses, approved incentives, adjustments, COD holdback, net earnings, payout requests and ledger |
| Incentive instructions | Assigned instructions, delivery target, progress and completion submission |
| Performance & ratings | Delivered/running orders, failed attempts, completed shifts, delivery-date completion and actual customer reviews |
| Vehicle & document wallet | Vehicle details, private document upload/download and expiration status |
| Edit profile | Existing contact and assigned-area form |
| Notifications | Existing approval, cash and new workforce notifications |
| Safety, language & settings | SOS, language, dark mode and notification sound |

Sign out is at the bottom of Profile. The global session footer is hidden.
The driver header contains the brand instead of language/SOS controls. In admin,
sign out and display/sound controls appear at the top-right of Overview only.
Customer display/sound controls appear inside Profile. Preferences are saved
per individual account; notification audio needs browser interaction and is
subject to the device/browser's audio policies.

## Failed delivery attempts and order chat

Each running driver order has **Report failed delivery attempt** and **Chat with
customer** actions. An active working shift is required to report an attempt.
The driver records a reason and explanation. The report appears in admin Drivers
and notifies the customer. The order remains running for dispatch or retry;
this is distinct from closing an order with the existing Failed status.

Conversations are scoped to the order owner, currently assigned driver and
authorized support/driver managers. Reassignment immediately removes the former
driver's access. Closed orders retain readable history for authorized parties
but reject new messages. Message text is escaped in the UI. Messages and attempt
reports use bounded input, rate limits and idempotency protection.

## Incentive instructions and approval

1. Super Admin, Operations Manager or a designated driver manager publishes
   instructions with an INR reward, assigned approved drivers and a minimum
   number of completed deliveries after publication. Publishing retries use an
   idempotency key to avoid duplicate campaigns.
2. Assigned drivers see the instructions and progress. The completion button is
   disabled when no instructions are assigned, a target is incomplete, a claim
   already exists or the instructions are closed.
3. The driver attests completion and supplies a note. Submission creates a
   pending review; it does not award money.
4. A manager reviews the actual instructions and claim. Approval creates one
   incentive earnings entry. Repeated approval cannot credit it again. A manager
   cannot review their own driver claim unless they are Super Admin.

Free-text instructions require human review. The software can check recorded
delivery counts, but it cannot independently verify arbitrary instructions.
Closing instructions prevents new claims and retains previously submitted work
and its review history.

## Earnings, cash holdback and payout requests

Accounts (Finance, Super Admin or a designated payout approver) configures base
pay per completed delivery, peak-hour bonus and applicable India-time hours.
Rates apply to deliveries completed after configuration. Delivery earnings are
recorded once only after the existing photo/customer-PIN/COD checks succeed.
Verified tips, back pay and other adjustments can be entered with an accounts
reference. Drivers cannot create their own pay rates or earnings entries.

The wallet shows:

**Net earnings = gross approved earnings − other deductions − completed payouts
− outstanding COD cash balance.**

**Available to claim = max(0, net earnings − pending/approved payout requests).**

COD cash is held back from payout availability until accounts records receipt;
it is not erased or deducted twice. See `UPGRADE_V10.md` for cash handovers.
An approved expense reduces the driver cash balance through that existing flow.

Drivers request cash or bank payout within the available balance. Only the last
four digits of a privately verified bank account are requested; full bank
credentials are not collected here. Requests reserve funds. Accounts approves
or rejects, then records actual payment with a reference and explicit payment
confirmation. A pending request cannot jump directly to Paid. Funds and COD are
rechecked at approval and payment, so a new collection cannot be ignored.
Concurrent requests cannot reserve the same funds twice.

This is an earnings ledger and approval workflow. It does not execute bank
transfers, issue stored-value money or pretend that an approved request is paid.

## Route planning and privacy

Admin **Drivers → Route planning** shows an automatically recalculated stop
sequence and coordinate map for unassigned deliveries or a selected driver.
Enter a depot and travel/stop costs, product cost per sale unit and missing
delivery coordinates. Customers may pin a saved address manually or explicitly
use current location while at that delivery address. Saved pins are copied into
future orders; changing an address does not rewrite an existing order's location.
Dispatch can separately correct a running order's coordinates.

Drivers see their own stop map, estimated distance and road-direction links.
They can explicitly use current location as the route start. The driver route
API omits product costs, operating cost, margin and profit fields entirely.

The local planner respects delivery-date groups and uses nearest-neighbour plus
2-opt to reduce estimated travel distance for the required stops. All required
orders are retained. It does not claim a globally optimal profit, use live road
traffic or guess address coordinates. The map is a coordinate itinerary;
**Open road directions** opens the chosen itinerary for road navigation. No
addresses are automatically submitted to a third-party geocoding service.

Admin contribution estimates use configured product costs and travel/stop costs
before unconfigured overheads. Missing cost/location data produces an explicit
setup/partial state rather than a fabricated profit number. No paid map or
geocoding account is required for this local estimate.

## Vehicle documents and performance

Drivers can save a vehicle plate/model and upload PDF, JPEG, PNG or WebP files up
to 1.5 MB, with an expiration date. Files require an authenticated owner or
authorized fleet manager to download, use attachment disposition and are never
public catalog assets. Expired and next-30-day documents are labelled. Renewing
a standard document for the same vehicle supersedes its prior metadata while
retaining the private history. Documents marked Other can coexist.

Performance uses recorded deliveries, attempts and shifts. Delivery-date
completion compares the actual day against the recorded delivery date; it is
not a promised-hour metric. Ratings come only from authenticated owners of
delivered orders. An unrated driver displays no fabricated average. Admin's
driver-details controls open the same performance/document information.

## OTP usability, refreshes and verification

OTP emails now start their plain-text body with the contiguous six-digit code,
show a selectable unspaced HTML code and retain purpose-specific subjects.
The portal uses `autocomplete="one-time-code"` and accepts pasted grouped digits.
Automatic email-to-keyboard suggestions depend on the device/mail app; manual
copy/paste remains supported. Codes are not placed in email subjects.

Visible portals check authenticated revision status every 1.5 seconds, pause
while hidden, refresh on resume and retry/back off after failures. Main reads
remain batched; admin's larger driver-management data is fetched only for that
workspace. Chat refreshes while open. Stock, earnings, payouts and approvals
remain transactional, with live ownership/permission checks. Network latency,
sleeping hosts and mail-provider delivery time still apply.

Validation is recorded in the coordinated PRs: isolated backend regressions,
all-script syntax/asset checks and DOM workflow checks for each complete portal.
DOM checks cover behavior and escaping; they do not replace real iOS/Android
layout checks, live inbox delivery or production database verification.
