-- Defense in depth: even an accidental future grant/permissive policy must not
-- allow direct browser access. FMV's authorized FastAPI database owner bypasses
-- RLS and continues checking the individual portal session and ownership.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
DO $fmv$
DECLARE item record;
BEGIN
    FOR item IN SELECT n.nspname AS schema_name,c.relname AS table_name
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND (n.nspname='fmv_secure' OR
            (n.nspname='public' AND c.relname=ANY(ARRAY['users','products','subscriptions',
             'orders','deliveries','offers','auth_otps','audit_logs','drivers','driver_shifts',
             'delivery_audit_logs','driver_expenses'])))
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname=item.schema_name
            AND tablename=item.table_name AND policyname='fmv_block_direct_clients') THEN
            EXECUTE format('CREATE POLICY fmv_block_direct_clients ON %I.%I AS RESTRICTIVE
                FOR ALL TO anon, authenticated USING (false) WITH CHECK (false)',item.schema_name,item.table_name);
        END IF;
    END LOOP;
END $fmv$;

-- Retain each legacy predicate, evaluated once per statement rather than once
-- per row. These policies remain restricted to the trusted service_role.
ALTER POLICY "User isolation deliveries" ON public.deliveries
    USING ((SELECT auth.email()) = user_email)
    WITH CHECK ((SELECT auth.email()) = user_email);
ALTER POLICY "Users can view own deliveries" ON public.deliveries
    USING (((SELECT auth.jwt()) ->> 'email') = user_email);
ALTER POLICY "Allow service role full access on offers" ON public.offers
    USING ((SELECT auth.role()) = 'service_role');
ALTER POLICY "Allow service role full access on products" ON public.products
    USING ((SELECT auth.role()) = 'service_role') WITH CHECK ((SELECT auth.role()) = 'service_role');
ALTER POLICY "Allow service role full access on subscriptions" ON public.subscriptions
    USING ((SELECT auth.role()) = 'service_role') WITH CHECK ((SELECT auth.role()) = 'service_role');
ALTER POLICY "User isolation subscriptions" ON public.subscriptions
    USING ((SELECT auth.email()) = user_email) WITH CHECK ((SELECT auth.email()) = user_email);
ALTER POLICY "Users can view own subscriptions" ON public.subscriptions
    USING (((SELECT auth.jwt()) ->> 'email') = user_email);

NOTIFY pgrst, 'reload schema';
COMMIT;
