-- GROUP 1B: Restructure morning_watchlist in dev to match prod schema
-- Authorized by Joel, schema drift remediation 2026-07-13
-- Pre-conditions confirmed: dev rows = 0, grep shows zero app-code refs to old id-based schema.
-- Prod schema: ticker VARCHAR PK, added_at TIMESTAMPTZ, notes TEXT (830 rows, untouched).

-- Step 1: Drop the id-based primary key
ALTER TABLE morning_watchlist DROP CONSTRAINT morning_watchlist_pkey;

-- Step 2: Drop the UNIQUE constraint on ticker (will be replaced by PK)
ALTER TABLE morning_watchlist DROP CONSTRAINT morning_watchlist_ticker_key;

-- Step 3: Drop the id column (sequence auto-drops with it)
ALTER TABLE morning_watchlist DROP COLUMN id;

-- Step 4: Change ticker from text to varchar (match prod)
ALTER TABLE morning_watchlist ALTER COLUMN ticker TYPE VARCHAR;

-- Step 5: Make ticker the new primary key
ALTER TABLE morning_watchlist ADD PRIMARY KEY (ticker);

-- Step 6: Add notes column (present in prod, was missing from dev)
ALTER TABLE morning_watchlist ADD COLUMN IF NOT EXISTS notes TEXT;
