-- =====================================================
-- ZEROAUDIT – PostgreSQL Initialization Script
-- Creates the `transactions` table with bank signature
-- and inserts sample data.
-- =====================================================

-- Switch to the database (already created via POSTGRES_DB env)
\c zeroaudit;

-- Enable UUID extension (optional, for better IDs)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Main transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    transaction_id VARCHAR(100) UNIQUE NOT NULL,
    account_id VARCHAR(100) NOT NULL,
    amount NUMERIC(15,2) NOT NULL,          -- can be negative for debits
    balance NUMERIC(15,2) NOT NULL,         -- resulting balance after transaction
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    bank_signature TEXT NOT NULL,            -- ECDSA signature from the bank
    bank_public_key TEXT NOT NULL,           -- Bank's public key (for demo, we store it here)
    metadata JSONB                           -- Any extra data (e.g., channel, location)
);

-- Index for CDC performance
CREATE INDEX idx_transactions_timestamp ON transactions(timestamp);

-- Sample data (signatures are placeholders – the prover will verify them)
INSERT INTO transactions 
    (transaction_id, account_id, amount, balance, bank_signature, bank_public_key, metadata)
VALUES
    ('txn_001', 'acc_12345', 5000.00, 15000.00, 
     '3045022100e476f5059c12b6f5e2f3a5b8b9a0c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3', 
     'MFYwEAYHKoZIzj0CAQYFK4EEAAoDQgAEKf3Hf2p8yLmzqW5XkYq1nGxP6M9Q8R7S6T5U4V3W2X1Y', 
     '{"type": "credit", "source": "bank_transfer"}'),
    
    ('txn_002', 'acc_12345', -2000.00, 13000.00, 
     '3046022100d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d1e2', 
     'MFYwEAYHKoZIzj0CAQYFK4EEAAoDQgAEKf3Hf2p8yLmzqW5XkYq1nGxP6M9Q8R7S6T5U4V3W2X1Y', 
     '{"type": "debit", "source": "atm_withdrawal"}'),
    
    ('txn_003', 'acc_67890', 10000.00, 25000.00, 
     '304402207b6a5d4c3b2a1f0e9d8c7b6a5d4c3b2a1f0e9d8c7b6a5d4c3b2a1f0e9d8c7b6a', 
     'MFYwEAYHKoZIzj0CAQYFK4EEAAoDQgAEt8s7v6u5j4i3h2g1f0e9d8c7b6a5d4c3b2a1f0e9d8c', 
     '{"type": "credit", "source": "salary"}');

-- Optional: create a role for the Debezium connector (if needed)
-- GRANT SELECT ON transactions TO zeroaudit_user;