-- ADDITIVE migration. Does not drop existing tables, columns, records or schemas.
-- Run as the Supabase database owner in the SQL editor before deploying v8.
BEGIN;
CREATE SCHEMA IF NOT EXISTS fmv_secure;
REVOKE ALL ON SCHEMA fmv_secure FROM PUBLIC;
CREATE TABLE IF NOT EXISTS fmv_secure.application_state (
    id integer PRIMARY KEY CHECK (id = 1),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS fmv_secure.rate_limits (
    bucket text PRIMARY KEY,
    until_epoch double precision NOT NULL,
    hits integer NOT NULL
);
CREATE INDEX IF NOT EXISTS fmv_rate_until ON fmv_secure.rate_limits (until_epoch);
REVOKE ALL ON ALL TABLES IN SCHEMA fmv_secure FROM PUBLIC;
-- The screenshot showed legacy public tables as UNRESTRICTED.
-- Browser anonymous/authenticated roles must not read or mutate these via PostgREST.
-- Backend uses DATABASE_URL directly as a trusted server role. Existing data is retained.
DO $$
DECLARE t text; r text;
BEGIN
    FOREACH t IN ARRAY ARRAY['audit_logs','auth_otps','deliveries','delivery_audit_logs',
        'driver_expenses','driver_shifts','drivers','offers','orders','products','subscriptions','users']
    LOOP
        IF to_regclass('public.' || quote_ident(t)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC',t);
            FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=r) THEN
                    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I',t,r);
                END IF;
            END LOOP;
        END IF;
    END LOOP;
    FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA fmv_secure FROM %I',r);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA fmv_secure FROM %I',r);
        END IF;
    END LOOP;
END $$;
COMMIT;
