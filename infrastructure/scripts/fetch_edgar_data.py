# infrastructure/scripts/fetch_edgar_data.py
# Fetches 10-K filing metadata for 5 major financial institutions
# from SEC EDGAR public API — no authentication required
# SEC EDGAR API reference: https://efts.sec.gov/LATEST/search-index

import requests
import json
import time
import csv
import os

# 5 major US financial institutions with their SEC CIK numbers
# CIK numbers are public identifiers — no sensitive data
COMPANIES = {
    "JPMorgan Chase": "0000019617",
    "Bank of America": "0000070858",
    "Wells Fargo": "0000072971",
    "Goldman Sachs": "0000886982",
    "Morgan Stanley": "0000895421"
}

# SEC requires identifying your script in the User-Agent header
# Reference: https://www.sec.gov/os/accessing-edgar-data
HEADERS = {
    "User-Agent": "Portfolio Project Akshay Salunke"
}

def fetch_company_filings(cik: str, company_name: str) -> list:
    """
    Fetch recent 10-K annual report filings for a company.
    Returns list of filing metadata dictionaries.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {company_name}: {e}")
        return []
    
    data = response.json()
    filings = data.get("filings", {}).get("recent", {})
    
    forms = filings.get("form", [])
    dates = filings.get("filingDate", [])
    accessions = filings.get("accessionNumber", [])
    
    results = []
    for i, form in enumerate(forms):
        if form == "10-K":
            results.append({
                "company": company_name,
                "cik": cik,
                "form_type": form,
                "filing_date": dates[i],
                "accession_number": accessions[i]
            })
    
    # Return last 5 annual reports per company
    return results[:5]

def main():
    all_filings = []
    
    for company, cik in COMPANIES.items():
        print(f"Fetching filings for {company}...")
        filings = fetch_company_filings(cik, company)
        all_filings.extend(filings)
        print(f"  Found {len(filings)} 10-K filings")
        # SEC rate limit: max 10 requests per second
        time.sleep(0.5)
    
    # Save to CSV in data/sample folder
    os.makedirs("data/sample", exist_ok=True)
    output_file = "data/sample/financial_filings.csv"
    
    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company", "cik", "form_type",
            "filing_date", "accession_number"
        ])
        writer.writeheader()
        writer.writerows(all_filings)
    
    print(f"\nComplete. Saved {len(all_filings)} filings to {output_file}")
    print("\nSample records:")
    for filing in all_filings[:3]:
        print(f"  {filing['company']} | {filing['filing_date']} | {filing['form_type']}")

if __name__ == "__main__":
    main()