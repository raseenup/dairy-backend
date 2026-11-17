# Set up your first Super Admin on Render Free

This method uses private environment variables and the normal application start.
It does not need Render Shell, SSH, a paid one-off job, a public setup endpoint,
or a change to the service start command.

Deploy the code containing `bootstrap_admin.py` and the matching startup change
in `secure_backend.py` before using these settings. Keep the existing database,
JWT secret and verified email-provider configuration.

1. Open the Render Dashboard and select the backend web service.
2. Open **Environment → Add Environment Variable**.
3. Add both variables before saving:

   | Key | Value |
   | --- | --- |
   | `BOOTSTRAP_ADMIN_EMAIL` | The email address you will use for your Super Admin account |
   | `BOOTSTRAP_ADMIN_INVITATION` | A private random code you choose, 32–200 characters, without spaces |

   Keep the invitation value in Render's private settings. It is a temporary
   invitation, not your account password. Never commit it to GitHub or paste it
   into public posts, logs, screenshots or support tickets.

4. Select **Save, rebuild, and deploy** and wait for the service to become live.
5. Open `https://admin.firstmilkventure.com/` and choose **Sign up** or
   **Accept administrator invitation** if the updated entry screen is deployed.
6. Use the exact email configured above, enter your name, Indian mobile, address,
   and a new account password. Paste the value of `BOOTSTRAP_ADMIN_INVITATION`
   into **Administrator invitation code**.
7. Finish signup using the six-digit OTP delivered to your email. The verified
   account receives the Super Admin role.
8. After successful signup, remove both bootstrap variables in Render and save.
   Use your normal email, password and OTP for subsequent sign-ins.

The backend stores only the invitation hash and gives the invitation a 24-hour
lifetime. Restarting does not renew it. If it expires before signup, choose a
new private invitation value and redeploy. Changing the invited email also
requires a new private invitation value.

Bootstrap is disabled once any administrator account exists. It never overwrites
passwords, upgrades an existing user's role or bypasses email verification. For
an existing administrator, use normal sign-in/password recovery or staff
invitations instead.

Keep your existing persistent PostgreSQL/Supabase `DATABASE_URL`. Render Free's
local filesystem is ephemeral: local SQLite data can disappear on restart or
redeploy. Removing bootstrap variables should not remove your account when the
application uses the same persistent database.

Official Render instructions: [environment variables](https://render.com/docs/configure-environment-variables)
and [free service limitations](https://render.com/docs/free).

Validation: first-account setup, hashed/no-log invitation handling, expiry,
restart behavior, invalid settings, named-email binding and OTP verification are
covered by isolated tests in `test_bootstrap_admin.py`. No production account,
environment setting or real customer email is changed by those tests.
