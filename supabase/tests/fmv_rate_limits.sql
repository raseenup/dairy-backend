-- Exercise the deployed PostgreSQL UPSERT without leaving a test bucket behind.
BEGIN;
SET LOCAL statement_timeout = '15s';
DO $test$
DECLARE
    test_bucket text := '__fmv_v13_regression__';
    current_epoch double precision := 1000;
    until_epoch double precision;
    current_hits integer;
    attempt integer;
BEGIN
    IF EXISTS (SELECT 1 FROM fmv_secure.rate_limits WHERE bucket=test_bucket) THEN
        RAISE EXCEPTION 'Test bucket already exists; refusing to alter it';
    END IF;
    FOR attempt IN 1..8 LOOP
        INSERT INTO fmv_secure.rate_limits (bucket,until_epoch,hits) VALUES (test_bucket,current_epoch+60,1)
            ON CONFLICT (bucket) DO UPDATE SET
            hits=CASE WHEN fmv_secure.rate_limits.until_epoch<=current_epoch THEN 1
                ELSE LEAST(fmv_secure.rate_limits.hits+1,4) END,
            until_epoch=CASE WHEN fmv_secure.rate_limits.until_epoch<=current_epoch THEN EXCLUDED.until_epoch
                ELSE fmv_secure.rate_limits.until_epoch END
            RETURNING hits INTO current_hits;
        IF current_hits<>LEAST(attempt,4) THEN RAISE EXCEPTION 'Incorrect counter/saturation'; END IF;
    END LOOP;
    current_epoch:=1060;
    INSERT INTO fmv_secure.rate_limits (bucket,until_epoch,hits) VALUES (test_bucket,current_epoch+60,1)
        ON CONFLICT (bucket) DO UPDATE SET
        hits=CASE WHEN fmv_secure.rate_limits.until_epoch<=current_epoch THEN 1
            ELSE LEAST(fmv_secure.rate_limits.hits+1,4) END,
        until_epoch=CASE WHEN fmv_secure.rate_limits.until_epoch<=current_epoch THEN EXCLUDED.until_epoch
            ELSE fmv_secure.rate_limits.until_epoch END
        RETURNING hits INTO current_hits;
    IF current_hits<>1 OR (SELECT r.until_epoch FROM fmv_secure.rate_limits r WHERE bucket=test_bucket)<>1120 THEN
        RAISE EXCEPTION 'Expired rate window did not reset';
    END IF;
END $test$;
ROLLBACK;
SELECT 'Passed: PostgreSQL limit counts, saturation and exact-boundary rollover; test row rolled back' AS result;
