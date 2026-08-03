# Data Notes

## Phase 5 — Raw to Curated ETL
Raw zone: 12 partitions keyed by ingest_year/ingest_month. 
Curated zone: 12 partitions keyed by year/month.

Partition counts tally exactly, confirming the ETL correctly read each row's filing_date value and wrote it to the corresponding curated partition.

## Phase 6 — Curated zone schema missing from catalog
While applying Lake Formation column-level permissions, the expected column company_standardised was not available in the Lake Formation console's column selector for the filings table.

Root cause: the Glue Crawler from Phase 4 only crawled the raw zone. The curated zone, where the ETL job writes company_standardised and cik_masked, had never been crawled — so those columns did not exist in the Glue Data Catalog at all.

Resolution: added a second crawler (CuratedZoneCrawler) targeting the curated zone, scheduled one hour after the raw crawler. See ADR #6.

## Phase 6 — Crawler blocked by Lake Formation after IAMAllowedPrincipals revoke
After revoking IAMAllowedPrincipals on financial_data_lake, the curated zone crawler failed with AccessDeniedException on Describe permission.

Root cause: revoking IAMAllowedPrincipals removed the IAM-only fallback for ALL principals, including the Glue crawler's own IAM role. Lake Formation and IAM are additive — once
IAMAllowedPrincipals is revoked, every principal interacting with the catalog needs an explicit Lake Formation grant, regardless of its IAM permissions.

Note: the raw zone crawler succeeded prior to this phase because it ran before IAMAllowedPrincipals was revoked. Once revoked, any crawler operation against financial_data_lake — including a future
scheduled run of the raw crawler — requires the same Lake Formation grant. The fix applied to the crawler role resolves this for both crawlers, since they share one role.

Resolution: granted financial-data-lake-glue-crawler-role explicit Lake Formation permissions (Describe/Create table at database level; Alter/Describe/Drop at table level) before re-running.

## Phase 6 — Curated table registered with auto-generated hash suffix
The curated zone crawler created a table named filings_<hash> instead of a readable name, because Glue detected a naming collision with the existing raw zone filings table and
auto-resolved it by appending a hash suffix.

Resolution: added TablePrefix: 'curated_' to CuratedZoneCrawler in glue-catalog.yaml. Deleted the auto-generated table and re-ran the crawler. Curated table now registers predictably
as curated_filings.

## Phase 6 — IAMAllowedPrincipals revoke required grants for every principal
Revoking IAMAllowedPrincipals on financial_data_lake affected three distinct principals, each requiring its own explicit Lake Formation grant before operations succeeded:

1. financial-data-lake-glue-crawler-role — needed grants at both database and table level before either crawler could run.
2. The admin IAM user (personal profile) — needed Drop/Alter/Describe at table level before CLI     delete-table operations succeeded, despite being registered as Lake Formation data lake
administrator. Administrator status grants the authority to manage permissions, not a bypass of table-level permission checks for destructive actions.
3. data-lake-director and data-lake-analyst roles — required explicit Select grants on curated_filings before Athena queries would work in Phase 7.

Lesson: IAMAllowedPrincipals is a single global bypass, not a per-persona setting. Removing it strips IAM-fallback access from every principal already interacting with the catalog — not just
the new personas being introduced. This is the real operational cost of enabling fine-grained governance, more so than the column masking configuration itself.

## Phase 6 — Crawler role permission residue
financial-data-lake-glue-crawler-role accumulated four Lake Formation grants across this phase: two automatic (full control on each table it created — filings, curated_filings) and two
manual (ALL_TABLES wildcard, unused Select on curated_filings) added during troubleshooting. Left in place for portfolio scope; production hardening would narrow these to Describe-only once
tables already exist.

## Phase 6 — Stale analyst grant on raw table
An initial Lake Formation grant was applied giving data-lake-analyst Select access to the raw filings table, before the missing company_standardised column was traced to the curated zone never
having been crawled. This grant was revoked once permissions were correctly applied to curated_filings instead — leaving it in place would have let the analyst role query the unmasked raw cik column directly, bypassing the governance this phase was built to enforce.

