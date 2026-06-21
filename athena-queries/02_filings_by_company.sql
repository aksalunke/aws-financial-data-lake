-- Filing count per company per year
SELECT 
    company_standardised,
    year,
    COUNT(*) as filing_count,
    COUNT(DISTINCT form_type) as form_types
FROM financial_data_lake.curated_filings
GROUP BY company_standardised, year
ORDER BY year DESC, company_standardised;