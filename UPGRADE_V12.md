# First Milk Venture v12 — readable themes, private chat media and flexible pay

Deploy the backend first, then all three matching portals. Keep the existing
database, JWT secret, allowed origins, department email senders and credentials.
No new paid service, account or secret is required.

## Deployment

Startup creates `campaign_files` and `order_chat_files` in the existing
`fmv_secure` PostgreSQL schema (equivalent tables for SQLite). The database role
needs table-creation rights. Existing campaign IDs and asset URLs are preserved:
legacy campaign bytes are copied into the file table before being removed from
the shared state row. Back up the persistent database before deployment as usual.
Existing modules, source schemas, orders, documents and financial records remain.

Deploy the portals with their updated `portal-core.js`, `workspace.js/css` and
their role-specific files. Use HTTPS. If the static host sets Permissions-Policy,
allow `microphone=(self)` for recording; the backend now uses this policy. Access
is still requested by the browser only after the user chooses Record voice.

## Changes

- All portals use paired dark surfaces and readable light inks, with mint, blue,
  gold and coral accents. Navigation, form fields, safety checkboxes, time cards,
  empty states, status chips, dialogs and tables have explicit dark styles.
- My Orders retains Recent / Delivered / Cancelled. Subscriptions now have
  independent Active / Paused / Cancelled sections and live counts. Cancelled
  subscriptions keep their history without pause/resume controls.
- Both sides of an order conversation can send one photo or audio attachment
  per message, with or without text. Maximum file size is 1.5 MB; conversations
  allow up to 100 uploads. In-browser voice recording stops at one minute and
  stops when the page is hidden or the conversation closes. Audio-file upload
  remains available when microphone recording is unsupported or denied.
- Photos are validated, resized to at most 1920 pixels and re-encoded to JPEG,
  removing metadata such as camera GPS and flattening chat animation. Supported
  voice containers are audio-only WebM/Opus, Ogg, M4A, MP3 and WAV. Container
  checks are not a malware/codec scanner. Real Opus and AAC samples are tested.
- Attachment bytes stay outside the shared state row. Every download requires
  the current individual session and rechecks order ownership/assignment; an
  unshared upload is visible only to its uploader. Reassignment revokes the old
  driver's access. URLs contain no session token. Responses use no-store and
  nosniff, and the frontend revokes in-memory media URLs on close/sign-out.
- Chat fetches only messages after its cursor, in bounded pages. Attachments load
  on demand. Campaign files are cached as immutable public assets; private media
  is never publicly cached. Larger responses are compressed. Existing 1.5-second
  visible-page revision polling, batching, hidden-page pause and backoff remain.
  These are near-live updates, not WebSockets or a guarantee of hosting latency.

## Base-pay schedules (admin Drivers → Pay schedule & peak rules)

Choose No base pay, Per completed delivery, Daily, Weekly or Monthly. The base
amount applies to the selected period, not to every delivery in a fixed-pay
schedule. Peak bonuses remain per verified delivery. Existing per-delivery rules
stay effective until replaced by a dated rule. Future rules are shown separately.

Fixed periods start on the rule's effective date. Weekly periods are seven days;
monthly periods follow the same anniversary, clamped to a shorter month's last
day, returning to the original anniversary in longer months. Only a full elapsed
period appears under Completed base-pay periods. Accounts or a designated payout
approver reviews it and chooses Review & credit period. One immutable source key
prevents duplicate credits, including concurrent requests. Payout review and COD
cash holdbacks continue to apply; this does not execute a bank transfer.

Fixed pay is contractual and is not inferred from shift attendance. Saved rules
cannot be backdated or overwritten. A new fixed-pay rule must start on a boundary
of the previous fixed period, avoiding undocumented proration/overlap. Use a
referenced earnings/deduction adjustment for past corrections.

## Home advertising strip (admin Ads & offers)

Choose Home ad strip (former address area), upload a GIF, animated WebP, MP4 or
image, or create a text post. Set artwork width (240–1600 px), height (80–1000 px)
and fit (show the whole image or crop to fill). The frame scales to the available
screen width; text flows below it rather than being clipped. Existing publishing,
scheduling, animation controls and delegated content-editor permissions apply.
The old Home address strip is removed. Addresses remain in Profile and checkout.

## Email verification

Email codes remain six contiguous, selectable digits. The preheader and plain
text start with the code; emails explain the purpose and expiry. Sign-up, sign-in
and the new password-reset dialog use a single `autocomplete="one-time-code"`
field. Paste code reads the clipboard only on an explicit tap; normal paste and
grouped-code paste work without clipboard access. No code is placed in the subject
or URL, and no email app or keyboard suggestion is controlled by the portal.

See Apple's [HTML AutoFill guidance](https://developer.apple.com/documentation/security/enabling-password-autofill-on-an-html-input-element).
Actual suggestions depend on the device, mail app, browser and settings.

## Verification

Run `.venv/bin/python -m pytest -q` (or the project's configured Python runtime).
The v12 tests cover private image/audio access, EXIF removal, reassignment,
idempotent uploads, invalid media, cursor reads, actual Opus/AAC fixtures, fixed
pay boundaries, concurrent period credit, ad validation and OTP formatting.

Isolated full-portal DOM workflows cover subscription filtering, ad placement and
dimensions, pay schedule submission, image/voice attachment forms, OTP paste and
password-reset fields. Sampled dark-mode text/background pairs exceed 4.5:1.
Real phone layout, microphone permissions/recording, keyboard suggestions, real
inbox delivery and production PostgreSQL migration remain deployment checks.