## Phase 7 — Admin user query failed: no Select grant on curated_filings
Running SELECT * as the admin IAM user failed with COLUMN_NOT_FOUND: Relation contains no accessible columns.

Root cause: earlier grants to the admin user (Drop, Alter, Describe) were scoped specifically to fix the table-deletion error in Phase 6. Select was never separately granted. Lake Formation evaluates column access per-permission — having Drop/Alter/Describe does not imply Select. With zero columns authorised, Athena reports the relation as having no accessible columns rather than denying the query outright.

Resolution: granted admin user explicit Select on curated_filings, all columns. Confirms each Lake Formation permission type is independent — no permission implies another, including for an
account's own administrator.

## Phase 7 — Full permission set required to query a governed, partitioned table
Getting the analyst role to successfully run SELECT * against curated_filings required four separate fixes, each surfaced by a distinct, specific error. IAM and Lake Formation are additive
systems — a role needs grants in both before any operation succeeds, and each AWS API action used under the hood needs its own explicit permission:

1. kms:GenerateDataKey — required to write encrypted query results to the Athena results bucket. kms:Decrypt alone only covers reading existing encrypted objects.
2. s3:GetBucketLocation — required for Athena to verify the output bucket exists, separate from GetObject/PutObject/ListBucket which only cover object-level access.
3. glue:GetPartitions — required to resolve partition metadata for a partitioned table. glue:GetTable only returns schema, not partition listings.
4. Lake Formation Select grant on curated_filings — required independently of all IAM permissions above; IAM grants access to call the AWS APIs, Lake Formation grants access to the data itself.

Final minimum IAM policy for both AnalystRole and DirectorRole:
athena:StartQueryExecution, athena:GetQueryResults, athena:GetQueryExecution, glue:GetTable, glue:GetDatabase, glue:GetPartitions, s3:GetObject, s3:ListBucket, s3:GetBucketLocation, s3:PutObject, kms:Decrypt, kms:GenerateDataKey — plus a Lake Formation Select grant scoped
to the specific table and columns the role should see.

Result confirmed: 
data-lake-analyst SELECT * on curated_filings returns 8 columns (cik_masked and accession_number absent). 
Admin user with full Select grant returns all 10 columns.
Column-level governance verified end-to-end.

## Phase 7 — Director role test: no additional fixes required
data-lake-director succeeded on the first query attempt with no permission errors. DirectorRole's original IAM policy already included kms:GenerateDataKey from the initial Phase 6 template,
unlike AnalystRole which was missing it.

This highlights the practical cost of least-privilege design:
the more narrowly scoped a role is, the more likely it is to surface missing permissions that a broader role never exposes. DirectorRole's broad access masked four permission gaps that
only became visible once AnalystRole's restricted policy was tested against the same operations.

Result confirmed: 
data-lake-director SELECT * on curated_filings returns all 10 columns, including cik_masked and accession_number.

## Phase 7 — Unexpected ingest_year/ingest_month columns in curated_filings
curated_filings contained four partition-like columns instead of two: ingest_year, ingest_month (expected only in raw filings) and year, month (expected, computed by the ETL).

Root cause: the ETL reads from the Glue Data Catalog via create_dynamic_frame.from_catalog(), which surfaces a table's partition columns as ordinary DataFrame columns. Since the raw filings table is partitioned by ingest_year/ingest_month, those columns passed through into cleaned_df unchanged and were written into curated Parquet output alongside the newly computed year/month.

Resolution: added explicit final_df.select() with a named 8-column list in raw_to_curated.py, replacing the implicit column carry-through from cleaned_df. Re-ran the Glue job and
curated crawler. Verified curated_filings now contains exactly the intended schema, and Lake Formation governance continues to function correctly post-fix.

## Phase 8 — Macie classification job returned zero objects
Classification job `raw-zone-pii-scan` completed with `approximateNumberOfObjectsToProcess: 0.0`
despite objects confirmed present in the raw bucket via `aws s3 ls --recursive`.

