# Architecture Decisions

## 1. Why SEC EDGAR public API over Aurora + AWS DMS
The original project plan included Amazon Aurora as a simulated source database with AWS DMS for Change Data Capture. This was replaced with direct ingestion from the SEC EDGAR public API
because it produces real, verifiable financial filing data at no cost and with no authentication requirement — strengthening portfolio credibility over synthetic data.

DMS provides CDC capability to minimise load on a live source database processing transactions. Since this project has no live source database, DMS adds no value and was correctly dropped.
The EDGAR API fetch script (scripts/edgar_fetch.py) replaces the entire Aurora + DMS layer with a direct HTTP call to a public REST endpoint.

Note: Macie, AWS Backup and VPC Access Points were also in the original plan and are documented as a planned Project 1 extension. All three are now implemented — see ADR #10, ADR #12, and ADR #13.

## 2. Why three S3 zones, not two
The raw zone is immutable — data is written exactly as ingested and never modified. This satisfies audit trail requirements: regulators can always verify the source data was not altered before transformation. The curated zone holds cleaned, Parquet-format data partitioned by year/month. The refined zone holds two pre-computed aggregation tables produced by a second Glue ETL job (curated_to_refined.py): filings_by_company_year and filings_by_year. These are BI-ready rollup tables that Athena queries directly rather than scanning individual curated records.

A two-zone model loses the ability to re-derive any transformation from the original, untouched source.

Note: the refined zone was provisioned in Phase 3 but not populated until the VPC Access Points extension phase, when the curated-to-refined ETL job and refined zone crawler were implemented. Implementing the refined zone at this stage also resolved the Athena/VPC enforcement conflict — VPC enforcement is scoped to raw and curated (individual records), while the refined zone (aggregated outputs with no sensitive identifiers) remains accessible to Athena under identity controls only.

## 3. Raw zone partitioned by filing_date, not ingest date
The raw zone uses filing_date (the SEC submission date) as the partition key rather than the date the fetch script was executed.

Trade-off accepted: ingest batch timing is not captured in the partition path, making it harder to isolate and reprocess a specific fetch batch.

Trade-off avoided: re-running the fetch script overwrites the same S3 key rather than creating duplicate partitions. In a financial data pipeline, duplicate records are a harder failure
to detect and correct than a missing batch timestamp. This preserves idempotency — the same filing always lands at the same path regardless of when it was fetched.

Production extension: a dedicated landing zone partitioned by ingest timestamp would sit upstream of the raw zone, capturing every batch before deduplication. Deferred for portfolio scope.

## 4. Glue IAM role split — crawler role and ETL role
Originally, a single IAM role (financial-data-lake-glue-crawler-role) served both the Glue Crawler and the Glue ETL job. This was documented at build time as a least-privilege gap: the combined role carried curated write access that the crawler never used.

This was resolved during the VPC Access Points extension phase. Two separate roles are now deployed in financial-data-lake-glue.yaml:

financial-data-lake-glue-crawler-role — read-only across all three data zones (raw, curated, refined). No write access to any zone. financial-data-lake-glue-etl-role — raw read, curated and refined write, scripts bucket read. No access beyond what each ETL job actually requires.

The role split also enabled a cleaner bucket policy exemption strategy in Sub-phase 3: the crawler role is exempted from VPC enforcement by ARN (crawlers run in AWS-managed infrastructure), while the ETL role is not exempted (ETL workers run inside the VPC and carry the aws:SourceVpce stamp). A successful ETL job run after VPC enforcement was applied is the proof that the role separation and VPC routing are both working correctly.

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

Trade-off avoided: without a curated zone crawler, the Glue Data Catalog only reflects the raw zone schema. Lake Formation column-level permissions applied to the raw schema would govern the wrong layer — analysts should query the curated zone where data is cleaned, transformed, and PII is already masked at the column level. The curated crawler is scheduled one hour after the raw crawler to ensure ETL completion before schema discovery.

