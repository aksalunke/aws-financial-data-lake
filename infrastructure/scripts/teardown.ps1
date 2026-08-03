# infrastructure/scripts/teardown.ps1
# Destroys all project resources to avoid ongoing charges. Run after each session.
#
# IMPORTANT: run this script as the dev-arch-project IAM user (--profile personal).
# That identity is in the bucket policy exemption list and can access S3 buckets
# directly even with VPC enforcement active.
#
# Lake Formation permissions and IAMAllowedPrincipals revocation are account-level
# settings not managed by CloudFormation — they are NOT removed by this script.
# Re-running setup from scratch requires redoing the Step 12 console steps in
# docs/setup-guide.md.

$AccountId = (aws sts get-caller-identity --profile personal --query Account --output text)
$Region = "eu-west-2"

Write-Host "Step 1 — Deleting Macie classification jobs (not CloudFormation-managed)..."
$Jobs = (aws macie2 list-classification-jobs `
  --filter-criteria '{"includes":[{"comparator":"NE","key":"jobStatus","values":["CANCELLED"]}]}' `
  --query "items[].jobId" --output text --profile personal --region $Region)
if ($Jobs) {
    Write-Host "Active Macie jobs found. Cancel them manually in the Macie console before continuing."
    Write-Host "Job IDs: $Jobs"
    Read-Host "Press Enter once all jobs are cancelled or completed"
} else {
    Write-Host "No active Macie jobs found."
}

Write-Host "Step 2 — Removing VPC enforcement bucket policies before emptying buckets..."
# dev-arch-project is in the ArnNotLike exemption list so can delete these directly.
# Without this step, aws s3 rm would be blocked by the aws:SourceVpce Deny from outside the VPC.
aws s3api delete-bucket-policy `
  --bucket "financial-data-lake-raw-$AccountId" `
  --profile personal
aws s3api delete-bucket-policy `
  --bucket "financial-data-lake-curated-$AccountId" `
  --profile personal

Write-Host "Step 3 — Emptying S3 buckets before stack deletion..."
aws s3 rm "s3://financial-data-lake-raw-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-curated-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-refined-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-athena-results-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-scripts-$AccountId" --recursive --profile personal

Write-Host "Step 4 — Deleting CloudFormation stacks (reverse dependency order)..."

# VPC stack first — deletes access points and gateway endpoint
aws cloudformation delete-stack --stack-name financial-data-lake-vpc --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-vpc --profile personal --region $Region
Write-Host "financial-data-lake-vpc deleted."

# Backup stack — deletes vault, CMK, backup plan, backup role
aws cloudformation delete-stack --stack-name financial-data-lake-backup --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-backup --profile personal --region $Region
Write-Host "financial-data-lake-backup deleted."

# Macie stack — deletes Macie session, custom identifier, findings filter
aws cloudformation delete-stack --stack-name financial-data-lake-macie --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-macie --profile personal --region $Region
Write-Host "financial-data-lake-macie deleted."

# IAM stack — deletes director and analyst personas
aws cloudformation delete-stack --stack-name financial-data-lake-iam --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-iam --profile personal --region $Region
Write-Host "financial-data-lake-iam deleted."

# Glue stack — deletes both ETL jobs, all crawlers, IAM roles, scripts bucket, VPC connection
# Scripts bucket was emptied in Step 3 — CloudFormation can now delete it
aws cloudformation delete-stack --stack-name financial-data-lake-glue --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-glue --profile personal --region $Region
Write-Host "financial-data-lake-glue deleted."

# S3 stack last — deletes all five S3 buckets and the data lake KMS CMK
# Bucket policies (RawBucketPolicy, CuratedBucketPolicy) are CloudFormation resources
# in this stack and are deleted automatically before the buckets.
# Buckets were emptied in Step 3 so CloudFormation can delete them.
aws cloudformation delete-stack --stack-name financial-data-lake-s3 --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-s3 --profile personal --region $Region
Write-Host "financial-data-lake-s3 deleted."

Write-Host ""
Write-Host "Teardown complete. All six stacks deleted."
Write-Host ""
Write-Host "Remaining account-level state (not removed by this script):"
Write-Host "  - Lake Formation administrator registration"
Write-Host "  - IAMAllowedPrincipals revocation state"
Write-Host "  - Lake Formation table and column grants"
Write-Host "  - Macie automated discovery configuration"
Write-Host ""
Write-Host "Re-running setup from scratch requires redoing Step 12 in docs/setup-guide.md."