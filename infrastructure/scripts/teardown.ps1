# infrastructure/scripts/teardown.ps1
# Destroys all project resources to avoid ongoing charges. Run after each session.

$AccountId = (aws sts get-caller-identity --profile personal --query Account --output text)
$Region = "eu-west-2"

Write-Host "Emptying S3 buckets before stack deletion..."
aws s3 rm "s3://financial-data-lake-raw-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-curated-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-refined-$AccountId" --recursive --profile personal
aws s3 rm "s3://financial-data-lake-athena-results-$AccountId" --recursive --profile personal

Write-Host "Deleting Glue job..."
aws glue delete-job --job-name financial-data-lake-raw-to-curated --profile personal --region $Region

Write-Host "Deleting CloudFormation stacks (reverse dependency order)..."
aws cloudformation delete-stack --stack-name financial-data-lake-iam --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-iam --profile personal --region $Region

aws cloudformation delete-stack --stack-name financial-data-lake-glue --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-glue --profile personal --region $Region

aws cloudformation delete-stack --stack-name financial-data-lake-s3 --profile personal --region $Region
aws cloudformation wait stack-delete-complete --stack-name financial-data-lake-s3 --profile personal --region $Region

Write-Host "Teardown complete. All billable resources destroyed."
Write-Host "Note: Lake Formation permissions and IAMAllowedPrincipals revocation are account-level settings and are not removed by this script. Re-running setup from scratch will require redoing the Step 8 console steps."