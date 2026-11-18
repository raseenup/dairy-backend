-- First Milk Venture: verified server-only access model (FastAPI + DATABASE_URL).
-- Browser clients use individual FMV sessions, not Supabase Data API roles.
-- This migration changes permissions, policy roles and indexes only. It retains
-- every existing table, column, row, original policy expression and trigger.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $fmv$
DECLARE
    item record;
    legacy_tables text[] := ARRAY['users','products','subscriptions','orders',
        'deliveries','offers','auth_otps','audit_logs','drivers','driver_shifts',
        'delivery_audit_logs','driver_expenses'];
BEGIN
    FOR item IN
        SELECT n.nspname AS schema_name,c.relname AS table_name
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND
            ((n.nspname='public' AND c.relname=ANY(legacy_tables)) OR n.nspname='fmv_secure')
        ORDER BY n.nspname,c.relname
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',item.schema_name,item.table_name);
        EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM PUBLIC, anon, authenticated',item.schema_name,item.table_name);
        -- Table-level REVOKE does not remove a pre-existing column-level grant.
        EXECUTE (SELECT format('REVOKE ALL (%s) ON TABLE %I.%I FROM PUBLIC, anon, authenticated',
            string_agg(quote_ident(a.attname),', '),item.schema_name,item.table_name)
            FROM pg_attribute a WHERE a.attrelid=format('%I.%I',item.schema_name,item.table_name)::regclass
            AND a.attnum>0 AND NOT a.attisdropped);
    END LOOP;
    -- Preserve legacy policy definitions for history, restrict them to the
    -- trusted service role. In particular, auth_otps had USING(true) TO public.
    FOR item IN SELECT * FROM pg_policies
        WHERE schemaname='public' AND tablename=ANY(legacy_tables)
    LOOP
        EXECUTE format('ALTER POLICY %I ON %I.%I TO service_role',item.policyname,item.schemaname,item.tablename);
    END LOOP;
    FOR item IN
        SELECT DISTINCT seq.oid::regclass AS sequence_name
        FROM pg_class seq JOIN pg_depend d ON d.objid=seq.oid
        JOIN pg_class t ON t.oid=d.refobjid JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE seq.relkind='S' AND d.deptype IN ('a','i')
        AND n.nspname='public' AND t.relname=ANY(legacy_tables)
    LOOP
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC, anon, authenticated',item.sequence_name);
    END LOOP;
END $fmv$;

REVOKE ALL ON SCHEMA fmv_secure FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA fmv_secure FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fmv_secure FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fmv_secure FROM PUBLIC, anon, authenticated;

-- Keep the existing qualified public.users INSERT and Auth trigger intact.
ALTER FUNCTION public.handle_new_user() SET search_path = '';
REVOKE EXECUTE ON FUNCTION public.handle_new_user() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.handle_new_user() TO supabase_auth_admin;

-- Objects created by this app's verified postgres owner must opt in to Data API
-- access. Existing service_role and server-owner privileges are preserved.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
-- PUBLIC function EXECUTE is a global default: schema-only REVOKE is insufficient.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA fmv_secure
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA fmv_secure
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA fmv_secure
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

-- The two missing foreign-key indexes reported by the live performance advisor.
CREATE INDEX IF NOT EXISTS fmv_orders_subscription_idx ON public.orders (subscription_id);
CREATE INDEX IF NOT EXISTS fmv_subscriptions_product_idx ON public.subscriptions (product_id);

NOTIFY pgrst, 'reload schema';
COMMIT;
