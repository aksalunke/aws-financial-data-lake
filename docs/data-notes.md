# Data Notes

## Phase 5 — Raw to Curated ETL

Raw zone: 12 partitions keyed by ingest_year/ingest_month.
Curated zone: 12 partitions keyed by year/month.

Partition counts tally exactly, confirming the ETL correctly
read each row's filing_date value and wrote it to the
corresponding curated partition.