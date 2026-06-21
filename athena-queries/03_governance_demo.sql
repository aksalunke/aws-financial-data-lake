-- Governance demonstration query
-- Run this as data-lake-director: returns all columns including cik_masked
-- Run this as data-lake-analyst: cik_masked and accession_number
-- are absent from the result set entirely (not NULL — the columns
-- themselves are not visible, since this uses Include columns)
SELECT *
FROM financial_data_lake.curated_filings
LIMIT 10;