Root cause: the Macie service-linked role `AWSServiceRoleForAmazonMacie` was absent from the
raw bucket KMS key policy. Macie could not decrypt the objects to read them, so they were
counted as zero processable objects rather than surfacing an explicit access error.

Resolution: added a second statement to the KMS key policy in `financial-data-lake-s3.yaml`
granting `kms:Decrypt` and `kms:DescribeKey` to the Macie service-linked role ARN. Redeployed
the S3 stack. Created a second classification job `raw-zone-pii-scan-v2` — Macie does not
permit duplicate job names, so the original job could not be rerun.

Same pattern as the Glue crawler IAM policy split in Phase 5 — a least-privilege gap that only
surfaced when a new service accessed an existing encrypted resource for the first time.

## Phase 8 — Console action stripped FindingCriteria from FindingsFilter
Clicking "Suppress findings" in the Macie console modified the `CuratedBucketFindingsFilter`
resource directly in AWS outside of CloudFormation, stripping `FindingCriteria` entirely.
The filter retained its name and `ARCHIVE` action but lost its targeting rules, making it
non-functional. CloudFormation drift detection reported `DRIFTED` on the Macie stack.

Root cause: the Macie console "Suppress findings" button edits existing filter resources
in place without any CloudFormation awareness.

Resolution: a trivial template edit (description update) was required to force a changeset,
since `cloudformation deploy` compares against its own last-deployed state rather than the
actual AWS resource state. If the template is unchanged, no changeset is created even when
the real resource has drifted. Redeployment restored `FindingCriteria` as confirmed by
`aws macie2 get-findings-filter`.

Lesson: console actions on CloudFormation-managed resources cause silent drift. The discipline
of making all changes at the template layer — not the console — is the only reliable way to
keep actual resource state aligned with declared state.

## Phase 8 — CloudFormation drift detection false positive on FindingsFilter
After redeployment restored `FindingCriteria`, drift detection continued to report `DRIFTED`
on the Macie stack. Re-ran `detect-stack-drift` explicitly — new timestamp confirmed a fresh
check, not a cached result.

Root cause: confirmed false positive — `aws macie2 get-findings-filter` shows the resource
matches the template exactly, including bucket name and finding type criteria. CloudFormation
drift detection does not correctly reconcile this resource type after a changeset update.

Documented as a CloudFormation/Macie drift detection limitation. All three original stacks
confirmed IN_SYNC. Macie stack infrastructure is correct; drift status is not actionable.

## Phase 8 — FindingsFilter verification: empty Archived view
Archived findings view empty at time of verification. The filter was confirmed present in
stack outputs with the correct `FindingCriteria` restored.

The empty Archived view is consistent with a correctly configured private encrypted bucket —
the curated bucket was never public, so the filter's targeted finding type
`Policy:IAMUser/S3BucketPublicAccessDisabled` will never be generated for it.

Note: the filter criterion was conservatively chosen. The more production-relevant suppression
would target `SensitiveData:S3Object/CustomIdentifier` on the curated bucket, where hashed
CIK values detected by automated sensitive data discovery would be intentional rather than
actionable. Documented as a known limitation; the filter demonstrates the suppression pattern
correctly even if the specific finding type is not the most realistic choice for this bucket.


## Phase 9 — S3 stack updated as backup prerequisite
Two properties added to all three zone bucket resources in financial-data-lake-s3.yaml before deploying the backup stack:

VersioningConfiguration.Status: Enabled — AWS Backup requires S3 versioning to create recovery points. A missing or suspended versioning state causes the backup job to fail with an explicit error rather than succeeding silently.
NotificationConfiguration.EventBridgeConfiguration.EventBridgeEnabled: true — AWS Backup creates EventBridge rules (prefixed AwsBackupManagedRule*) to track object changes for continuous backup. The S3 bucket must have EventBridge notifications enabled to send events to those rules.