Note: a third crawler (financial-data-lake-refined-crawler) was added during the VPC Access Points extension phase to catalog the two refined zone aggregation tables. It runs without a schedule — triggered manually after each curated-to-refined job execution.

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
All six CloudFormation stacks were checked with aws cloudformation detect-stack-drift after the full build including the VPC Access Points extension:

financial-data-lake-s3: IN_SYNC
financial-data-lake-glue: IN_SYNC
financial-data-lake-iam: IN_SYNC
financial-data-lake-macie: DRIFTED (false positive — see below)
financial-data-lake-backup: IN_SYNC
financial-data-lake-vpc: IN_SYNC

Five IN_SYNC stacks confirm every fix applied across the full build — the IAM policy additions, the TablePrefix correction, the curated crawler addition, the explicit column selection in the ETL script, the backup vault and plan, the VPC infrastructure, the bucket policy enforcement, and the Glue role split — was made by editing and redeploying CloudFormation templates, not by ad-hoc console edits that would silently diverge from the committed source of truth.

The Macie stack reports DRIFTED due to a repeated CloudFormation/Macie drift detection issue on the AWS::Macie::FindingsFilter resource type. This has occurred twice across the project: first on the original FindingsFilter in Phase 8, and again on CuratedBucketFindingsFilter during the VPC extension phase. In both cases: no console action was taken, a forced aws cloudformation update-stack did not resolve it, and aws macie2 list-findings-filters confirmed the filter is active and correctly configured. There is no AWS documentation specifically naming this resource type as having drift detection issues. It is documented as a project-specific empirical observation: CloudFormation cannot reliably reconcile AWS::Macie::FindingsFilter after updates, regardless of whether the deployed state matches the template. The DRIFTED status is not actionable and has no functional impact.

Scope limitation: drift detection only covers resources defined in the templates. Lake Formation permissions and the IAMAllowedPrincipals revocation are account-level and database-level settings outside CloudFormation's resource model — they cannot be drift-checked by this command regardless of whether they match documentation. See ADR #7.

A reusable drift detection script is at: infrastructure/scripts/check-drift.ps1

## 9. Why Parquet format in the curated zone
Parquet is columnar — Athena scans only the columns referenced in a query. A query selecting 3 columns from a 50-column table scans approximately 6% of the data versus 100% with CSV. Combined with date partitioning, this reduces Athena query costs by 80-95% compared to unpartitioned CSV.
Reference: AWS Athena performance tuning documentation.

## 10. Macie classification job excluded from CloudFormation
The financial-data-lake-macie stack manages three Macie resources via CloudFormation:
the Macie session (AWS::Macie::Session), the custom CIK data identifier
(AWS::Macie::CustomDataIdentifier), and the findings filter (AWS::Macie::FindingsFilter).

The classification job — the scan that actually inspects S3 objects for sensitive data —
is not managed by CloudFormation. No AWS::Macie::ClassificationJob resource type exists.
The job is created via CLI using aws macie2 create-classification-job after stack deployment.

The stack output CustomDataIdentifierId serves as the explicit handoff between the IaC
layer and the CLI step — the job creation command consumes this value to connect the
scan to the custom CIK detection pattern defined in the template.

Trade-off accepted: the classification job is not reproducible via stack teardown and
redeploy. The job ID is ephemeral and does not persist. A new job must be created via CLI
after each redeploy.

Trade-off avoided: building a workaround (e.g. CloudFormation custom resource invoking
a Lambda to call CreateClassificationJob) would add significant complexity for no
meaningful benefit at portfolio scale.

Production fix: an EventBridge scheduled rule triggering a Lambda function that calls
CreateClassificationJob on a defined schedule would make job creation operationally
reproducible without a native CloudFormation resource type. This also enables periodic
scans rather than a one-time job.

Same category as ADR #7 — an AWS platform constraint on initial setup, not a gap in
the design.

## 11. Macie EventBridge alerting not configured
Macie findings currently appear in the console only. No proactive alerting or automated
remediation is configured. Automated sensitive data discovery runs continuously in the
background and samples all S3 buckets in the region by default — policy findings are
generated automatically if bucket configuration changes, but nothing notifies an operator
when they appear.

