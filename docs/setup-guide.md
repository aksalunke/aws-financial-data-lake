# Setup Guide — AWS Financial Data Lake
This guide documents the exact steps to deploy this project from scratch in a fresh AWS account.

## Prerequisites
- AWS account with administrator access
- AWS CLI v2 installed and configured (`aws configure --profile personal`)
- Python 3.10+ with `boto3` and `pandas` installed
- Git installed and configured
- Estimated cost: under $10 for a full build-and-teardown cycle (see Cost Breakdown below)

## Deployment Order
Stacks must be deployed in this order due to cross-stack dependencies (IAM roles reference resources created by the Glue stack).

### 1. Clone and configure
\`\`\`powershell
git clone https://github.com/YOUR_USERNAME/aws-financial-data-lake.git
cd aws-financial-data-lake
aws sts get-caller-identity --profile personal
\`\`\`

### 2. Deploy S3 three-zone stack
\`\`\`powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-s3 `
  --template-body file://infrastructure/cloudformation/s3-data-lake.yaml `
  --capabilities CAPABILITY_IAM `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-s3 `
  --profile personal --region eu-west-2
\`\`\`

### 3. Fetch sample data and upload to raw zone
\`\`\`powershell
python scripts/edgar_fetch.py
python scripts/upload_raw_sample.py
\`\`\`

### 4. Deploy Glue Data Catalog and Crawlers
\`\`\`powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-glue `
  --template-body file://infrastructure/cloudformation/glue-catalog.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-glue `
  --profile personal --region eu-west-2

aws glue start-crawler --name financial-data-lake-raw-crawler --profile personal --region eu-west-2
\`\`\`

Wait for `READY` state before proceeding (poll with `aws glue get-crawler`).

### 5. Upload and run the ETL job
\`\`\`powershell
aws s3 cp glue-jobs/raw_to_curated.py s3://financial-data-lake-raw-YOUR_ACCOUNT_ID/scripts/raw_to_curated.py --profile personal --region eu-west-2

aws glue create-job `
  --name financial-data-lake-raw-to-curated `
  --role financial-data-lake-glue-crawler-role `
  --command '{"Name":"glueetl","ScriptLocation":"s3://financial-data-lake-raw-YOUR_ACCOUNT_ID/scripts/raw_to_curated.py","PythonVersion":"3"}' `
  --default-arguments '{"--raw_bucket":"financial-data-lake-raw-YOUR_ACCOUNT_ID","--curated_bucket":"financial-data-lake-curated-YOUR_ACCOUNT_ID","--job-bookmark-option":"job-bookmark-enable"}' `
  --glue-version "4.0" --number-of-workers 2 --worker-type G.1X `
  --profile personal --region eu-west-2

aws glue start-job-run --job-name financial-data-lake-raw-to-curated --profile personal --region eu-west-2
\`\`\`

### 6. Run the curated crawler
\`\`\`powershell
aws glue start-crawler --name financial-data-lake-curated-crawler --profile personal --region eu-west-2
\`\`\`

### 7. Deploy IAM roles for governance
\`\`\`powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-iam `
  --template-body file://infrastructure/cloudformation/iam-roles.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --profile personal --region eu-west-2
\`\`\`

### 8. One-time Lake Formation console setup
This step cannot be scripted — it is a known AWS limitation on fresh accounts:

1. Lake Formation console → register your IAM user as data lake administrator
2. Edit `financial_data_lake` database → uncheck "Use only IAM access control for new tables"
3. Revoke `IAMAllowedPrincipals` at both database and table level for `financial_data_lake` / `curated_filings`
4. Grant `financial-data-lake-glue-crawler-role`: Describe + Create table (database level), Alter + Describe + Drop (table level)
5. Grant your own admin user: Select, Drop, Alter, Describe on `curated_filings`
6. Grant `data-lake-director`: Select, all columns, on `curated_filings`
7. Grant `data-lake-analyst`: Select, included columns only (`company`, `form_type`, `filing_date`, `filing_year`, `primary_document`, `company_standardised`) — excludes `cik_masked` and `accession_number`

See `docs/data-notes.md` for the full reasoning behind each grant — several of these were discovered through trial and error and are not obvious from AWS documentation alone.

### 9. Verify
\`\`\`powershell
aws athena start-query-execution `
  --query-string "SELECT * FROM financial_data_lake.curated_filings LIMIT 10" `
  --result-configuration "OutputLocation=s3://financial-data-lake-athena-results-YOUR_ACCOUNT_ID/" `
  --profile personal --region eu-west-2
\`\`\`

## Cost Breakdown (Estimated)
| Resource | Cost driver | Estimated cost |
|---|---|---|
| S3 storage | ~50 rows of CSV/Parquet, negligible size | < $0.01 |
| Glue Crawlers | Per-run, billed per DPU-hour, ~1 min runs | < $0.10 total |
| Glue ETL Job | G.1X × 2 workers, ~3 min run | ~$0.05 per run |
| Athena queries | Per TB scanned, dataset is tiny | < $0.01 total |
|| **Total for a full build-test-teardown cycle** | | **Under $1** |

Run the teardown script (below) after each working session to avoid the KMS monthly charge accumulating.

## Teardown
See `infrastructure/scripts/teardown.ps1` for full cleanup. Run after every session.