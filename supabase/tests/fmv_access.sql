-- Read-only authorization regression. No customer row is read or mutated.
-- Run as the database owner after the two v13 migrations; all work rolls back.
BEGIN;
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
    checks integer := 0;
BEGIN
    FOR relation IN
        SELECT c.oid,n.nspname,c.relname,c.relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND (n.nspname='fmv_secure' OR
            (n.nspname='public' AND c.relname=ANY(ARRAY['users','products','subscriptions',
             'orders','deliveries','offers','auth_otps','audit_logs','drivers','driver_shifts',
             'delivery_audit_logs','driver_expenses'])))
    LOOP
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
    IF checks<>144 THEN RAISE EXCEPTION 'Expected 144 denied operations, checked %; review table inventory',checks; END IF;
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
END $test$;
ROLLBACK;
SELECT 'Passed: 144 client operations denied; server state and Auth trigger access retained' AS result;
