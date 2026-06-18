# glue-jobs/raw_to_curated.py
# Glue ETL job: raw CSV → curated Parquet
# Runs on AWS Glue 3.0 with PySpark

import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import (
    col, to_date, year, month,
    sha2, when
)

args = getResolvedOptions(sys.argv, [
    'JOB_NAME',
    'raw_bucket',
    'curated_bucket'
])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# ── EXTRACT ───────────────────────────────────────────────
# Read from Glue Data Catalog — schema already registered by crawler
raw_df = glueContext.create_dynamic_frame.from_catalog(
    database="financial_data_lake",
    table_name="filings"
).toDF()

print(f"Records read from raw zone: {raw_df.count()}")

# ── TRANSFORM ─────────────────────────────────────────────

# 1. Cast filing_date to date type, derive year and month partition columns
cleaned_df = raw_df \
    .withColumn("filing_date", to_date(col("filing_date"), "yyyy-MM-dd")) \
    .withColumn("year", year(col("filing_date"))) \
    .withColumn("month", month(col("filing_date"))) \
    .filter(col("filing_date").isNotNull()) \
    .filter(col("company").isNotNull())

# 2. PII masking — SHA-256 hash the CIK
# CIK is a public SEC identifier but we demonstrate
# the masking pattern used for genuinely sensitive
# fields like account numbers or SSNs in production
cleaned_df = cleaned_df \
    .withColumn("cik_masked", sha2(col("cik").cast("string"), 256)) \
    .drop("cik")

# 3. Standardise company names to ticker symbols
cleaned_df = cleaned_df \
    .withColumn(
        "company_standardised",
        when(col("company") == "JPMorgan Chase", "JPM")
        .when(col("company") == "Bank of America", "BAC")
        .when(col("company") == "Wells Fargo", "WFC")
        .when(col("company") == "Goldman Sachs", "GS")
        .when(col("company") == "Morgan Stanley", "MS")
        .when(col("company") == "Citigroup", "C")
        .otherwise(col("company"))
    )

# ── LOAD ──────────────────────────────────────────────────
# Write to curated zone as Parquet partitioned by year/month
# Partitioning by year/month means Athena only scans
# the partitions matching a WHERE clause — reduces cost
output_path = f"s3://{args['curated_bucket']}/filings/"

cleaned_df.write \
    .mode("overwrite") \
    .partitionBy("year", "month") \
    .parquet(output_path)

print(f"Records written to curated zone: {cleaned_df.count()}")
print(f"Output path: {output_path}")

job.commit()