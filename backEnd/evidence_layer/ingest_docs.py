#!/usr/bin/env python3
"""
Standalone ingestion script for reference documents.
Bypasses CLI package issues by importing directly.
"""

from pathlib import Path

from src.ingest import ingest_document
from src.manifest import Corpus, DocType


def main():
    """Ingest all reference documents from backEnd/files/."""

    files_dir = Path(__file__).parent.parent / "files"

    # Documents to ingest
    documents = [
        # IRS Documents
        {
            "filename": "Publication_5653.pdf",
            "doc_type": DocType.IRS,
            "version": "IRS Cost Seg ATG (2024)",
        },
        {
            "filename": "Publication_946.pdf",
            "doc_type": DocType.IRS,
            "version": "IRS Pub 946 (2024)",
        },
        {
            "filename": "Publication_527.pdf",
            "doc_type": DocType.IRS,
            "version": "IRS Pub 527 (2024)",
        },
        {
            "filename": "Rev_Proc_87-56.pdf",
            "doc_type": DocType.IRS,
            "version": "Rev Proc 87-56",
        },
        # RSMeans Documents
        {
            "filename": "Residential_Costs_with_Rsmeans_Data_2020.pdf",
            "doc_type": DocType.RSMEANS,
            "version": "RSMeans Residential 2020",
        },
        {
            "filename": "Building_Construction_Costs_RSMeans_2020.pdf",
            "doc_type": DocType.RSMEANS,
            "version": "RSMeans Building 2020",
        },
    ]

    print(f"\n{'='*60}")
    print("Basis Evidence Layer - Reference Document Ingestion")
    print(f"{'='*60}\n")
    print(f"Files directory: {files_dir}")
    print(f"Documents to ingest: {len(documents)}\n")

    results = []

    for doc in documents:
        pdf_path = files_dir / doc["filename"]

        if not pdf_path.exists():
            print(f"❌ SKIP: {doc['filename']} (file not found)")
            continue

        print(f"\n{'─'*60}")
        print(f"📄 Ingesting: {doc['filename']}")
        print(f"   Type: {doc['doc_type'].value}")
        print(f"   Version: {doc['version']}")
        print(f"{'─'*60}")

        try:
            result = ingest_document(
                pdf_path=pdf_path,
                corpus=Corpus.REFERENCE,
                doc_type=doc["doc_type"],
                version_label=doc["version"],
                chunk_tokens=400,
                overlap_tokens=80,
                skip_vectors=False,
            )

            print(f"\n✅ SUCCESS: {result.doc_id}")
            print(f"   Elements: {result.num_elements}")
            print(f"   Tables: {result.num_tables}")
            print(f"   Chunks: {result.num_chunks}")
            print(f"   Data dir: {result.data_dir}")

            results.append({
                "filename": doc["filename"],
                "doc_id": result.doc_id,
                "status": "success",
                "elements": result.num_elements,
                "tables": result.num_tables,
                "chunks": result.num_chunks,
            })

        except Exception as e:
            print(f"\n❌ ERROR: {str(e)}")
            results.append({
                "filename": doc["filename"],
                "status": "error",
                "error": str(e),
            })

    # Summary
    print(f"\n\n{'='*60}")
    print("INGESTION SUMMARY")
    print(f"{'='*60}\n")

    success = [r for r in results if r.get("status") == "success"]
    errors = [r for r in results if r.get("status") == "error"]

    print(f"✅ Successful: {len(success)}")
    print(f"❌ Errors: {len(errors)}")

    if success:
        print("\nIngested documents:")
        for r in success:
            print(f"  • {r['doc_id']} ({r['chunks']} chunks)")

    if errors:
        print("\nFailed documents:")
        for r in errors:
            print(f"  • {r['filename']}: {r['error']}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
