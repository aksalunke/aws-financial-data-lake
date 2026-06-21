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