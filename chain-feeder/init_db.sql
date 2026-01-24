-- Create the data warehouse database
CREATE DATABASE chaintelligence;

-- Switch to the new database to create tables
\c chaintelligence;

CREATE TABLE IF NOT EXISTS lp_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    address VARCHAR(42),
    protocol VARCHAR(50),
    network VARCHAR(50),
    position_label VARCHAR(255),
    balance_usd NUMERIC,
    assets JSONB,
    unclaimed JSONB
);
