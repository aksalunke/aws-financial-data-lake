# scripts/upload_raw_sample.py
import pandas as pd
import boto3
import io

BUCKET = "financial-data-lake-raw-114563865894"
PREFIX = "filings"
PROFILE = "personal"
REGION = "eu-west-2"
CSV_PATH = "data/sample/financial_filings.csv"
DATE_COLUMN = "filing_date"

# ── Load ─────────────────────────────────────────────────
df = pd.read_csv(CSV_PATH)

df[DATE_COLUMN] = pd.to_datetime(df[DATE_COLUMN])
df["_part_year"]  = df[DATE_COLUMN].dt.year.astype(str)
df["_part_month"] = df[DATE_COLUMN].dt.month.astype(str).str.zfill(2)

# ── Upload each partition ─────────────────────────────────
session = boto3.Session(profile_name=PROFILE, region_name=REGION)
s3 = session.client("s3")

for (year, month), group in df.groupby(["_part_year", "_part_month"]):
    partition_df = group.drop(columns=["_part_year", "_part_month"])

    key = f"{PREFIX}/ingest_year={year}/ingest_month={month}/financial_filings.csv"

    buffer = io.StringIO()
    partition_df.to_csv(buffer, index=False)

    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=buffer.getvalue().encode("utf-8")
    )

    print(f"Uploaded {len(partition_df)} rows → s3://{BUCKET}/{key}")

print("Done.")