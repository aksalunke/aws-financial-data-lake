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
