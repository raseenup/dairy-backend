-- Read-only authorization regression. No customer row is read or mutated.
-- Run as the database owner after applying the app migrations. All checks roll back.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
DO $test$
DECLARE
    relation record;
    app_role text;
    command text;
    statement text;
    first_column text;
    rejected boolean;
    starting_role text := current_user;
    required_tables constant text[] := ARRAY[
        'public.audit_logs','public.auth_otps','public.deliveries','public.delivery_audit_logs',
        'public.driver_expenses','public.driver_shifts','public.drivers','public.offers',
        'public.orders','public.products','public.subscriptions','public.users',
        'fmv_secure.application_state','fmv_secure.campaign_files','fmv_secure.campaign_upload_parts',
        'fmv_secure.driver_private_files','fmv_secure.order_chat_files','fmv_secure.product_media','fmv_secure.rate_limits'];
    checked_tables text[] := ARRAY[]::text[];
    checks integer := 0;
BEGIN
    FOR relation IN
        SELECT c.oid,n.nspname,c.relname,c.relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND (n.nspname='fmv_secure' OR
            (n.nspname || '.' || c.relname) = ANY(required_tables))
        ORDER BY n.nspname,c.relname
    LOOP
        checked_tables := array_append(checked_tables, relation.nspname || '.' || relation.relname);
        IF NOT relation.relrowsecurity THEN RAISE EXCEPTION 'RLS is disabled on %.%',relation.nspname,relation.relname; END IF;
        SELECT quote_ident(attname) INTO first_column FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped ORDER BY attnum LIMIT 1;
        FOREACH app_role IN ARRAY ARRAY['anon','authenticated'] LOOP
            IF has_table_privilege(app_role,relation.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(app_role,relation.oid,'SELECT,INSERT,UPDATE,REFERENCES') THEN
                RAISE EXCEPTION 'Unexpected client privilege for % on %.%',app_role,relation.nspname,relation.relname;
            END IF;
            FOREACH command IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE'] LOOP
                statement := CASE command
                    WHEN 'SELECT' THEN format('EXPLAIN SELECT * FROM %I.%I WHERE false',relation.nspname,relation.relname)
                    WHEN 'INSERT' THEN format('EXPLAIN INSERT INTO %I.%I SELECT * FROM %I.%I WHERE false',relation.nspname,relation.relname,relation.nspname,relation.relname)
                    WHEN 'UPDATE' THEN format('EXPLAIN UPDATE %I.%I SET %s=%s WHERE false',relation.nspname,relation.relname,first_column,first_column)
                    ELSE format('EXPLAIN DELETE FROM %I.%I WHERE false',relation.nspname,relation.relname) END;
                rejected := false;
                BEGIN
                    EXECUTE format('SET LOCAL ROLE %I',app_role);
                    EXECUTE statement;
                EXCEPTION WHEN insufficient_privilege THEN rejected := true;
                END;
                EXECUTE format('SET LOCAL ROLE %I',starting_role);
                IF NOT rejected THEN RAISE EXCEPTION '% can % %.%',app_role,command,relation.nspname,relation.relname; END IF;
                checks := checks+1;
            END LOOP;
        END LOOP;
    END LOOP;
    IF NOT (required_tables <@ checked_tables) THEN
        RAISE EXCEPTION 'Missing application tables: %',
            ARRAY(SELECT unnest(required_tables) EXCEPT SELECT unnest(checked_tables));
    END IF;
    IF has_function_privilege('anon','public.handle_new_user()','EXECUTE')
        OR has_function_privilege('authenticated','public.handle_new_user()','EXECUTE') THEN
        RAISE EXCEPTION 'Auth trigger function is callable by a client';
    END IF;
    IF NOT has_function_privilege('supabase_auth_admin','public.handle_new_user()','EXECUTE') THEN
        RAISE EXCEPTION 'Auth trigger administrator lost execute permission';
    END IF;
    -- The real server's verified postgres role still has normal access.
    PERFORM 1 FROM fmv_secure.application_state WHERE id=1;
    IF NOT FOUND THEN RAISE EXCEPTION 'Application state is missing'; END IF;
    RAISE NOTICE 'Verified % denied role/operation combinations on % tables', checks, cardinality(checked_tables);
END $test$;
ROLLBACK;
SELECT 'Passed: required tables are present, client access is denied, and server and Auth trigger access is retained' AS result;
