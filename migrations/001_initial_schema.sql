-- ChainWatch Database Schema
-- PostgreSQL migration file

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wallet_address VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    session_token TEXT,
    session_expires_at TIMESTAMP WITH TIME ZONE
);

-- Wallets table
CREATE TABLE IF NOT EXISTS wallets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    address VARCHAR(255) NOT NULL,
    chain VARCHAR(10) NOT NULL CHECK (chain IN ('eth', 'sol', 'btc')),
    label VARCHAR(255),
    is_whale BOOLEAN DEFAULT FALSE,
    is_mine BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wallet_id UUID REFERENCES wallets(id) ON DELETE CASCADE,
    tx_hash VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,
    amount DECIMAL(30, 18),
    token VARCHAR(50),
    usd_value DECIMAL(20, 2),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    chain VARCHAR(10) NOT NULL,
    from_address VARCHAR(255),
    to_address VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Alerts table
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    rule_type VARCHAR(50) NOT NULL,
    threshold DECIMAL(20, 2),
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Whale suggestions table
CREATE TABLE IF NOT EXISTS whale_suggestions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chain VARCHAR(10) NOT NULL CHECK (chain IN ('eth', 'sol', 'btc')),
    address VARCHAR(255) NOT NULL,
    label VARCHAR(255),
    source VARCHAR(255),
    added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Copy trade signals table
CREATE TABLE IF NOT EXISTS copy_trade_signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wallet_id UUID REFERENCES wallets(id) ON DELETE CASCADE,
    token_address VARCHAR(255),
    token_symbol VARCHAR(50),
    action VARCHAR(20) NOT NULL,
    amount_usd DECIMAL(20, 2),
    confidence_score DECIMAL(5, 2),
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    executed_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets(user_id);
CREATE INDEX IF NOT EXISTS idx_wallets_address ON wallets(address);
CREATE INDEX IF NOT EXISTS idx_transactions_wallet_id ON transactions(wallet_id);
CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_tx_hash ON transactions(tx_hash);
CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_whale_suggestions_chain ON whale_suggestions(chain);
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_wallet_id ON copy_trade_signals(wallet_id);

-- Pre-seed whale suggestions
-- HIGH-1 FIX: Add unique constraint on (chain, address) so ON CONFLICT works correctly.
-- Without this, ON CONFLICT DO NOTHING targets only the PK (UUID) and never prevents
-- duplicate (chain, address) rows from re-running the seed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_whale_suggestions_chain_address
    ON whale_suggestions(chain, address);

INSERT INTO whale_suggestions (chain, address, label, source) VALUES
-- Ethereum whales
('eth', '0x28C6c06298d514Db089934071355E5743bf21d60', 'Binance Hot Wallet', 'public'),
('eth', '0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549', 'Binance Cold Wallet', 'public'),
('eth', '0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8', 'Anchorage Digital', 'public'),
('eth', '0x56Eddb7aa87536c09CCc2793473599fD21A8b17F', 'Crypto.com', 'public'),
('eth', '0xDFd5293D8e347dFe59E90eFd55b2956a1343963d', 'Kraken', 'public'),
-- Solana whales
('sol', '5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1', 'Raydium Authority', 'public'),
('sol', '9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM', 'Binance SOL', 'public'),
('sol', 'HXVJVK5HtoCVLfALx9RPN2rbX7gKBUDRQM7XhUqppump', 'Pump.fun Authority', 'public'),
('sol', '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8', 'Raydium AMM', 'public'),
('sol', 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4', 'Jupiter Aggregator', 'public'),
-- Bitcoin whales
('bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh', 'Binance BTC', 'public'),
('bc1qazcm763858nkj2dj986etajv6wquslv8uxwczt', 'Bitfinex Cold', 'public'),
('34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo', 'Binance Cold BTC', 'public'),
('bc1qsxdxm0v65y8d4jw24k9vcf8nq9g7qkvq2uqygm', 'Kraken BTC', 'public'),
('btc', '1P5ZEDWTyMmXPXjVZM295zXq4Y5q1gGq1q', 'Unknown Whale', 'public')
ON CONFLICT (chain, address) DO NOTHING;
