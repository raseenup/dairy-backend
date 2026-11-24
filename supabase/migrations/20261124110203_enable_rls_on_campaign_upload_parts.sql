-- Temporary advertising upload chunks are private to the FastAPI database role.
-- The backend also enables RLS when it first creates the table on a fresh installation.
SET LOCAL lock_timeout = '2s';
ALTER TABLE IF EXISTS fmv_secure.campaign_upload_parts ENABLE ROW LEVEL SECURITY;
