# First Milk Venture v9 — deployment and everyday use

This upgrade adds visible administrator invitations, customer status emails, a
dedicated Profile area, saved delivery addresses and a publishing workspace.
Deploy the backend before the three portal updates. Existing data and original
request-schema classes remain in place.

## Administrator invitation field

1. Open `https://admin.firstmilkventure.com/`.
2. Select **Accept administrator invitation** on the welcome screen, or **Sign up**.
3. Enter your name, mobile number, address/assigned area, invited email address,
   password and **Administrator invitation code**.
4. Enter the six-digit email code to finish registration.

An invitation is private, email-specific, single use and valid for 24 hours.
An existing administrator signs in with their own password and email OTP;
they do not enter an invitation on every sign-in.

For the first Super Admin, the trusted backend owner runs this in the backend
directory, with the real deployment environment configured:

```bash
python manage.py your-email@example.com --role super_admin
```

Copy the generated private code into the administrator sign-up form. Do not
put the code in a public repository or publish it in a screenshot. This command
creates the invitation; it does not create a verified account or send an email.

After the first Super Admin signs in, open **Invitations** in the admin
navigation. Enter the new staff member's email, choose their role and click
**Create & email invitation**. The result also provides a private copyable code.
Creating a new invitation for the same email invalidates the earlier unused
invitation and cancels its unsent email.

## Customer navigation

| Area | Contents |
| --- | --- |
| Home | Delivery location, campaign banners, fresh menus/posts and catalog |
| Offers | Published offers and company stories |
| Search | Product search overlay |
| Cart | Items, quantities, payment method and selected saved delivery address |
| Profile | Actual customer name, mobile, email, account menu and Logout |
| Profile → Manage Address | Add, edit, remove, select and set a default address |
| Profile → My Orders & subscriptions | Existing orders, delivery PINs, subscriptions and reorder actions |
| Profile → FMV Wallet | Named wallet area with accurate availability information |
| Profile → Account workspace | Existing account/team, help, returns, billing, documents and business tools |
| Profile → Help & FAQs | Knowledge base and support tickets |
| Profile → Company information | Terms, privacy, refund, about and contact pages published by administrators |

Profile and Logout controls are removed from the Home header. All existing
account tools are retained in their assigned account area. FMV Wallet is the
requested name and navigation area; a stored-value payment/top-up service has
not been introduced. The page states that wallet payments are unavailable.
“Request account deletion” opens a support request; it does not silently erase
an account. Company policy text must be supplied and published by the company.

## Saved addresses

Customers can save up to 20 addresses with labels such as Home, Work or Parents.
Each record contains a recipient name, Indian mobile number and full delivery
address. The original profile address becomes the first saved address.

The customer chooses an address in Cart before placing an order. The server
checks ownership and records the selected address, recipient and phone in the
order. Editing or removing a saved address does not change existing orders.
The default address is also used for recurring deliveries that have no explicit
selected address. Selecting another checkout address creates a fresh checkout
idempotency key in the portal.

## Customer emails and company banners

Set these server-only environment variables using a verified Resend sender:

```dotenv
RESEND_API_KEY=your_real_server_only_key
AUTH_EMAIL_SENDER=First Milk Venture Auth <auth@firstmilkventure.com>
ORDER_EMAIL_SENDER=First Milk Venture <auth@firstmilkventure.com>
```

Never put real credentials in frontend `config.js` or GitHub. Keep the existing
`JWT_SECRET`, database and `ALLOWED_ORIGINS` configuration from `SETUP.md`.
The sender domain must be verified with the email provider. This package does
not change DNS records or send test messages to real customers.

| Trigger | Email purpose |
| --- | --- |
| Account signup | Sign-up verification code |
| Account signin | Sign-in verification code |
| Forgot password | Password reset code |
| New administrator invitation | Administrator invitation and private code |
| Order created | Order placed, including any pending payment state |
| Driver selects Picked Up / admin selects Mark shipped | Order shipped |
| Driver selects In Transit or Arrived / admin selects Out for delivery | Out for delivery; includes the delivery PIN |
| Valid delivery PIN, photograph and payment checks succeed | Order delivered |

