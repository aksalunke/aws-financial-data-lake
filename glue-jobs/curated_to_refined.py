# glue-jobs/curated_to_refined.py
# Glue ETL job: curated Parquet -> refined aggregations
# Runs on AWS Glue 3.0 with PySpark

import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import (
    col, min, max, count, countDistinct
)

args = getResolvedOptions(sys.argv, [
    'JOB_NAME',
    'refined_bucket'
])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# ── EXTRACT ───────────────────────────────────────────────
# Read from Glue Data Catalog — curated table registered by the
# curated zone crawler. Table name is curated_filings: the crawler
# uses TablePrefix 'curated_' and the S3 target path ends in 'filings/'.
curated_df = glueContext.create_dynamic_frame.from_catalog(
    database="financial_data_lake",
    table_name="curated_filings"
).toDF()

print(f"Records read from curated zone: {curated_df.count()}")

# ── TRANSFORM ─────────────────────────────────────────────

# Aggregation 1: filing counts per company per year
# One row per company per year. Answers: how many 10-K filings did
# each company submit in each year, and across what date range.
# This is the rollup a BI dashboard queries directly rather than
# scanning individual curated records on every request.
by_company_year_df = curated_df \
    .groupBy("company_standardised", "filing_year") \
    .agg(
        count("*").alias("total_filings"),
        min("filing_date").alias("first_filing"),
        max("filing_date").alias("most_recent_filing")
    ) \
    .select(
        "company_standardised",
        "filing_year",
        "total_filings",
        "first_filing",
        "most_recent_filing"
    ) \
    .orderBy("company_standardised", "filing_year")

print(f"Company-year aggregation rows: {by_company_year_df.count()}")

# Aggregation 2: market-level filing volume per year
# One row per year across all filers in the dataset. Answers: how
# active was the 10-K filing market in each calendar year.
by_year_df = curated_df \
    .groupBy("filing_year") \
    .agg(
        count("*").alias("total_filings"),
        countDistinct("company_standardised").alias("unique_filers")
    ) \
    .select(
        "filing_year",
        "total_filings",
        "unique_filers"
    ) \
    .orderBy("filing_year")

print(f"Year aggregation rows: {by_year_df.count()}")

# ── LOAD ──────────────────────────────────────────────────
# Two separate output prefixes in the refined zone — one per
# aggregation. No partition columns: aggregated tables are small
# enough that full table scans in Athena carry negligible cost.
#
# Neither cik_masked nor accession_number survive aggregation —
# they are individual-record identifiers with no meaning in a
# grouped output. The refined zone therefore carries no sensitive
# identifiers and requires no column-level Lake Formation restriction
# or VPC bucket policy enforcement. Identity controls (IAM) are
# sufficient at this layer.

by_company_year_path = f"s3://{args['refined_bucket']}/filings_by_company_year/"
by_year_path = f"s3://{args['refined_bucket']}/filings_by_year/"

by_company_year_df.write \
    .mode("overwrite") \
    .parquet(by_company_year_path)

print(f"Company-year aggregation written to: {by_company_year_path}")

by_year_df.write \
    .mode("overwrite") \
    .parquet(by_year_path)

print(f"Year aggregation written to: {by_year_path}")

job.commit()