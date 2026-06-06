-- ChainWatch Per-User Alpaca Credentials Migration 006
-- Adds columns to users table for storing per-user Alpaca paper trading API keys
-- All columns are nullable: NULL means the user has not connected an Alpaca account.

-- Encrypted Alpaca API key (AES-256-GCM ciphertext, base64-encoded)
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_api_key_enc TEXT;

-- Initialization vector for API key encryption (base64-encoded 12-byte nonce)
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_api_key_iv TEXT;

-- Encrypted Alpaca secret key (AES-256-GCM ciphertext, base64-encoded)
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_secret_key_enc TEXT;

-- Initialization vector for secret key encryption (base64-encoded 12-byte nonce)
-- CRITICAL: each encrypted value must have its own IV to avoid nonce reuse attacks
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_secret_key_iv TEXT;

-- Alpaca paper trading account identifier returned from GET /v2/account
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_paper_account_id TEXT;

-- Timestamp of when the user connected their Alpaca paper trading account
ALTER TABLE users ADD COLUMN IF NOT EXISTS alpaca_connected_at TIMESTAMP WITH TIME ZONE;
