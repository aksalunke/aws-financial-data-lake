"""
Synthetic restricted test fixtures — separate ingestion path from the real
10-K corpus. Used only by the automated deny-path test suite to prove that
permission_level: restricted content is actually excluded from analyst-role
retrieval, and actually included for director-role retrieval. Design
rationale is in ADR #15 (depends on the authorization design in ADR #14).

These are NOT simulated sensitive financial material. They are boring,
self-describing placeholder documents with obviously fake identifiers.
Real 10-K filings never look like this on purpose — that's the point.

Each fixture contains one distinctive, wholly fictional "hook" term
(e.g. "Glenbarrow Ratio") specific enough that a semantic search for it
has no legitimate reason to match anything else in the corpus. That's
what makes the deny-path test meaningful rather than trivially true: the
content is genuinely retrievable in principle, and excluded only because
the permission filter is doing its job — not because nothing would have
matched it anyway.

Usage:
    python generate_synthetic_fixtures.py --out ./kb-source/test-fixtures
"""

import argparse
import json
from pathlib import Path

FIXTURES = [
    {
        "slug": "synthetic-0001",
        "company": "Test Fixture Alpha Holdings",
        "cik": "SYNTHETIC-CIK-0000000001",
        "accession_number": "SYNTHETIC-0000000001-00-000001",
        "hook_term": "Glenbarrow Ratio",
        "body": """SYNTHETIC TEST FIXTURE — NOT A REAL FILING

This document is a synthetic test fixture created for the AWS Financial
Data Lake project's governed RAG extension. It does not describe a real
company, a real filing, or real financial data of any kind. It exists
solely to verify that content tagged permission_level: restricted is
correctly excluded from retrieval results returned to the analyst
persona, and correctly included for the director persona.

Fictional entity name: Test Fixture Alpha Holdings
Fictional identifier: SYNTHETIC-CIK-0000000001
Fictional accession reference: SYNTHETIC-0000000001-00-000001

For test-query purposes, this fixture discusses a fictional internal
metric referred to as the Glenbarrow Ratio. In fiscal year 9999, Test
Fixture Alpha Holdings reported a fictional Glenbarrow Ratio of 17.3,
compared to a fictional prior-year Glenbarrow Ratio of 12.1. This figure
does not correspond to any real financial metric, accounting standard,
or disclosure requirement, and should not be interpreted as such.

This paragraph exists to give the fixture enough distinctive, retrievable
text that a semantic search for "Glenbarrow Ratio" or "Test Fixture Alpha
Holdings" would plausibly return this chunk if permission filtering were
not applied — which is the entire point of the fixture. If a query using
either term, issued under the analyst persona, ever returns this content,
the deny path has failed.""",
    },
    {
        "slug": "synthetic-0002",
        "company": "Placeholder Holdings Beta Inc.",
        "cik": "SYNTHETIC-CIK-0000000002",
        "accession_number": "SYNTHETIC-0000000002-00-000001",
        "hook_term": "Project Thistlewood",
        "body": """SYNTHETIC TEST FIXTURE — NOT A REAL FILING

This document is a synthetic test fixture created for the AWS Financial
Data Lake project's governed RAG extension. It does not describe a real
company, a real filing, or real financial data of any kind. It exists
solely to verify that content tagged permission_level: restricted is
correctly excluded from retrieval results returned to the analyst
persona, and correctly included for the director persona.

Fictional entity name: Placeholder Holdings Beta Inc.
Fictional identifier: SYNTHETIC-CIK-0000000002
Fictional accession reference: SYNTHETIC-0000000002-00-000001

For test-query purposes, this fixture discusses a fictional internal
designation referred to as Project Thistlewood. Placeholder Holdings
Beta Inc. describes Project Thistlewood as an internal restructuring
initiative with a fictional completion target of Q3 of fiscal year 9999.
No real corporate initiative, product, or transaction of this name exists.

This paragraph exists to give the fixture enough distinctive, retrievable
text that a semantic search for "Project Thistlewood" or "Placeholder
Holdings Beta" would plausibly return this chunk if permission filtering
were not applied. If a query using either term, issued under the analyst
persona, ever returns this content, the deny path has failed.""",
    },
]


def write_fixture(fixture: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir / f"{fixture['slug']}.txt"
    meta_path = out_dir / f"{fixture['slug']}.txt.metadata.json"

    text_path.write_text(fixture["body"], encoding="utf-8")
    meta_path.write_text(json.dumps({
        "metadataAttributes": {
            "permission_level": "restricted",
            "company": fixture["company"],
            "cik": fixture["cik"],
            "accession_number": fixture["accession_number"],
            # Independent second marker, beyond permission_level — cheap
            # insurance that these can be audited or excluded by a separate
            # signal if permission_level itself is ever the thing under test.
            "synthetic": True,
        }
    }, indent=2), encoding="utf-8")

    print(f"  wrote {text_path.name}  ({len(fixture['body']):,} chars)")
    print(f"  wrote {meta_path.name}")
    print(f"  hook term for later deny-path test: \"{fixture['hook_term']}\"")


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic restricted test fixtures for the deny-path test suite."
    )
    parser.add_argument("--out", default="./kb-source/test-fixtures", help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out)
    for fixture in FIXTURES:
        write_fixture(fixture, out_dir)

    print(f"\n{len(FIXTURES)} synthetic fixtures written to {out_dir}/")
    print("Separate directory from the real corpus (kb-source/filings/) —")
    print("matching the separate-ingestion-path requirement in ADR #14/#15.")


if __name__ == "__main__":
    main()