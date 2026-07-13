-- GROUP 3: Add missing PKs to dev (prevents Replit from dropping them from prod)
-- Authorized by Joel, schema drift remediation 2026-07-13
-- All 4 dev tables confirmed 0 rows before execution.

ALTER TABLE aiem_finding_embeddings    ADD PRIMARY KEY (research_date);
ALTER TABLE aiem_ticker_reference_cache ADD PRIMARY KEY (ticker);
ALTER TABLE ticker_lifecycle            ADD PRIMARY KEY (ticker);
ALTER TABLE vix_daily                   ADD PRIMARY KEY (scan_date);