Trade-off accepted: findings require manual console review to be actioned. In a production
environment this is not acceptable — findings would go unnoticed until someone happened
to open the Macie console.

Trade-off avoided: adding EventBridge + SNS + Lambda for alerting at this stage would
expand scope before the core Macie proof-of-concept was validated. The finding detection
story — custom identifier matching CIK patterns in the raw zone — is the primary
portfolio objective. Alerting is operational infrastructure layered on top of it.

Production fix: an EventBridge rule targeting aws.macie2 events with detail-type
"Macie Finding" would trigger an SNS notification or Lambda for automated remediation
such as blocking public access or quarantining an affected object. Severity filtering
on the EventBridge rule would suppress low-severity findings from generating noise.
Scoped as a known gap for this project.

## 12. AWS Backup S3 regional opt-in excluded from CloudFormation
The financial-data-lake-backup stack manages all AWS Backup resources via CloudFormation: the backup vault, vault KMS key, IAM role, backup plan, and backup selection covering all three S3 zones.

One prerequisite sits outside the stack: the S3 service opt-in for AWS Backup is controlled by aws backup update-region-settings, which has no CloudFormation resource type equivalent. This setting must be verified or set via CLI before the backup plan can discover and protect S3 resources through tag-based selection.

Verified state: aws backup describe-region-settings confirmed S3 opt-in was already true in eu-west-2 prior to stack deployment. No update was required.

Nuance: the opt-in is only enforced when AWS Backup uses tag-based resource discovery. The financial-data-lake-backup stack uses explicit ARN-based resource selection in the BackupSelection block — all three zone bucket ARNs are listed directly in the Resources array. The AWS Backup API documentation states that explicitly assigned resource ARNs are included in the backup even if the opt-in is disabled for that service. The opt-in verification step is therefore a hygiene check for this project rather than a functional prerequisite, but is documented because the account-wide setting would affect any future tag-based selection added to this or any other backup plan.

Trade-off accepted: the opt-in state is not captured in the stack template and cannot be drift-checked or reproduced by a stack teardown and redeploy.

Trade-off avoided: building a CloudFormation custom resource to wrap the CLI call would add Lambda and IAM complexity for a one-time account-level setting that is unlikely to change.

Same category as ADR #10 — an AWS platform constraint on initial setup, not a gap in the design.

## 13. VPC Access Points — network-layer S3 restriction
What this ADR covers:
The VPC Access Points extension adds a network-origin restriction layer to the raw and curated S3 zones. This ADR documents the design decisions and their trade-offs. The implementation spans three sub-phases: VPC infrastructure (financial-data-lake-vpc stack), Glue VPC wiring and refined zone (financial-data-lake-glue update), and bucket policy enforcement (financial-data-lake-s3 update).

Decision 1: S3 Gateway Endpoint over Interface Endpoint
S3 Gateway Endpoints are free, attach to route tables, and are sufficient for S3-only traffic restriction. S3 Interface Endpoints (PrivateLink) cost approximately $7/month per Availability Zone and provide private DNS resolution — benefits that are not needed when the only requirement is routing S3 traffic off the public internet and enabling aws:SourceVpce in the request context.

Trade-off accepted: gateway endpoints only support S3 and DynamoDB. Any other service requiring VPC-private access would need a separate Interface Endpoint.

Decision 2: New dedicated VPC over default VPC
The AWS default VPC has public subnets and no S3 gateway endpoint configured. A dedicated VPC with a private subnet, no internet gateway, and a gateway endpoint to S3 is the production-correct pattern and creates a clean, auditable network boundary for the data lake.

Decision 3: Enforcement scoped to raw and curated zones only
The refined zone has no VPC enforcement. Athena cannot run in a customer VPC — it runs in AWS-managed infrastructure and accesses S3 from there. Applying aws:SourceVpce enforcement to the refined zone would block Athena queries entirely. Since the refined zone contains only aggregated outputs with no sensitive identifiers (neither cik_masked nor accession_number survive aggregation), identity controls (IAM + Lake Formation) are sufficient at that layer.

