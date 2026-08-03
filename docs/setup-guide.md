# Setup Guide — AWS Financial Data Lake

This guide documents the exact steps to deploy this project from scratch in a fresh AWS account.

## Prerequisites

- AWS account with administrator access
- AWS CLI v2 installed and configured (`aws configure --profile personal`)
- Python 3.10+ with `boto3` and `pandas` installed
- Git installed and configured
- Estimated cost: under $15 for a full build-and-teardown cycle (see Cost Breakdown below)

## Deployment Order

Six CloudFormation stacks must be deployed in this order. The Glue stack
references S3 bucket names constructed from the project name, and the VPC
stack creates access points that reference the S3 buckets.

```
financial-data-lake-s3   →  financial-data-lake-glue
                         →  financial-data-lake-iam
                         →  financial-data-lake-macie
                         →  financial-data-lake-backup
                         →  financial-data-lake-vpc
```

---

### 1. Clone and configure

```powershell
git clone https://github.com/YOUR_USERNAME/aws-financial-data-lake.git
cd aws-financial-data-lake
aws sts get-caller-identity --profile personal
```

Note your Account ID from the output — you will need it throughout this guide.
Replace `YOUR_ACCOUNT_ID` in all commands below with this value.

---

### 2. Deploy S3 three-zone stack

```powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-s3 `
  --template-body file://infrastructure/cloudformation/s3-data-lake.yaml `
  --capabilities CAPABILITY_IAM `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-s3 `
  --profile personal --region eu-west-2
```

This creates five S3 buckets: raw, curated, refined, athena-results, and a KMS CMK
shared across all data zones.

---

### 3. Fetch sample data and upload to raw zone

```powershell
python scripts/edgar_fetch.py
python scripts/upload_raw_sample.py
```

This pulls 10-K filing metadata from the SEC EDGAR API and uploads it as partitioned
CSV to the raw zone.

---

### 4. Deploy Glue stack

```powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-glue `
  --template-body file://infrastructure/cloudformation/glue-catalog.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-glue `
  --profile personal --region eu-west-2
```

This creates: Glue catalog database, two IAM roles (crawler role and ETL role),
a dedicated scripts bucket, Glue VPC connection, two ETL jobs (raw-to-curated
and curated-to-refined), and three crawlers (raw, curated, refined).

---

### 5. Upload ETL scripts to the scripts bucket

```powershell
aws s3 cp glue-jobs/raw_to_curated.py `
  s3://financial-data-lake-scripts-YOUR_ACCOUNT_ID/raw_to_curated.py `
  --profile personal --region eu-west-2

aws s3 cp glue-jobs/curated_to_refined.py `
  s3://financial-data-lake-scripts-YOUR_ACCOUNT_ID/curated_to_refined.py `
  --profile personal --region eu-west-2
```

Note: scripts go to the dedicated scripts bucket, not the raw zone bucket. The raw
zone's architectural contract is immutable source data only.

---

### 6. Run the raw zone crawler

```powershell
aws glue start-crawler `
  --name financial-data-lake-raw-crawler `
  --profile personal --region eu-west-2
```

Poll until `State: READY` before proceeding:

```powershell
aws glue get-crawler `
  --name financial-data-lake-raw-crawler `
  --query "Crawler.{State:State,Status:LastCrawl.Status}" `
  --profile personal --region eu-west-2
```

---

### 7. Run the raw-to-curated ETL job

```powershell
aws glue start-job-run `
  --job-name financial-data-lake-raw-to-curated `
  --profile personal --region eu-west-2
```

Poll until `JobRunState: SUCCEEDED` before proceeding:

```powershell
aws glue get-job-runs `
  --job-name financial-data-lake-raw-to-curated `
  --query "JobRuns[0].{State:JobRunState,Error:ErrorMessage}" `
  --profile personal --region eu-west-2
```

---

### 8. Run the curated zone crawler

```powershell
aws glue start-crawler `
  --name financial-data-lake-curated-crawler `
  --profile personal --region eu-west-2
```

Poll until `State: READY` before proceeding.

---

### 9. Run the curated-to-refined ETL job

```powershell
aws glue start-job-run `
  --job-name financial-data-lake-curated-to-refined `
  --profile personal --region eu-west-2
```

Poll until `JobRunState: SUCCEEDED` before proceeding.

---

### 10. Run the refined zone crawler

```powershell
aws glue start-crawler `
  --name financial-data-lake-refined-crawler `
  --profile personal --region eu-west-2
```

Poll until `State: READY`. This registers two tables: `refined_filings_by_company_year`
and `refined_filings_by_year` in the Glue catalog.

---

### 11. Deploy IAM stack (director and analyst personas)

```powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-iam `
  --template-body file://infrastructure/cloudformation/iam-roles.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-iam `
  --profile personal --region eu-west-2
