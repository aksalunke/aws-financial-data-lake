# AWS Financial Data Lake

## Business Problem
A financial institution needs to consolidate loan and deposit 
transaction data from multiple operational databases into a 
governed, queryable data lake. Analysts must be able to run 
ad-hoc SQL queries without impacting production systems. 
Sensitive customer PII must be masked at query time, and 
access must be controlled at the column level per role.

## Architecture
![Architecture Diagram](docs/diagrams/architecture.png)

## Architecture Insights
## Lake Formation Governance: What It Actually Costs

A common assumption about Lake Formation is that enabling it is a
single switch that adds column-level security on top of an
existing data lake, with no other side effects. Building this
project surfaced a more accurate picture.

By default, every Glue Data Catalog database and table is created
with an `IAMAllowedPrincipals` grant — a backward-compatibility
bypass that lets any principal with sufficient IAM permissions
access the resource, ignoring Lake Formation entirely. Lake
Formation is technically present from day one, but dormant.

Revoking `IAMAllowedPrincipals` is the action that actually turns
on enforcement. The assumption going in was that this revoke would
only affect the two new personas being introduced — a director
role with full access and an analyst role with masked columns.

In practice, removing that single global bypass affected every
principal already interacting with the catalog:

- **The Glue Crawler's IAM role** — previously ran successfully on
  IAM permissions alone. After the revoke, it required explicit
  Lake Formation grants (Describe, Create table at the database
  level; Alter, Describe, Drop at the table level) before it could
  run again, including on zones it had already crawled successfully
  before the revoke.

- **The account's own administrator user** — registering an IAM
  user as a Lake Formation data lake administrator grants the
  ability to *manage* permissions, not blanket bypass of table-level
  checks. A `DROP TABLE` operation, run from the same admin session
  that configured Lake Formation, still failed until that session
  was explicitly granted Drop permission on the table. Administrator
  status is the authority to grant permissions; it is not itself a
  permission to perform destructive actions.

- **The director and analyst roles** — the personas the project was
  actually built to demonstrate — still needed their own explicit
  grants on top of all of the above before any Athena query would
  succeed.

The practical lesson: `IAMAllowedPrincipals` is not a per-persona
setting. It is a single global switch. Turning it off does not
selectively restrict the two new roles you intend to govern — it
strips the IAM fallback from every principal that touches the
catalog, including infrastructure that was already working and
your own administrative session. Every one of those principals
then needs an explicit, audited Lake Formation grant before normal
operation resumes.

This is the actual operational cost of column-level governance —
not the column masking itself, which is straightforward to
configure, but the cascading permission grants required across
every existing principal once the IAM bypass is removed. A
production rollout of Lake Formation needs to budget for this:
identify every role and service that currently touches the catalog
*before* revoking `IAMAllowedPrincipals`, and have their grants
ready in advance — rather than discovering each one through a
failed operation, as happened during this build.


## AWS Services Used
- **Ingestion:** AWS DMS (Change Data Capture from Aurora)
- **Storage:** Amazon S3 (three-zone: raw / curated / refined)
- **Cataloguing:** AWS Glue Crawler, AWS Glue Data Catalog
- **Transformation:** AWS Glue ETL (PySpark), AWS Glue Studio
- **Governance:** AWS Lake Formation, Amazon Macie
- **Security:** AWS KMS, S3 VPC Access Points, IAM
- **Query:** Amazon Athena
- **Backup:** AWS Backup
- **IaC:** AWS CloudFormation with drift detection

## Data Source
SEC EDGAR public API — quarterly 10-K and 10-Q financial 
filings from five major US financial institutions.
Free, no authentication required.
Reference: https://www.sec.gov/cgi-bin/browse-edgar

## Project Structure
├── data/sample/          # Sample data files for testing
├── docs/                 # Architecture diagrams and decisions
├── infrastructure/       # CloudFormation templates and scripts
├── glue-jobs/            # PySpark ETL scripts
├── lambda-functions/     # Lambda code
├── athena-queries/       # SQL query examples
├── lake-formation/       # Permission configuration scripts
└── tests/                # Unit tests for ETL logic

## Architecture Decisions
See [docs/architecture-decisions.md](docs/architecture-decisions.md)

## Setup Guide
See [docs/setup-guide.md](docs/setup-guide.md)

## Estimated AWS Cost
Running this project at portfolio scale with small dataset: 
under $15/month. Destroy all resources after each session 
using the teardown script to minimise cost.



## Author
Akshay | AWS Solutions Architect | 
MSc Financial Technology | 
[Your LinkedIn URL]
