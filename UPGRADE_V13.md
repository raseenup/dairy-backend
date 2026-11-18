# First Milk Venture v13 — private data, clear prices and faster portal updates

The Supabase warnings were real: all twelve legacy `public` tables had RLS
disabled. `auth_otps` also had an all-access policy assigned to `public` despite
its service-role label. The active portals use FastAPI's individual sessions;
the server uses `DATABASE_URL` and the private `fmv_secure` schema, not browser
Supabase sessions. Browser `auth.uid()` policies would not match this architecture.

## Database repair

The following migrations were applied to the connected production database and
are retained here with their actual Supabase history versions:

1. `supabase/migrations/20261118012113_harden_fmv_database_access.sql`
2. `supabase/migrations/20261118012309_block_direct_clients_and_optimize_policies.sql`

They enable RLS on all eighteen application tables, revoke client table/column
and sequence privileges, restrict the original legacy policies to `service_role`,
and add an explicit restrictive client-denial policy. This also protects against
accidental future grants or permissive policies. The private schema remains
inaccessible to clients. Existing rows, schemas, columns and triggers remain.

`handle_new_user()` keeps its original qualified `public.users` INSERT and Auth
trigger. Its search path is fixed and direct client execution is revoked. Auth's
administrator retains execution. New objects created by the app's `postgres`
owner no longer grant anonymous/authenticated access automatically. Objects
created by other roles still need their own reviewed grants and policies.

The database's verified server role is `postgres` with `BYPASSRLS`; normal
FastAPI operations keep their existing privileges and ownership checks. No new
browser privilege, service-role key, JWT secret or paid service is introduced.
The two advisor-reported missing foreign-key indexes were added, and legacy
Auth policy predicates now use scalar SELECTs to avoid per-row evaluation.

After application, Security Advisor returned **zero findings**. Performance
Advisor returned no warnings/errors; its only two informational entries concern
the newly created indexes not yet being used. Keep those foreign-key indexes.
The main state's user/product/order/subscription counts and revision were
unchanged by the repair. These checks do not establish whether earlier exposure
was exploited; historic anonymous data-access logs were not exhaustively audited.

## Portal changes and deployment

Deploy this backend and the three coordinated portal changes. The security
migrations above are already applied on the connected project; deploying the
Python code does not need to run them again. For another database, inspect its
schema/roles and access model first: these migrations target the verified legacy
tables, policy names and Auth trigger, not an arbitrary Supabase project.

The matching portals retain ISO `INR` in payment/API payloads, configuration and
downloaded financial records. Everyday prices use ₹; unnecessary currency labels,
the one-option currency selector, the auth-location suffix and “INR checkout”
are removed or hidden. Money-input labels use ₹ where units are needed.

Each portal includes `polish.css` after its existing styles and uses short,
role-specific brand copy with paired light/dark colors. No layout, business
module, original function or request schema is removed. This backend includes
the new stylesheet in its explicit static asset allowlist for same-origin
deployments; independently hosted portals serve it with their other local assets.

## Performance and security behavior

- Script loading is deferred in the original order. HTML starts locked so a
  slow connection cannot flash a private workspace before session validation.
- Visible online pages check revisions every second, refresh immediately after
  local mutations, pause while hidden/offline and back off up to 30 seconds on
  connection errors. This is near-live polling, not WebSocket push or a guarantee
  of hosting response time; free-host sleeping/cold starts still affect latency.
- Identical markup is not repeatedly replaced, preserving focus, quantity input,
  open details and media while that section's data is unchanged. Changed data
  still renders. Rendering state is cleared on sign-out.
- Concurrent GETs coalesce only when session, mutation generation, URL and
  normalized headers match. A mutation cannot reuse an older pending GET.
- The rupee formatter is reused instead of constructed for every amount.
- PostgreSQL/SQLite rate-limit counters now use a single atomic UPSERT per hit.
  Expired windows reset at the exact boundary; rejected attempts remain counted,
  counters saturate, and occasional bounded cleanup replaces a full expired-row
  sweep on every live request. Session revocation is still checked on every poll.

## Verification

### Render and Supabase TLS

With `APP_ENV=production`, PostgreSQL uses `verify-full` for both certificate
and hostname verification. Managed production hosts under `*.pooler.supabase.com`
use `certs/supabase-production-roots.pem`. The connection pool loads this
choice once at startup. Other hosts use the system trust store. An explicit
`PGSSLROOTCERT` or connection-string `sslrootcert` takes precedence and must
name a trust store available to the deployed process.

The bundle contains the two public production certificates distributed with
the Supabase CLI. The first certificate also matches the Supabase dashboard
download. Review certificate rotations against these upstream sources.

- [Production certificate issued in 2021](https://github.com/supabase/cli/blob/0d1a3be4cd00f660b3678759cc39b879d64787bb/apps/cli/src/command-internal/supabase-ca-templates/prod-ca-2021.ts)
- [Production certificate issued in 2025](https://github.com/supabase/cli/blob/0d1a3be4cd00f660b3678759cc39b879d64787bb/apps/cli/src/command-internal/supabase-ca-templates/prod-ca-2025.ts)
- [Supabase SSL configuration](https://supabase.com/docs/guides/platform/ssl-enforcement)

### Original v13 checks

`python -m pytest -q`: **121 passed**. This includes concurrent rate limits,
window rollover, bounded cleanup, existing auth/OTP, checkout/stock, roles,
snapshots, chat/media, payroll and ad regressions. Existing dependency deprecation
warnings are unrelated to these changes.

Each portal's `tests/portal-core.test.cjs` passes six isolated DOM/network tests
with Node and jsdom 27.2.0 (**18 passed** across the portals). Tests cover retained
focus/input, changed markup, header-aware read coalescing, mutation/read races,
session replacement, bearer scoping, offline pause, backoff and revocation.

Run `supabase/tests/fmv_access.sql` as the owner after migrations: **144 direct
client SELECT/INSERT/UPDATE/DELETE attempts denied**, server state and Auth trigger
access retained. `supabase/tests/fmv_rate_limits.sql` verifies PostgreSQL counting,
saturation and rollover; its test row is rolled back. Both passed on production.

Browser QA used isolated sample data: all three dashboards loaded with deferred
scripts, both themes were inspected, and customer checkout/admin catalog/driver
views had no repeated visible INR labels. Real customer credentials, live
payments, actual phone keyboard behavior and production load were not exercised.
