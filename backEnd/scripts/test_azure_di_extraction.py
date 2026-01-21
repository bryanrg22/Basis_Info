#!/usr/bin/env python3
"""
Test Azure DI extraction across multiple PDFs.

Reports which fields were extracted vs which fell back to vision.

Usage:
    python scripts/test_azure_di_extraction.py path/to/pdf1.pdf path/to/pdf2.pdf ...
    python scripts/test_azure_di_extraction.py data/appraisals/*.pdf
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project paths
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "evidence_layer" / "src"))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Critical fields we care most about
CRITICAL_FIELDS = [
    "subject.property_address",
    "improvements.year_built",
    "improvements.gross_living_area",
    "reconciliation.final_opinion_of_market_value",
    "listing_and_contract.contract_price",
    "reconciliation.effective_date",
]


async def test_extraction(pdf_path: str) -> dict:
    """Test Azure DI extraction on a single PDF."""
    from tiered_extraction.azure_di_extractor import AzureDocumentExtractor

    extractor = AzureDocumentExtractor()
    if not extractor.is_available():
        return {"error": "Azure DI not configured"}

    result = await extractor.extract(pdf_path)

    # Collect extracted fields
    extracted_fields = {}
    for section, fields in result.sections.items():
        for field_name, field_data in fields.items():
            key = f"{section}.{field_name}"
            extracted_fields[key] = {
                "value": field_data.value,
                "confidence": field_data.confidence,
            }

    return {
        "pdf": pdf_path,
        "total_fields": len(extracted_fields),
        "overall_confidence": result.overall_confidence,
        "needs_review": result.needs_review,
        "extracted_fields": extracted_fields,
    }


def print_report(results: list[dict]):
    """Print a summary report."""
    print("\n" + "=" * 70)
    print("AZURE DI EXTRACTION REPORT")
    print("=" * 70)

    for result in results:
        if "error" in result:
            print(f"\n❌ {result.get('pdf', 'unknown')}: {result['error']}")
            continue

        pdf_name = Path(result["pdf"]).name
        print(f"\n📄 {pdf_name}")
        print(f"   Total fields extracted: {result['total_fields']}")
        print(f"   Overall confidence: {result['overall_confidence']:.1%}")
        print(f"   Needs review: {'Yes ⚠️' if result['needs_review'] else 'No ✅'}")

        # Check critical fields
        print("\n   Critical Fields:")
        missing_critical = []
        for field in CRITICAL_FIELDS:
            if field in result["extracted_fields"]:
                data = result["extracted_fields"][field]
                conf = data["confidence"]
                status = "✅" if conf >= 0.9 else "⚠️" if conf >= 0.7 else "❌"
                value_preview = str(data["value"])[:30]
                print(f"     {status} {field}: {value_preview}... ({conf:.1%})")
            else:
                print(f"     ❌ {field}: MISSING")
                missing_critical.append(field)

        if missing_critical:
            print(f"\n   ⚠️  {len(missing_critical)} critical fields missing - would trigger vision fallback")
        else:
            print(f"\n   ✅ All critical fields extracted!")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_pdfs = len(results)
    successful = sum(1 for r in results if "error" not in r)
    avg_fields = sum(r.get("total_fields", 0) for r in results) / max(successful, 1)

    all_missing = []
    for result in results:
        if "error" not in result:
            for field in CRITICAL_FIELDS:
                if field not in result["extracted_fields"]:
                    all_missing.append(field)

    print(f"PDFs processed: {successful}/{total_pdfs}")
    print(f"Average fields extracted: {avg_fields:.0f}")

    if all_missing:
        from collections import Counter
        missing_counts = Counter(all_missing)
        print("\nMost commonly missing fields:")
        for field, count in missing_counts.most_common(5):
            print(f"  - {field}: missing in {count}/{successful} PDFs")
    else:
        print("\n✅ All critical fields extracted in all PDFs!")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python test_azure_di_extraction.py <pdf_path> [pdf_path2] ...")
        print("\nExample:")
        print("  python scripts/test_azure_di_extraction.py data/appraisals/*.pdf")
        sys.exit(1)

    pdf_paths = sys.argv[1:]
    print(f"Testing Azure DI extraction on {len(pdf_paths)} PDF(s)...")

    results = []
    for pdf_path in pdf_paths:
        if not Path(pdf_path).exists():
            results.append({"pdf": pdf_path, "error": "File not found"})
            continue

        print(f"\nProcessing: {pdf_path}...")
        result = await test_extraction(pdf_path)
        results.append(result)

    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