Every renderer variant includes a visible purpose and an FMV company banner in
HTML and plain text. To replace the default banner, open **Ads & offers**, create
a campaign and choose **Company banner in emails**. If several email banners
are published, the lowest display order is used. Video campaigns use their text
in email; image campaigns may also show their uploaded image.

Order/invitation messages are recorded in a durable outbox in the same database
transaction as the event. They are sent after commit, with a background worker
checking every 30 seconds while the backend is running. Provider failures retry
with backoff; the order and its stock deduction remain committed. A repeated
checkout or delivery event does not create another message for the same event.
Authentication codes send synchronously and retain their five-minute lifetime.

Super Admins can review queued, retry, sent and expired invitation messages from
**Invitations → Email delivery status**. “Sent” means the provider accepted the
message; inbox placement and final delivery are not independently confirmed.
The same provider idempotency key is used for retries. Automatic retries stop
before the provider's 24-hour deduplication window ends; `needs_review` records
require checking provider delivery history before any manual resend. See the
[provider's idempotency documentation](https://resend.com/docs/dashboard/emails/idempotency-keys).

## Ads, offers, animated menus and company pages

Super Admins have publishing access. They can invite a **Content Manager** or
designate an existing administrator as a content editor from **Invitations**.
A designated editor receives campaign and company-page access; their original
finance, support or operations permissions stay tied to their role.

Open **Ads & offers → Add ad or post**. Enter the title/description, choose the
content type and location, select an optional file and click **Upload & publish**.
Uploading the file and saving the campaign are handled by that single submit.
Existing cards provide Edit, Publish/Unpublish and Remove actions.

Supported uploads: PNG, JPEG, GIF, animated WebP and MP4, up to 1.5 MB per file.
Images are limited to 4096 pixels per dimension, 12 megapixels and 300 frames.
Use browser-compatible MP4 video. Choose fade, slide or no entry animation;
optional start/end times and display order control publishing. Multiple Home
banners form a carousel with previous/next and pause controls. Reduced-motion
preferences disable entry animations and automatic slides; animated media
requires an explicit play action in that mode.

Advertising media is public. Customer documents and delivery photographs remain
behind their existing private routes. Posts and descriptions are treated as
plain text. Publishing an offer does not automatically change checkout prices;
use Catalog to set the actual product price.

Use **Ads & offers → Help, policies & company pages** to publish the company's
approved terms, privacy/refund policies, about and contact information. Empty
pages are identified as unpublished in the customer Profile area.

## Deployment

1. Back up the current application database.
2. Merge/deploy the backend changes first, including `experience.py` and the
   updated `secure_backend.py` and `email_templates.py`.
3. Keep the existing PostgreSQL `migration_v8_security.sql` for new deployments.
   The v9 collections are additive fields in the existing JSON/JSONB state row;
   there is no new relational migration to run.
4. Confirm server-only Resend credentials and verified sender settings.
5. Restart the backend with the existing command:

   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

6. Merge/deploy Admin and Customer, including their new `experience.js` and
   `experience.css`, then the shared Driver update. Publish every changed file;
   updating `index.html` alone is insufficient.
7. Refresh the portals. Create/accept a staff invitation, publish the first
   campaign and use a test customer to confirm the configured email provider.

## Verification completed

- `python -m pytest -q`: **55 passed** using isolated state and stubbed mail.
- All changed JavaScript files passed `node --check`.
- DOM smoke checks: **16 passed**, exercising 68 real local API requests through
  FastAPI's test client. Covered invitation forms, publishing, address checkout,
  Profile navigation, role restrictions and designation/revocation of editors.
- No JavaScript errors occurred in those DOM checks.
- The environment blocked the browser preview through its URL policy. Pixel
  layout, actual video playback, and real iPhone/Safari behavior were not
  visually verified. The screenshot references were used to implement the layout.
- No live customer mail, production payment or production database operation was
  performed. Real email delivery needs the production provider configuration.

All original project source files and original request-schema classes remain
included. New controllers extend the existing authenticated application.
