-- Apply the same server-only policy used by the other private FMV tables.
SET LOCAL lock_timeout = '2s';
DO $fmv$
BEGIN
    IF to_regclass('fmv_secure.campaign_upload_parts') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                       WHERE schemaname='fmv_secure' AND tablename='campaign_upload_parts'
                         AND policyname='fmv_block_direct_clients') THEN
        CREATE POLICY fmv_block_direct_clients ON fmv_secure.campaign_upload_parts
        AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
    END IF;
END
$fmv$;
