# Architecture Decisions

## 1. Why AWS DMS over direct S3 export
DMS supports ongoing Change Data Capture (CDC) with minimal 
load on the source Aurora database. Direct export via SQL 
SELECT queries would lock tables during extraction — 
unacceptable for a production banking database processing 
live transactions. DMS reads from the Aurora binary log, 
adding negligible overhead.
Reference: AWS DMS documentation — CDC with Aurora MySQL.

## 2. Why three S3 zones, not two
The raw zone is immutable — data is written exactly as 
ingested and never modified. This satisfies audit trail 
requirements: regulators can always verify the source data 
was not altered before transformation. The curated zone 
holds cleaned, Parquet-format data partitioned by date. 
The refined zone holds aggregated, query-ready datasets.
A two-zone model loses the ability to re-derive any 
transformation from the original source.

## 3. Raw zone partitioned by filing_date, not ingest date

The raw zone uses filing_date (the SEC submission date) as the
partition key rather than the date the fetch script was executed.

Trade-off accepted: ingest batch timing is not captured in the
partition path, making it harder to isolate and reprocess a
specific fetch batch.

Trade-off avoided: re-running the fetch script overwrites the
same S3 key rather than creating duplicate partitions. In a
financial data pipeline, duplicate records are a harder failure
to detect and correct than a missing batch timestamp. This
preserves idempotency — the same filing always lands at the
same path regardless of when it was fetched.

Production extension: a dedicated landing zone partitioned by
ingest timestamp would sit upstream of the raw zone, capturing
every batch before deduplication. Deferred for portfolio scope.

## 4. Why Athena over Redshift for this use case
Athena is serverless — zero cluster management, 
pay-per-query pricing at $5 per TB scanned, reduced to 
approximately $0.50 with Parquet and partitioning. 
Redshift requires a provisioned cluster running 24/7, 
costing approximately $180 per month minimum. For 
irregular ad-hoc query patterns, Athena is 10-20x 
cheaper than Redshift.
Redshift is appropriate when query concurrency exceeds 
20 simultaneous users or complex joins across very large 
tables require MPP execution.

## 5. Why Lake Formation over IAM-only
S3 bucket policies and IAM policies operate at the 
object and prefix level — they cannot restrict access 
to specific columns within a Parquet file. Lake Formation 
adds a permissions layer on top of the Glue Data Catalog 
that enforces column-level security at query time in Athena.
This satisfies GDPR Article 25 (data protection by design) 
and PCI-DSS Requirement 7 (least privilege access).

## 6. Why Parquet format in the curated zone
Parquet is columnar — Athena scans only the columns 
referenced in a query. A query selecting 3 columns from 
a 50-column table scans approximately 6% of the data 
versus 100% with CSV. Combined with date partitioning, 
this reduces Athena query costs by 80-95% compared to 
unpartitioned CSV.
Reference: AWS Athena performance tuning documentation.

## 7. Why Kinesis over MSK for streaming (Project 2 reference)
Documented here for cross-project consistency. Kinesis 
is fully managed with no broker administration and 
integrates natively with Lambda. MSK is appropriate 
when the on-premises system requires Kafka-compatible 
protocol for hybrid connectivity.