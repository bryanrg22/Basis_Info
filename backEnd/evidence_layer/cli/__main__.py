"""
CLI for evidence layer.

Commands:
    ingest   - Ingest a PDF into the evidence layer
    search   - Search indexed documents
    info     - Show document info
"""

from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table as RichTable

app = typer.Typer(
    name="evidence-layer",
    help="Basis Evidence Layer - PDF ingestion and retrieval",
)
console = Console()


@app.command()
def ingest(
    pdf_path: Path = typer.Argument(..., help="Path to PDF file or directory"),
    corpus: str = typer.Option("reference", "--corpus", "-c", help="Corpus: reference or study"),
    doc_type: str = typer.Option("irs", "--doc-type", "-t", help="Doc type: irs, rsmeans, appraisal, invoice, other"),
    study_id: Optional[str] = typer.Option(None, "--study-id", "-s", help="Study ID (required for study corpus)"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Version label (e.g., 'IRS Pub 946 (2024)')"),
    chunk_tokens: int = typer.Option(400, "--chunk-tokens", help="Target tokens per chunk"),
    overlap_tokens: int = typer.Option(80, "--overlap-tokens", help="Overlap tokens between chunks"),
    skip_vectors: bool = typer.Option(False, "--skip-vectors", help="Skip vector index (faster)"),
):
    """
    Ingest a PDF into the evidence layer.
    
    Examples:
        # Ingest IRS publication to reference corpus
        evidence-layer ingest pub946.pdf --corpus reference --doc-type irs --version "IRS Pub 946 (2024)"
        
        # Ingest appraisal to study corpus
        evidence-layer ingest appraisal.pdf --corpus study --doc-type appraisal --study-id STUDY_001
        
        # Ingest all PDFs in a directory
        evidence-layer ingest ./irs_docs/ --corpus reference --doc-type irs
    """
    from ..src.ingest import ingest_directory, ingest_document
    from ..src.manifest import Corpus, DocType
    
    # Validate inputs
    try:
        corpus_enum = Corpus(corpus)
    except ValueError:
        rprint(f"[red]Invalid corpus: {corpus}. Must be 'reference' or 'study'[/red]")
        raise typer.Exit(1)
    
    try:
        doc_type_enum = DocType(doc_type)
    except ValueError:
        valid_types = ", ".join(dt.value for dt in DocType)
        rprint(f"[red]Invalid doc type: {doc_type}. Must be one of: {valid_types}[/red]")
        raise typer.Exit(1)
    
    if corpus_enum == Corpus.STUDY and not study_id:
        rprint("[red]--study-id is required for study corpus[/red]")
        raise typer.Exit(1)
    
    pdf_path = Path(pdf_path)
    
    if pdf_path.is_dir():
        # Ingest all PDFs in directory
        results = ingest_directory(
            input_dir=pdf_path,
            corpus=corpus_enum,
            doc_type=doc_type_enum,
            study_id=study_id,
            version_label=version,
            chunk_tokens=chunk_tokens,
            overlap_tokens=overlap_tokens,
            skip_vectors=skip_vectors,
        )
        
        # Summary table
        if results:
            table = RichTable(title="Ingestion Results")
            table.add_column("Doc ID", style="cyan")
            table.add_column("Elements", justify="right")
            table.add_column("Tables", justify="right")
            table.add_column("Chunks", justify="right")
            
            for r in results:
                table.add_row(
                    r.doc_id,
                    str(r.num_elements),
                    str(r.num_tables),
                    str(r.num_chunks),
                )
            
            console.print(table)
    else:
        # Ingest single PDF
        if not pdf_path.exists():
            rprint(f"[red]File not found: {pdf_path}[/red]")
            raise typer.Exit(1)
        
        result = ingest_document(
            pdf_path=pdf_path,
            corpus=corpus_enum,
            doc_type=doc_type_enum,
            study_id=study_id,
            version_label=version,
            chunk_tokens=chunk_tokens,
            overlap_tokens=overlap_tokens,
            skip_vectors=skip_vectors,
        )
        
        rprint(f"\n[green]✓ Ingested:[/green] {result.doc_id}")
        rprint(f"  Elements: {result.num_elements}")
        rprint(f"  Tables: {result.num_tables}")
        rprint(f"  Chunks: {result.num_chunks}")
        rprint(f"  Data dir: {result.data_dir}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    doc_id: str = typer.Option(..., "--doc-id", "-d", help="Document ID to search"),
    method: str = typer.Option("hybrid", "--method", "-m", help="Search method: bm25, vector, hybrid"),
    corpus: str = typer.Option("reference", "--corpus", "-c", help="Corpus: reference or study"),
    study_id: Optional[str] = typer.Option(None, "--study-id", "-s", help="Study ID (for study corpus)"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
    show_text: bool = typer.Option(True, "--show-text/--hide-text", help="Show chunk text"),
):
    """
    Search indexed documents.
    
    Examples:
        # BM25 search for IRS code
        evidence-layer search "1245" --doc-id IRS_PUB946_2024 --method bm25
        
        # Semantic search
        evidence-layer search "depreciation for business equipment" --doc-id IRS_PUB946_2024 --method vector
        
        # Hybrid search (default)
        evidence-layer search "tangible personal property" --doc-id IRS_PUB946_2024
    """
    from ..src.retrieval import bm25_search, hybrid_search, vector_search
    
    # Select search function
    search_fn = {
        "bm25": bm25_search,
        "vector": vector_search,
        "hybrid": hybrid_search,
    }.get(method)
    
    if search_fn is None:
        rprint(f"[red]Invalid method: {method}. Must be bm25, vector, or hybrid[/red]")
        raise typer.Exit(1)
    
    rprint(f"\n🔍 Searching: [cyan]{query}[/cyan]")
    rprint(f"   Method: {method}, Doc: {doc_id}")
    
    results = search_fn(
        doc_id=doc_id,
        query=query,
        top_k=top_k,
        corpus=corpus,
        study_id=study_id,
    )
    
    if not results:
        rprint("[yellow]No results found[/yellow]")
        return
    
    rprint(f"\n[green]Found {len(results)} results:[/green]\n")
    
    for i, result in enumerate(results, 1):
        score = result.get("score", 0)
        chunk_type = result.get("type", "text")
        page_span = result.get("page_span", (0, 0))
        section = result.get("section_path", [])
        
        rprint(f"[bold]{i}.[/bold] Score: {score:.4f} | Pages: {page_span[0]}-{page_span[1]} | Type: {chunk_type}")
        
        if section:
            rprint(f"   Section: {' > '.join(section)}")
        
        if show_text:
            text = result.get("text", "")[:500]
            if len(result.get("text", "")) > 500:
                text += "..."
            rprint(f"   [dim]{text}[/dim]")
        
        # Show table if present
        if "table" in result:
            table_data = result["table"]
            rprint(f"   [yellow]📊 Table: {table_data.get('caption', 'Untitled')}[/yellow]")
            rprint(f"      Columns: {', '.join(table_data.get('headers', []))}")
            rprint(f"      Rows: {len(table_data.get('rows', []))}")
        
        rprint()


@app.command()
def info(
    doc_id: str = typer.Argument(..., help="Document ID"),
    corpus: str = typer.Option("reference", "--corpus", "-c", help="Corpus: reference or study"),
    study_id: Optional[str] = typer.Option(None, "--study-id", "-s", help="Study ID (for study corpus)"),
):
    """
    Show document info and statistics.
    """
    from ..src.manifest import Corpus, get_data_dir, load_manifest
    
    corpus_enum = Corpus(corpus)
    manifest = load_manifest(doc_id, corpus_enum, study_id)
    
    if manifest is None:
        rprint(f"[red]Document not found: {doc_id}[/red]")
        raise typer.Exit(1)
    
    data_dir = get_data_dir(corpus_enum, study_id)
    
    rprint(f"\n[bold]Document: {doc_id}[/bold]")
    rprint(f"  Corpus: {manifest.corpus.value}")
    rprint(f"  Type: {manifest.doc_type.value}")
    rprint(f"  Original: {manifest.original_filename}")
    rprint(f"  Pages: {manifest.page_count}")
    rprint(f"  Size: {manifest.file_size_bytes:,} bytes")
    
    if manifest.version_label:
        rprint(f"  Version: {manifest.version_label}")
    if manifest.study_id:
        rprint(f"  Study: {manifest.study_id}")
    
    rprint(f"  Registered: {manifest.registered_at}")
    rprint(f"  Processed: {manifest.is_processed}")
    
    # Check artifacts
    rprint("\n[bold]Artifacts:[/bold]")
    
    artifacts = [
        ("layout", f"{doc_id}.elements.jsonl"),
        ("structured", f"{doc_id}.tables.jsonl"),
        ("retrieval", f"{doc_id}.chunks.jsonl"),
        ("indexes/bm25", f"{doc_id}.bm25.pkl"),
        ("indexes/vector", f"{doc_id}.faiss"),
    ]
    
    for subdir, filename in artifacts:
        path = data_dir / subdir / filename
        if path.exists():
            size = path.stat().st_size
            rprint(f"  ✓ {subdir}/{filename} ({size:,} bytes)")
        else:
            rprint(f"  ✗ {subdir}/{filename} [dim](not found)[/dim]")


@app.command()
def list_docs(
    corpus: str = typer.Option("reference", "--corpus", "-c", help="Corpus: reference or study"),
    study_id: Optional[str] = typer.Option(None, "--study-id", "-s", help="Study ID (for study corpus)"),
):
    """
    List all documents in a corpus.
    """
    import json
    
    from ..src.manifest import Corpus, get_data_dir
    
    corpus_enum = Corpus(corpus)
    
    try:
        data_dir = get_data_dir(corpus_enum, study_id)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    
    manifest_path = data_dir / "manifest.json"
    
    if not manifest_path.exists():
        rprint("[yellow]No documents found[/yellow]")
        return
    
    with open(manifest_path) as f:
        manifests = json.load(f)
    
    if not manifests:
        rprint("[yellow]No documents found[/yellow]")
        return
    
    table = RichTable(title=f"Documents in {corpus} corpus")
    table.add_column("Doc ID", style="cyan")
    table.add_column("Type")
    table.add_column("Pages", justify="right")
    table.add_column("Processed")
    table.add_column("Version")
    
    for m in manifests:
        table.add_row(
            m["doc_id"],
            m["doc_type"],
            str(m["page_count"]),
            "✓" if m.get("is_processed") else "✗",
            m.get("version_label", "-"),
        )
    
    console.print(table)


if __name__ == "__main__":
    app()

