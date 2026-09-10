"""
EDGAR full-text search + fetch — Phase A, Step 1 of the Bedrock RAG extension.

Locates the most recent real 10-K filed by a given CIK via EDGAR's
full-text search API (EFTS: https://efts.sec.gov/LATEST/search-index),
then fetches the actual filing document and writes it as plain text
alongside a .metadata.json sidecar tagging it permission_level: public —
the honest tag for every document in the real corpus per the locked scope.

Dependencies:
    pip install requests beautifulsoup4

Usage:
    python edgar_fetch.py --cik 0000320193 --company "Apple Inc"

This is written against documented and community-verified EFTS behavior,
not tested against the live endpoint from this environment (no outbound
network access to sec.gov from here). Run it for real, against one CIK
already in your curated table, and paste back the actual output —
this is exactly the kind of thing worth verifying against reality before
trusting it, the same lesson as the dbt_project.yml / "Using ... file at"
line from the TfL build.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# SEC requires a descriptive User-Agent identifying the requester — a
# generic or missing one returns 403. Replace with your real contact info
# before running this for real.
HEADERS = {"User-Agent": "financial-data-lake-rag-poc your-email@example.com"}

# SEC's documented rate limit is 10 req/sec across all EDGAR endpoints,
# with a temporary IP block on breach. This project only needs a handful
# of filings, so stay well under it.
REQUEST_DELAY_SECONDS = 0.5


def search_10k_filing(cik: str) -> Optional[dict]:
    """
    Query EFTS for the most recent 10-K filed by this CIK.

    Returns the raw hit dict from EFTS's Elasticsearch-style response,
    or None if nothing matched.

    NOTE — unverified assumption flagged deliberately: EFTS is a full-text
    search engine, so `q` is normally a real keyword. Here it's set to a
    generic anchor term that should appear in virtually every 10-K's own
    cover page. If the first real run returns zero hits for a CIK you know
    has filed 10-Ks, this is the first parameter to inspect — try widening
    `q` or check whether an empty/omitted `q` is accepted for a pure
    ciks+forms listing.
    """
    padded_cik = cik.zfill(10)
    params = {
        "q": "10-K",
        "forms": "10-K",
        "ciks": padded_cik,
    }
    resp = requests.get(EFTS_URL, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        return None

    # Hits aren't guaranteed sorted by date — pick the most recent explicitly.
    hits.sort(key=lambda h: h["_source"].get("file_date", ""), reverse=True)
    return hits[0]


def build_document_url(hit: dict, cik: str) -> Tuple[str, str, str]:
    """
    Extract accession number and filename from an EFTS hit's `_id` field
    (format: "{accession-with-dashes}:{filename}") and construct the
    direct Archives URL for the filing document.

    Returns (accession_number, filename, url).
    """
    accession_with_dashes, filename = hit["_id"].split(":")
    accession_no_dashes = accession_with_dashes.replace("-", "")
    url = f"{ARCHIVES_BASE}/{int(cik)}/{accession_no_dashes}/{filename}"
    return accession_with_dashes, filename, url


def fetch_filing_text(url: str) -> str:
    """Fetch the filing document and strip HTML down to plain text."""
    time.sleep(REQUEST_DELAY_SECONDS)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    # Diagnostic — isolates whether any content loss happens at fetch time
    # (network/decoding) or parse time (BeautifulSoup). Compare all three:
    # if content_length and raw_bytes_received roughly match, the full
    # document arrived over the wire, and any shortfall in the final
    # extracted text points at the parser, not the network.
    content_length_header = resp.headers.get("Content-Length")
    print(f"  [diagnostic] Content-Length header: {content_length_header}")
    print(f"  [diagnostic] raw bytes received:    {len(resp.content):,}")
    print(f"  [diagnostic] decoded text length:    {len(resp.text):,}")

    # html.parser — proven empirically equivalent to lxml's HTML and XML
    # modes on two real filings (JPMorgan, Goldman Sachs): all three produced
    # byte-for-byte identical extracted output. No lxml dependency needed.
    # html.parser's leniency is a small real advantage on filers not yet
    # tested — a strict XML parser can hard-fail on any minor
    # well-formedness slip; html.parser tolerates it.
    #
    # The large raw-bytes-vs-extracted-text gap on iXBRL filings (seen here
    # as ~85-90% reduction) is expected, not a bug — confirmed via the
    # diagnostics below: 0 characters live inside script/style tags on
    # either filing tested, and every tagged number in iXBRL carries a
    # verbose attribute wrapper (contextRef, unitRef, decimals, scale,
    # name...) that get_text() correctly never counts, since it only reads
    # text nodes. Diagnostics kept permanently, not just for this
    # investigation — cheap, and they'll flag it immediately if some future
    # filer's document actually does hide real content in script/style.
    soup = BeautifulSoup(resp.text, "html.parser")

    removed = soup(["script", "style"])
    script_style_chars = sum(len(tag.get_text()) for tag in removed)
    print(f"  [diagnostic] chars inside script/style tags: {script_style_chars:,}")

    for tag in removed:
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse repeated blank lines left over from stripped markup.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    print(f"  [diagnostic] extracted text length:  {len(text):,}")
    return text


def write_output(company: str, cik: str, accession_number: str, text: str, out_dir: Path) -> None:
    """
    Write the filing text and its matching .metadata.json sidecar.
    Every chunk Bedrock derives from this document at ingestion inherits
    this document-level tag — satisfies "tag each chunk" per ADR #14
    without a custom chunking Lambda.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{cik}_{accession_number}"
    text_path = out_dir / f"{safe_name}.txt"
    meta_path = out_dir / f"{safe_name}.txt.metadata.json"

    text_path.write_text(text, encoding="utf-8")
    meta_path.write_text(json.dumps({
        "metadataAttributes": {
            "permission_level": "public",
            "company": company,
            "cik": cik,
            "accession_number": accession_number,
        }
    }, indent=2), encoding="utf-8")

    print(f"  wrote {text_path.name}  ({len(text):,} chars)")
    print(f"  wrote {meta_path.name}")


def main():
    parser = argparse.ArgumentParser(description="Fetch a real 10-K filing via EDGAR full-text search.")
    parser.add_argument("--cik", required=True, help="10-digit CIK, e.g. 0000320193")
    parser.add_argument("--company", required=True, help="Company name, for metadata tagging")
    parser.add_argument("--out", default="./kb-source/filings", help="Output directory")
    args = parser.parse_args()

    print(f"Searching EFTS for {args.company} (CIK {args.cik})...")
    hit = search_10k_filing(args.cik)
    if hit is None:
        print("No 10-K found for this CIK. Check the q/forms/ciks params above.", file=sys.stderr)
        sys.exit(1)

    accession_number, filename, url = build_document_url(hit, args.cik)
    print(f"Found: {hit['_source'].get('display_names')} — {filename}")
    print(f"Fetching {url} ...")

    text = fetch_filing_text(url)
    write_output(args.company, args.cik, accession_number, text, Path(args.out))
    print("Done.")


if __name__ == "__main__":
    main()