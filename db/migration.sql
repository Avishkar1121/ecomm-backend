-- 1) Store "remember me" explicitly instead of inferring it from expiry length
ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS remember boolean NOT NULL DEFAULT false;

-- Backfill existing rows with the old heuristic (safe to run once)
UPDATE auth_tokens
   SET remember = true
 WHERE type = 'refresh' AND (expires_at - created_at) > interval '2 days';

-- 2) The refresh/reset UPDATE ... WHERE token_hash = :h relies on this being fast and unique
CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_tokens_token_hash ON auth_tokens (token_hash);

-- 3) Cleanup query: run daily from cron / pg_cron / a scheduled job
-- DELETE FROM auth_tokens
--  WHERE expires_at < now() - interval '7 days'
--     OR (used_at IS NOT NULL AND used_at < now() - interval '7 days');