Both are non-destructive updates — CloudFormation modifies the bucket in place, no recreation. S3 stack redeployed and confirmed IN_SYNC on drift detection.

Note: the Athena results bucket was also missing versioning and was updated at the same time. That bucket is not included in the backup selection — Athena query outputs are ephemeral — but versioning on it causes no harm.

## Phase 9 — Backup stack deployed with placeholder ARNs
The financial-data-lake-backup stack reached CREATE_COMPLETE on the first deploy because the RawBucketArn, CuratedBucketArn, and RefinedBucketArn parameters were passed the literal placeholder strings from the template comments rather than actual bucket ARNs. CloudFormation accepted the deploy — the parameter values are strings and no validation exists at the template level to require ARN format.

The BackupSelection resource was therefore created pointing at buckets that do not exist.

Resolution: reran cloudformation deploy with the correct ARNs. The update rolled back with UPDATE_ROLLBACK_COMPLETE — the bucket names were passed without the arn:aws:s3::: prefix, producing a 400 from the Backup API: "AWS partition and service vendor code must be specified." A second update with fully-qualified ARNs (arn:aws:s3:::financial-data-lake-raw-ACCOUNTID) succeeded. Stack confirmed UPDATE_COMPLETE.

Lesson: CloudFormation parameter validation does not enforce ARN format unless a constraint pattern is added explicitly. A AllowedPattern constraint on each ARN parameter would have surfaced the malformed value at changeset creation rather than at resource update time.

## Phase 9 — BackupSizeInBytes: 0 on completed recovery points
All three manual backup jobs completed with Status: COMPLETED but BackupSizeInBytes: 0 on the resulting recovery points in financial-data-lake-vault.

Root cause: not an error. AWS Backup for S3 uses versioning-based recovery points rather than copying bytes into the vault. A recovery point is a catalogue entry recording the versioned state of the bucket at that moment — no data is physically written into the vault, so vault storage consumed is zero. This is S3-specific behaviour; EBS and RDS backups do write physical snapshot data into the vault and would show non-zero sizes.

Confirming evidence: describe-backup-job for each job showed BytesTransferred values above zero (11,617 bytes for the raw zone), confirming the bucket data was actually read by the backup service. BytesTransferred measures data read from the source; BackupSizeInBytes measures incremental vault storage consumed. Both values are correct and consistent.

Status: COMPLETED is the authoritative success indicator for S3 backup jobs, not the size field.

## Phase 9 — Drift detection: all five stacks
detect-stack-drift run across all five stacks after the full backup build:

financial-data-lake-s3: IN_SYNC
financial-data-lake-glue: IN_SYNC
financial-data-lake-iam: IN_SYNC
financial-data-lake-macie: DRIFTED (pre-existing false positive — see Phase 8)
financial-data-lake-backup: IN_SYNC

Four stacks IN_SYNC confirms the S3 versioning and EventBridge additions, the full backup stack deployment, and all intermediate fixes were made at the template layer rather than via console. Macie drift status is unchanged from Phase 8 and remains a documented platform limitation, not an actionable gap.

## Phase 10 — Security group description rejected: invalid character set
The financial-data-lake-vpc stack failed on the first deploy with: "Invalid security group description. Valid descriptions are strings less than 256 characters from the following set: a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*"

Root cause: the SecurityGroupEgress Description field contained an em dash character (—), which is outside the AWS-accepted character set for security group descriptions. The same restriction applies to GroupDescription and AWS::EC2::SecurityGroupIngress description fields. Note: CloudFormation stack-level Description fields are not subject to this restriction — em dashes in the Description property cause no error but render as ??? in the CloudFormation console, which is cosmetic only.

Resolution: replaced the em dash with a plain hyphen in all security group description fields.

## Phase 10 — Access points orphaned after ROLLBACK_COMPLETE
The first deploy of financial-data-lake-vpc failed on the security group resource. CloudFormation rolled back to ROLLBACK_COMPLETE. Three S3 access points (raw-ap, curated-ap, refined-ap) were created before the failure and were not deleted during rollback — they survived as orphaned resources outside CloudFormation's control.

