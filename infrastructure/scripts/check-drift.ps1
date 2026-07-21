# infrastructure/scripts/check-drift.ps1
# Runs CloudFormation drift detection across all three project stacks
# and reports status. Run periodically to confirm no manual console
# changes have silently diverged from the committed templates.

$Region = "eu-west-2"
$Stacks = @(
    "financial-data-lake-s3",
    "financial-data-lake-glue",
    "financial-data-lake-iam"
)

foreach ($Stack in $Stacks) {
    Write-Host "`nChecking drift for $Stack..."

    $DetectionId = aws cloudformation detect-stack-drift `
        --stack-name $Stack `
        --profile personal `
        --region $Region `
        --query 'StackDriftDetectionId' `
        --output text

    Start-Sleep -Seconds 10

    $Result = aws cloudformation describe-stack-drift-detection-status `
        --stack-drift-detection-id $DetectionId `
        --profile personal `
        --region $Region `
        --query '{Status:DetectionStatus,DriftStatus:StackDriftStatus}' `
        --output json

    Write-Host "$Stack -> $Result"
}

Write-Host "`nDrift check complete. Note: Lake Formation permissions and"
Write-Host "the IAMAllowedPrincipals revocation are account-level settings"
Write-Host "outside CloudFormation's resource model and will never appear"
Write-Host "as drift, even if changed. See ADR #7 for details."