Production fix: EMR running inside the VPC could serve as the query engine for a fully VPC-enforced architecture. Athena is retained as the analytics interface by scoping enforcement to the zones containing individual records.

Decision 4: Bucket policy aws:SourceVpce enforcement as primary mechanism
VPC-configured Access Points as secondary layer Both mechanisms are implemented as defence in depth, mirroring the CIK masking / Macie detection pattern already in the project.

aws:SourceVpce in the bucket policy Deny: rejects any request without the VPC endpoint stamp at the bucket level, regardless of credentials or identity.

Access points with VpcConfiguration.VpcId: rejects any request not originating from within the specified VPC at the access point level.

The bucket policy is the primary enforcement mechanism because the ETL code uses standard bucket-name S3 paths that route through the gateway endpoint transparently. The access points demonstrate the VPC-restricted access point pattern for production reference.

Decision 5: Crawlers exempted by role ARN, not VPC-connected
Glue Crawlers for S3 targets run in AWS-managed infrastructure without VPC connectivity. Configuring crawlers with VPC connections is possible for JDBC data sources but is not the standard pattern for S3 crawlers. Exempting the crawler role by ARN in the bucket policy Deny is the pragmatic approach and is consistent with the Macie and Backup exemption pattern already in the project.

The ETL role is NOT exempted — ETL workers run inside the VPC and requests carry aws:SourceVpce. A successful ETL job run after enforcement was applied is the proof that VPC routing is working. If the ETL role were also exempted, this proof would not be possible.

Decision 6: Four bucket policy exemptions
The following identities are exempted from the aws:SourceVpce Deny on the raw and curated buckets:

AWSServiceRoleForAmazonMacie — scans raw zone from AWS service infrastructure. AWSServiceRoleForBackup — orchestrates backup jobs from AWS service infrastructure. financial-data-lake-backup-role — physically reads S3 objects during backup execution. Note: AWS Backup S3 jobs use two separate roles — the SLR for orchestration and a customer-managed role for data access. Both must be exempted. This was discovered during verification when the backup job failed after only the SLR was exempted. dev-arch-project (IAM user) — CloudFormation runs as this identity. Any bucket policy update fails with AccessDenied if the deployment identity is not exempted — the Deny fires on s3:PutBucketPolicy before CloudFormation can apply the update. This was discovered when the stack entered UPDATE_ROLLBACK_FAILED and required root console access to delete the bucket policies before recovery. The deployment identity must always be in the exemption list when applying VPC enforcement.

Documented limitations:
Athena is blocked from querying raw and curated zones after enforcement is applied. This is expected and documented — Athena queries the refined zone only.

The IAM policy simulator cannot simulate aws:SourceVpce context. It evaluates bucket policy Deny conditions as if the key is absent, which produces the correct result for exempted roles (Deny does not fire) but cannot confirm VPC routing for non-exempted roles. A live ETL job succeeding after enforcement is the only reliable proof of VPC routing.

Macie's automated sensitive data discovery confirmed access is intact post-enforcement by generating 12 new findings independently. Manual classification jobs returned 0 objects due to Macie's internal deduplication — objects already classified by automated discovery are not rescanned by one-time manual jobs. This is expected behaviour, not an access failure.

Additional improvements implemented in this extension:
Three gaps documented during the original build were resolved as part of this extension:

Scripts bucket: raw_to_curated.py was stored in the raw zone bucket since Phase 5, violating the raw zone's immutable source data contract and causing Macie to scan scripts alongside data objects. A dedicated financial-data-lake-scripts bucket was created in the Glue stack with SSE-S3 encryption. Both ETL scripts were moved there.

Glue job under CloudFormation: the raw-to-curated job was created via CLI and was not reproducible via stack teardown and redeploy. It is now defined in financial-data-lake-glue.yaml alongside the new curated-to-refined job.

Glue role split: implemented as part of this extension — see ADR #4.