Subsequent deploy attempts failed with: "The following hook(s)/validation failed: [AWS::EarlyValidation::ResourceExistenceCheck]" CloudFormation's early validation detected that the access point names were already taken and blocked the deploy before any resources were created.

Resolution: manually delete all three access points via aws s3control delete-access-point, confirm empty AccessPointList, then redeploy. Also required deleting the stuck stack (REVIEW_IN_PROGRESS state) and deploying fresh.

Lesson: resources created before a CloudFormation rollback may survive the rollback. Any resource with a globally or regionally unique name (S3 access points, S3 buckets, IAM roles) must be manually cleaned up before redeploying with the same names.

## Phase 10 — Lake Formation grants required for GlueETLRole
The first run of the raw-to-curated job after the Glue role split failed with: "AccessDeniedException: Insufficient Lake Formation permission(s): Required Describe on filings"

Root cause: GlueETLRole is a new IAM principal created during the VPC extension. Lake Formation has no grants for a new principal regardless of its IAM permissions — IAM and Lake Formation are evaluated independently. The previous GlueCrawlerRole had accumulated Lake Formation grants over Phase 6 and 7, but those grants do not transfer to the new role.

Resolution: three grants required via aws lakeformation grant-permissions:

DESCRIBE on database financial_data_lake
DESCRIBE + SELECT on table filings (raw)
DESCRIBE + SELECT on table curated_filings (curated — needed by curated-to-refined job)

Same root cause as Phase 7 — a new principal touching Lake Formation governed tables for the first time always requires explicit grants regardless of IAM policy. Third occurrence of this pattern in the project. See also Phase 6 (crawler role after IAMAllowedPrincipals revoke) and Phase 8 (Macie SLR absent from KMS key policy).

## Phase 10 — Bucket policy lockout: deployment identity blocked
After applying aws:SourceVpce enforcement to the raw and curated bucket policies via cloudformation deploy, the next stack update (adding the backup customer role exemption) failed with: "dev-arch-project is not authorized to perform: s3:PutBucketPolicy on resource: financial-data-lake-raw with an explicit deny in a resource-based policy"

Root cause: the bucket policy Deny with Principal: * applies to every identity without exception, including the IAM user running CloudFormation. The deployment identity had no VPC endpoint stamp (CLI requests come from the internet), satisfying the first Deny condition. It was not in the ArnNotLike exemption list, satisfying the second condition. The Deny fired on the s3:PutBucketPolicy call before CloudFormation could apply any update.

The AWS account root user was also blocked — S3 bucket policy Deny statements with Principal: * apply to root. The root user could not view bucket objects in the S3 console until the bucket policy was deleted.

Recovery sequence:

Log in as root in the AWS console
Delete both bucket policies from the S3 console (S3 bucket -> Permissions -> Bucket policy -> Delete)
Stack was in UPDATE_ROLLBACK_FAILED at this point — ran: aws cloudformation continue-update-rollback --resources-to-skip RawBucketPolicy CuratedBucketPolicy
Waited for UPDATE_ROLLBACK_COMPLETE
Added dev-arch-project to the ArnNotLike exemption list in both bucket policies
Redeployed successfully

Lesson: the deployment identity must always be in the bucket policy exemption list before applying VPC enforcement. Root being blocked by the bucket policy Deny is the strongest possible evidence the enforcement is genuine — the Deny applies unconditionally regardless of identity or privilege level.

## Phase 10 — AWS Backup requires two separate role exemptions
Aws Backup start-backup-job failed with: "IAM Role does not have sufficient permissions to execute the backup" after only AWSServiceRoleForBackup was exempted in the bucket policy.

Root cause: AWS Backup S3 jobs use two distinct IAM roles with different functions: AWSServiceRoleForBackup — the AWS-managed service-linked role for orchestration, scheduling, and job management. Runs in AWS service infrastructure. financial-data-lake-backup-role — the customer-managed role that physically reads S3 objects during backup execution. Also runs in AWS service infrastructure.

