# infrastructure/scripts/fetch_edgar_data.py
# Fetches 10-K filing metadata for 5 major financial institutions
# from SEC EDGAR public API — no authentication required
# SEC EDGAR API reference: https://efts.sec.gov/LATEST/search-index
# Version 2 — adds proper logging, validation, and full history fetch

import requests
import time
import csv
import os
import logging
from datetime import datetime

# Configure logging — writes to both console and file
# This replaces print() statements so logs persist in AWS CloudWatch
# when this script runs as a Lambda function later
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(),  # console output
        logging.FileHandler('data/sample/fetch_log.txt')  # file output
    ]
)
logger = logging.getLogger(__name__)

# 5 major US financial institutions with their SEC CIK numbers
# CIK numbers are permanent public identifiers
# Verified at: https://www.sec.gov/cgi-bin/browse-edgar
COMPANIES = {
    "JPMorgan Chase":  "0000019617",
    "Bank of America": "0000070858",
    "Wells Fargo":     "0000072971",
    "Goldman Sachs":   "0000886982",
    "Morgan Stanley":  "0000895421"
}

# SEC requires identifying your script in the User-Agent header
# Without this the API returns 403 Forbidden
# Reference: https://www.sec.gov/os/accessing-edgar-data
HEADERS = {
    "User-Agent": "Portfolio Project aksha@email.com"
}

# How many annual reports to fetch per company
# 10 years of 10-K filings = ~50 rows total across 5 companies
MAX_FILINGS_PER_COMPANY = 10
TARGET_FORM_TYPE = "10-K"


def validate_filing(filing: dict) -> tuple:
    """
    Validates a filing record before saving.
    Returns (is_valid, reason) tuple.
    Added in v2 — catches malformed API responses early
    rather than writing bad data silently to CSV.
    """
    # Check all required fields are present and non-empty
    required_fields = [
        "company", "cik", "form_type",
        "filing_date", "accession_number"
    ]
    for field in required_fields:
        if not filing.get(field):
            return False, f"Missing or empty field: {field}"

    # Validate date format is YYYY-MM-DD
    try:
        datetime.strptime(filing["filing_date"], "%Y-%m-%d")
    except ValueError:
        return False, f"Invalid date format: {filing['filing_date']}"

    # Validate accession number format (should contain hyphens)
    if "-" not in filing["accession_number"]:
        return False, f"Malformed accession number: {filing['accession_number']}"

    return True, "valid"


def fetch_company_filings(cik: str, company_name: str) -> list:
    """
    Fetches full 10-K filing history for a company.
    Uses the submissions endpoint which returns all filings,
    not just the most recent 40.
    """

    # Primary endpoint — returns recent filings (last 40 across all types)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching {company_name} — SEC API too slow")
        return []
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching {company_name}: {e}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error fetching {company_name}: {e}")
        return []

    try:
        data = response.json()
    except ValueError:
        logger.error(f"Invalid JSON response for {company_name}")
        return []

    results = []

    # Process recent filings first
    recent = data.get("filings", {}).get("recent", {})
    results.extend(
        extract_10k_filings(recent, company_name, cik)
    )

    # If recent filings didn't give enough 10-K records,
    # fetch older filing pages from the files array
    # This is the fix for only getting 6 rows in v1
    filing_files = data.get("filings", {}).get("files", [])

    for filing_file in filing_files:
        if len(results) >= MAX_FILINGS_PER_COMPANY:
            break

        file_url = f"https://data.sec.gov/submissions/{filing_file['name']}"
        try:
            file_response = requests.get(
                file_url,
                headers=HEADERS,
                timeout=10
            )
            file_response.raise_for_status()
            older_filings = file_response.json()
            results.extend(
                extract_10k_filings(older_filings, company_name, cik)
            )
            # Respect SEC rate limit between pagination requests
            time.sleep(0.3)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not fetch older filings page for {company_name}: {e}")
            continue

    # Return up to MAX_FILINGS_PER_COMPANY results
    return results[:MAX_FILINGS_PER_COMPANY]


def extract_10k_filings(filings_data: dict, company_name: str, cik: str) -> list:
    """
    Extracts 10-K records from a filings data dictionary.
    Separated into its own function so it can process both
    recent filings and paginated older filings identically.
    """
    forms = filings_data.get("form", [])
    dates = filings_data.get("filingDate", [])
    accessions = filings_data.get("accessionNumber", [])
    descriptions = filings_data.get("primaryDocument", [])

    results = []
    for i, form in enumerate(forms):
        if form == TARGET_FORM_TYPE:
            filing = {
                "company": company_name,
                "cik": cik,
                "form_type": form,
                "filing_date": dates[i] if i < len(dates) else "",
                "accession_number": accessions[i] if i < len(accessions) else "",
                "primary_document": descriptions[i] if i < len(descriptions) else "",
                "filing_year": dates[i][:4] if i < len(dates) and dates[i] else ""
            }
            results.append(filing)

    return results


def main():
    # Create output directory — exist_ok prevents error if already exists
    os.makedirs("data/sample", exist_ok=True)

    logger.info("Starting SEC EDGAR 10-K filing fetch")
    logger.info(f"Target: {len(COMPANIES)} companies, "
                f"up to {MAX_FILINGS_PER_COMPANY} filings each")

    all_filings = []
    seen_accessions = set()  # tracks duplicates across companies
    validation_failures = 0

    for company, cik in COMPANIES.items():
        logger.info(f"Fetching filings for {company} (CIK: {cik})")
        filings = fetch_company_filings(cik, company)

        # Validate and deduplicate each filing
        for filing in filings:
            is_valid, reason = validate_filing(filing)

            if not is_valid:
                logger.warning(f"Skipping invalid record — {reason}: {filing}")
                validation_failures += 1
                continue

            if filing["accession_number"] in seen_accessions:
                logger.warning(f"Duplicate detected, skipping: "
                               f"{filing['accession_number']}")
                continue

            seen_accessions.add(filing["accession_number"])
            all_filings.append(filing)

        logger.info(f"  Accepted {len(filings)} valid filings for {company}")

        # SEC rate limit — max 10 requests per second
        # 0.5 second sleep keeps us at 2 requests/second safely
        time.sleep(0.5)

    # Write validated, deduplicated data to CSV
    output_file = "data/sample/financial_filings.csv"
    fieldnames = [
        "company", "cik", "form_type", "filing_date",
        "accession_number", "primary_document", "filing_year"
    ]

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_filings)

    # Summary report
    logger.info("=" * 50)
    logger.info(f"Fetch complete")
    logger.info(f"Total filings saved: {len(all_filings)}")
    logger.info(f"Validation failures: {validation_failures}")
    logger.info(f"Output file: {output_file}")
    logger.info("=" * 50)

    # Show sample of what was saved
    logger.info("Sample records:")
    for filing in all_filings[:5]:
        logger.info(f"  {filing['company']} | "
                    f"{filing['filing_date']} | "
                    f"{filing['form_type']}")


if __name__ == "__main__":
    main()