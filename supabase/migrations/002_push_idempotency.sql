-- Add idempotency key support to push logs
ALTER TABLE crm_push_logs ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX idx_push_logs_idempotency
    ON crm_push_logs (org_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
