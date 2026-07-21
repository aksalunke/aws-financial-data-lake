# Architecture Decisions

## 1. Why SEC EDGAR public API over Aurora + AWS DMS
The original project plan included Amazon Aurora as a simulated source database with AWS DMS for Change Data Capture. This was replaced with direct ingestion from the SEC EDGAR public API
because it produces real, verifiable financial filing data at no cost and with no authentication requirement — strengthening portfolio credibility over synthetic data.

DMS provides CDC capability to minimise load on a live source database processing transactions. Since this project has no live source database, DMS adds no value and was correctly dropped.
The EDGAR API fetch script (scripts/edgar_fetch.py) replaces the entire Aurora + DMS layer with a direct HTTP call to a public REST endpoint.

Note: Macie, AWS Backup, and VPC Access Points were also in the original plan but are not yet implemented. These are documented as a planned Project 1 extension — see docs/setup-guide.md for
the current implemented scope.

## 2. Why three S3 zones, not two
The raw zone is immutable — data is written exactly as ingested and never modified. This satisfies audit trail requirements: regulators can always verify the source data was not altered
before transformation. The curated zone holds cleaned, Parquet-format data partitioned by year/month. The refined zone is provisioned (see infrastructure/cloudformation/s3-data-lake.yaml)
but not yet populated — reserved for future aggregated, BI-ready rollup tables built from the curated zone.

A two-zone model loses the ability to re-derive any transformation from the original, untouched source.

## 3. Raw zone partitioned by filing_date, not ingest date
The raw zone uses filing_date (the SEC submission date) as the partition key rather than the date the fetch script was executed.

Trade-off accepted: ingest batch timing is not captured in the partition path, making it harder to isolate and reprocess a specific fetch batch.

Trade-off avoided: re-running the fetch script overwrites the same S3 key rather than creating duplicate partitions. In a financial data pipeline, duplicate records are a harder failure
to detect and correct than a missing batch timestamp. This preserves idempotency — the same filing always lands at the same path regardless of when it was fetched.

Production extension: a dedicated landing zone partitioned by ingest timestamp would sit upstream of the raw zone, capturing every batch before deduplication. Deferred for portfolio scope.

## 4. Single Glue IAM role for crawler and ETL job
A single IAM role (financial-data-lake-glue-crawler-role) is used by both the Glue Crawler and the Glue ETL job.

Trade-off accepted: the role carries broader permissions than the crawler alone requires. The crawler only needs read access to the raw zone; the combined role also holds write access to the curated zone, which the crawler never uses.

Trade-off avoided: managing two separate roles adds deployment complexity without meaningful security benefit at portfolio scale where both resources are owned and operated by the same team.

Production correction: separate roles should be created — one for the crawler (read-only on raw) and one for the ETL job (read on raw, write on curated). This enforces least privilege
precisely and contains the blast radius if either role is compromised. Deferred for portfolio scope.

## 5. Why Lake Formation over IAM-only
Lake Formation is used for column-level access control rather than IAM bucket policies alone.

Trade-off accepted: Lake Formation adds operational complexity — the IAMAllowedPrincipals grant must be revoked and permissionsmanaged in a separate layer from IAM.

Trade-off avoided: IAM bucket policies operate at the S3 object level and cannot restrict access to individual columns within a Parquet file. Lake Formation enforces column-level permissions at
the catalog layer, making it the only AWS-native solution for column-level PII control without application-layer filtering.

This is a compliance requirement for financial data pipelines operating under frameworks such as GDPR and PCI DSS.

Verified in practice: data-lake-analyst running SELECT * on curated_filings returns 8 columns. data-lake-director returns all 10 columns. Column-level governance confirmed end-to-end
via STS assume-role test in Athena (Phase 7).

## 6. Two Glue crawlers — raw and curated zone
A separate crawler is deployed for the curated zone in addition to the raw zone crawler, each scoped to its own zone's schema.

Trade-off accepted: two crawlers to manage and schedule.

Trade-off avoided: without a curated zone crawler, the Glue Data Catalog only reflects the raw zone schema. Lake Formation column-level permissions applied to the raw schema would govern the wrong layer — analysts should query the curated zone where data is cleaned, transformed, and PII is already masked at the column level. The curated crawler is scheduled one hour after
the raw crawler to ensure ETL completion before schema discovery.

The curated crawler is added after Phase 5 ETL completion since the curated zone contains no data until the ETL job runs. In production both crawlers would be defined in the same template
from the outset, with the curated crawler simply finding no data on its first scheduled run.

## 7. Lake Formation governance is not fully captured in Infrastructure as Code
The S3 zones, Glue resources, and IAM roles are fully defined in CloudFormation and are torn down and redeployed cleanly via stack delete/create.

Lake Formation governance is not. The following configuration lives outside any CloudFormation template:

- Registration of the admin IAM user as Lake Formation data lake administrator (account-level, persists across teardown)
- Revocation of the IAMAllowedPrincipals default grant (database-level, does NOT persist — recreating the database via CloudFormation restores the default grant automatically)
- All individual Select/Describe/Alter/Drop grants to the crawler role, admin user, and director/analyst personas (must be recreated even though IAM role ARNs are identical on redeploy)

Production extension: AWS::LakeFormation::Permissions CloudFormation resource type supports managing grants as code after initial bootstrap. A production version of this project
would script all four specific grants using this resource type, leaving only the one-time administrator registration and IAMAllowedPrincipals revocation as manual console steps.
See docs/setup-guide.md Step 8 for the manual sequence.

## 8. CloudFormation drift detection confirmed IN_SYNC
All three CloudFormation stacks were checked with aws cloudformation detect-stack-drift after the full build:

- financial-data-lake-s3: IN_SYNC
- financial-data-lake-glue: IN_SYNC
- financial-data-lake-iam: IN_SYNC

This confirms every fix applied during the build — the IAM policy additions (kms:GenerateDataKey, s3:GetBucketLocation, glue:GetPartitions), the TablePrefix correction, the curated crawler addition, and the explicit column selection in the ETL script — was made by editing and redeploying CloudFormation templates, not by ad-hoc console edits that would silently diverge from the committed source of truth.

Scope limitation: drift detection only covers resources defined in the templates. Lake Formation permissions and the IAMAllowedPrincipals revocation are account-level and database-level settings outside CloudFormation's resource model — they cannot be drift-checked by this command regardless of
whether they match documentation. See ADR #7.

A reusable drift detection script is at: infrastructure/scripts/check-drift.ps1

## 9. Why Parquet format in the curated zone
Parquet is columnar — Athena scans only the columns referenced in a query. A query selecting 3 columns from a 50-column table scans approximately 6% of the data versus 100% with CSV. Combined with date partitioning, this reduces Athena query costs by 80-95% compared to unpartitioned CSV.
Reference: AWS Athena performance tuning documentation.