The initial exemption only covered the SLR. The customer-managed role also makes direct S3 GetObject calls and was blocked by the VPC enforcement Deny.

Resolution: added financial-data-lake-backup-role to the ArnNotLike exemption list in both bucket policies alongside AWSServiceRoleForBackup.

Lesson: AWS Backup is a two-role service. Any bucket policy restricting S3 access by identity must exempt both the SLR and the customer-managed execution role.

## Phase 10 — Macie classification jobs returned 0 objects post-enforcement
Multiple manual classification jobs triggered after VPC enforcement was applied returned approximateNumberOfObjectsToProcess: 0 and completed in under 10 seconds.

Root cause: Macie's internal deduplication, not an access failure. Once an object version has been classified by any mechanism — a previous manual job or automated discovery — it is not rescanned by a subsequent one-time job unless the object itself changes. The field initialRun: false in the job response confirms this behaviour.

Evidence that Macie access was intact:

aws macie2 describe-buckets showed objectCount: 12 — Macie could enumerate bucket objects, which requires ListObjectsV2 to succeed through the SLR exemption.
The IAM policy simulator returned EvalDecision: allowed for the Macie SLR on both s3:GetObject and s3:ListBucket. Note: the simulator cannot simulate aws:SourceVpce context — it reaches the correct result for exempted roles because ArnNotLike matches, but cannot confirm VPC routing for non-exempted roles.
Macie's automated discovery independently generated 12 new findings on the raw bucket after enforcement was applied — the same 12 objects, scanned without any manual trigger. These are duplicate findings (same objects already found in Phase 8) but prove Macie retained access through the policy change.

## Phase 10 — CuratedBucketFindingsFilter drift: second occurrence
detect-stack-drift on financial-data-lake-macie reported DRIFTED on CuratedBucketFindingsFilter with FindingCriteria nulled to null. No console action was taken. A forced aws cloudformation update-stack restored the stack to UPDATE_COMPLETE but drift detection still reported DRIFTED on the same resource after the update.

This is the second occurrence of this pattern in the project. The first occurred in Phase 8 on the original FindingsFilter after a console action. This second occurrence involved no console action. In both cases: forced redeployment did not resolve the drift status, and aws macie2 list-findings-filters confirmed the filter is active and correctly configured despite the drift report.

There is no AWS documentation specifically citing AWS::Macie::FindingsFilter as having drift detection issues. This is documented as a project-specific empirical observation: CloudFormation cannot reliably reconcile this resource type after updates regardless of whether the deployed state matches the template. The DRIFTED status is not actionable.

## Phase 10 — Athena results written to wrong bucket
Early Athena verification commands in Sub-phase 2 used: --result-configuration OutputLocation=s3://financial-data-lake-raw-ACCOUNTID/athena-results/

This wrote Athena query results to the raw zone bucket, creating an athena-results/ prefix that violates the raw zone's immutable source data contract. A dedicated financial-data-lake-athena-results bucket exists in the S3 stack and was always the correct destination. The error was an incorrect output location in the verification command.

Cleanup: the athena-results/ prefix was deleted from the raw bucket after VPC enforcement was applied and direct CLI access became blocked. It was deleted while still accessible.

Going forward: all Athena queries use s3://financial-data-lake-athena-results-ACCOUNTID/ as the OutputLocation.

## Phase 10 — Drift detection: all six stacks
detect-stack-drift run across all six stacks after the full VPC Access Points build:

financial-data-lake-s3: IN_SYNC financial-data-lake-glue: IN_SYNC financial-data-lake-iam: IN_SYNC financial-data-lake-macie: DRIFTED (second occurrence of FindingsFilter false positive — see above) financial-data-lake-backup: IN_SYNC financial-data-lake-vpc: IN_SYNC

Five stacks IN_SYNC confirms the VPC infrastructure, Glue role split, scripts bucket, curated-to-refined job, refined zone crawler, and bucket policy enforcement were all made at the template layer. Macie drift status is a repeated platform limitation, not an actionable gap.