```

---

### 12. One-time Lake Formation console setup

This step cannot be scripted — it is a known AWS platform limitation on fresh accounts.
See ADR #7 in `docs/architecture-decisions.md` for full context.

1. Lake Formation console → register your IAM user as data lake administrator
2. Edit `financial_data_lake` database → uncheck "Use only IAM access control for new tables"
3. Revoke `IAMAllowedPrincipals` at both database level and table level for `filings` and `curated_filings`
4. Grant `financial-data-lake-glue-crawler-role`: Describe + Create table (database level), Alter + Describe + Drop (table level)
5. Grant `financial-data-lake-glue-etl-role`: Describe (database level), Describe + Select (table level) on both `filings` and `curated_filings`
6. Grant your own admin user: Select, Drop, Alter, Describe on `curated_filings`
7. Grant `data-lake-director`: Select, all columns, on `curated_filings`
8. Grant `data-lake-analyst`: Select, included columns only (`company`, `form_type`, `filing_date`, `filing_year`, `primary_document`, `company_standardised`) — excludes `cik_masked` and `accession_number`

Note: step 5 (GlueETLRole grants) was added during the VPC extension phase when the
single crawler/ETL role was split into two separate roles. A new principal touching
Lake Formation governed tables for the first time always requires explicit grants
regardless of IAM permissions — see Phase 10 in `docs/data-notes.md`.

See `docs/data-notes.md` for the full reasoning behind each grant — several of these
were discovered through debugging and are not obvious from AWS documentation alone.

---

### 13. Verify Athena governance

```powershell
# As director — should return 10 columns
aws athena start-query-execution `
  --query-string "SELECT * FROM curated_filings LIMIT 5" `
  --query-execution-context Database=financial_data_lake `
  --result-configuration "OutputLocation=s3://financial-data-lake-athena-results-YOUR_ACCOUNT_ID/" `
  --profile personal --region eu-west-2

# As analyst — should return 8 columns (cik_masked and accession_number absent)
aws sts assume-role `
  --role-arn arn:aws:iam::YOUR_ACCOUNT_ID:role/data-lake-analyst `
  --role-session-name analyst-test `
  --profile personal --region eu-west-2
```

Also verify the refined zone is queryable:

```powershell
aws athena start-query-execution `
  --query-string "SELECT * FROM refined_filings_by_year ORDER BY filing_year" `
  --query-execution-context Database=financial_data_lake `
  --result-configuration "OutputLocation=s3://financial-data-lake-athena-results-YOUR_ACCOUNT_ID/" `
  --profile personal --region eu-west-2
```

---

### 14. Deploy Macie stack and create classification job

```powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-macie `
  --template-body file://infrastructure/cloudformation/financial-data-lake-macie.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameters ParameterKey=CuratedBucketName,ParameterValue=financial-data-lake-curated-YOUR_ACCOUNT_ID `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-macie `
  --profile personal --region eu-west-2
```

Get the custom data identifier ID from the stack output:

```powershell
$CustomId = aws cloudformation describe-stacks `
  --stack-name financial-data-lake-macie `
  --query "Stacks[0].Outputs[?OutputKey=='CustomDataIdentifierId'].OutputValue" `
  --output text --profile personal --region eu-west-2
```

