-- Total filings in the data lake
-- Athena scans only the partitioned Parquet files in curated zone
SELECT 
    COUNT(*) as total_filings,
    COUNT(DISTINCT company_standardised) as unique_companies,
    MIN(filing_date) as earliest_filing,
    MAX(filing_date) as latest_filing
FROM financial_data_lake.curated_filings;