Create the classification job (no CloudFormation resource type exists for this — see ADR #10):

```powershell
aws macie2 create-classification-job `
  --job-type ONE_TIME `
  --name raw-zone-pii-scan `
  --custom-data-identifier-ids $CustomId `
  --s3-job-definition "{\"bucketDefinitions\":[{\"accountId\":\"YOUR_ACCOUNT_ID\",\"buckets\":[\"financial-data-lake-raw-YOUR_ACCOUNT_ID\"]}]}" `
  --profile personal --region eu-west-2
```

---

### 15. Verify S3 Backup opt-in and deploy Backup stack

```powershell
aws backup describe-region-settings `
  --query "ResourceTypeOptInPreference.S3" `
  --profile personal --region eu-west-2
```

If this returns `false`, run:

```powershell
aws backup update-region-settings `
  --resource-type-opt-in-preference S3=true `
  --profile personal --region eu-west-2
```

Deploy the backup stack:

```powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-backup `
  --template-body file://infrastructure/cloudformation/financial-data-lake-backup.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameters `
    ParameterKey=RawBucketArn,ParameterValue=arn:aws:s3:::financial-data-lake-raw-YOUR_ACCOUNT_ID `
    ParameterKey=CuratedBucketArn,ParameterValue=arn:aws:s3:::financial-data-lake-curated-YOUR_ACCOUNT_ID `
    ParameterKey=RefinedBucketArn,ParameterValue=arn:aws:s3:::financial-data-lake-refined-YOUR_ACCOUNT_ID `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-backup `
  --profile personal --region eu-west-2
```

Note: pass fully-qualified ARNs with `arn:aws:s3:::` prefix. Passing bucket names without
the prefix causes a 400 error from the Backup API at update time, not at stack creation
time — see Phase 9 in `docs/data-notes.md`.

---

### 16. Deploy VPC stack

```powershell
aws cloudformation create-stack `
  --stack-name financial-data-lake-vpc `
  --template-body file://infrastructure/cloudformation/financial-data-lake-vpc.yaml `
  --parameters `
    ParameterKey=ProjectName,ParameterValue=financial-data-lake `
    ParameterKey=RawBucketName,ParameterValue=financial-data-lake-raw-YOUR_ACCOUNT_ID `
    ParameterKey=CuratedBucketName,ParameterValue=financial-data-lake-curated-YOUR_ACCOUNT_ID `
    ParameterKey=RefinedBucketName,ParameterValue=financial-data-lake-refined-YOUR_ACCOUNT_ID `
  --profile personal --region eu-west-2

aws cloudformation wait stack-create-complete `
  --stack-name financial-data-lake-vpc `
  --profile personal --region eu-west-2
```

This deploys the VPC with a private subnet, S3 gateway endpoint, Glue security group,
and three VPC-restricted S3 access points.

The S3 stack (deployed in Step 2) already contains the VPC enforcement bucket policies
targeting the gateway endpoint created in this step. Both stacks must be deployed for
the enforcement to be active. The VPC stack exports the endpoint ID
(`financial-data-lake-S3EndpointId`) which the S3 stack imports via cross-stack reference.

---

### 17. Final verification

**Confirm VPC enforcement is active:**

```powershell
aws s3 ls s3://financial-data-lake-raw-YOUR_ACCOUNT_ID/ `
  --profile personal --region eu-west-2
```

Note: `dev-arch-project` is in the bucket policy exemption list, so this command
succeeds for that identity. To confirm the enforcement blocks non-exempted access,
run the same command using a different IAM role that is not in the exemption list —
it should return AccessDenied.

**Confirm ETL job routes through VPC:**

```powershell
aws glue start-job-run `
  --job-name financial-data-lake-raw-to-curated `
  --profile personal --region eu-west-2
```

A successful run after VPC enforcement was applied is the definitive proof that
Glue workers are routing through the gateway endpoint.

**Confirm Athena still queries the refined zone:**

```powershell
aws athena start-query-execution `
  --query-string "SELECT * FROM refined_filings_by_year ORDER BY filing_year" `
  --query-execution-context Database=financial_data_lake `
  --result-configuration "OutputLocation=s3://financial-data-lake-athena-results-YOUR_ACCOUNT_ID/" `
  --profile personal --region eu-west-2
```

The refined zone has no VPC enforcement — Athena queries it directly.

---

## Cost Breakdown (Estimated)

| Resource | Cost driver | Estimated cost |
|---|---|---|
| S3 storage | ~50 rows of CSV/Parquet across 5 buckets, negligible size | < $0.01 |
| KMS keys | Two CMKs (data lake key + backup vault key) at $1/month each | $2/month if not torn down |
| Glue crawlers | Per-run, billed per DPU-hour, ~1 min runs | < $0.10 total |
| Glue ETL jobs | G.1X x 2 workers, ~3 min per run | ~$0.05 per run |
| Athena queries | Per TB scanned, dataset is tiny | < $0.01 total |
| Macie | Automated discovery free tier covers small accounts | < $0.10 |
| AWS Backup | S3 recovery points use versioning — no vault storage cost | < $0.01 |
| VPC | Gateway endpoint is free, no NAT gateway, no Elastic IP | $0 |
| **Total for a full build-test-teardown cycle** | | **Under $5** |

The KMS monthly charge ($2/month for two CMKs) is the only meaningful ongoing cost.
Run the teardown script after each working session to avoid it accumulating.

---

## Teardown

See `infrastructure/scripts/teardown.ps1` for full cleanup. Run after every session.

Important notes on teardown:

**Lake Formation** — Lake Formation permissions and the IAMAllowedPrincipals revocation
are account-level settings not managed by CloudFormation. They are not removed by the
teardown script. Re-running setup from scratch requires redoing Step 12 console steps.
However, since IAM role ARNs are deterministic (role names are fixed in the templates),
Lake Formation grants may persist correctly across a teardown and redeploy if the role
names have not changed.

**VPC enforcement** — the raw and curated bucket policies contain a Deny referencing
the VPC endpoint ID. The teardown script deletes the bucket policies explicitly before
emptying the buckets, because `aws s3 rm` from outside the VPC would otherwise be blocked.
Since `dev-arch-project` is in the ArnNotLike exemption list, it can delete the policies
directly without needing to be inside the VPC.

**Macie classification job** — the classification job is not managed by CloudFormation
and must be cancelled or left to complete before the Macie stack is deleted. The teardown
script lists any active jobs for manual review.