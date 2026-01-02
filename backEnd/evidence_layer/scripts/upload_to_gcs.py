#!/usr/bin/env python3
"""
Upload local indexes to Google Cloud Storage for production deployment.

Usage:
    # Upload reference indexes
    python -m evidence_layer.scripts.upload_to_gcs my-bucket \\
        --prefix reference/indexes \\
        --data-dir data/reference/indexes

    # Upload specific document types
    python -m evidence_layer.scripts.upload_to_gcs my-bucket \\
        --prefix reference/indexes/bm25 \\
        --data-dir data/reference/indexes/bm25

    # Dry run to see what would be uploaded
    python -m evidence_layer.scripts.upload_to_gcs my-bucket --dry-run
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()
app = typer.Typer(help="Upload evidence layer indexes to GCS")


@app.command()
def upload(
    bucket_name: str = typer.Argument(..., help="GCS bucket name"),
    prefix: str = typer.Option(
        "indexes",
        "--prefix",
        "-p",
        help="Path prefix within bucket",
    ),
    data_dir: Path = typer.Option(
        Path("data/reference/indexes"),
        "--data-dir",
        "-d",
        help="Local data directory to upload",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be uploaded without uploading",
    ),
    include_pattern: Optional[str] = typer.Option(
        None,
        "--include",
        "-i",
        help="Only upload files matching this pattern (e.g., '*.bm25.pkl')",
    ),
):
    """Upload local indexes to GCS bucket."""
    from google.cloud import storage

    if not data_dir.exists():
        console.print(f"[red]Error: Data directory not found: {data_dir}[/red]")
        raise typer.Exit(1)

    # Collect files to upload
    if include_pattern:
        files = list(data_dir.rglob(include_pattern))
    else:
        files = [f for f in data_dir.rglob("*") if f.is_file()]

    if not files:
        console.print("[yellow]No files found to upload[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold]Found {len(files)} files to upload[/bold]")
    console.print(f"  Bucket: gs://{bucket_name}/{prefix}/")
    console.print(f"  Source: {data_dir}")
    console.print()

    if dry_run:
        console.print("[yellow]DRY RUN - No files will be uploaded[/yellow]")
        console.print()
        for f in files:
            relative_path = f.relative_to(data_dir)
            blob_name = f"{prefix}/{relative_path}"
            size_kb = f.stat().st_size / 1024
            console.print(f"  {relative_path} ({size_kb:.1f} KB) -> {blob_name}")
        return

    # Initialize GCS client
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
    except Exception as e:
        console.print(f"[red]Error connecting to GCS: {e}[/red]")
        raise typer.Exit(1)

    # Upload files with progress
    uploaded = 0
    failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Uploading...", total=len(files))

        for f in files:
            relative_path = f.relative_to(data_dir)
            blob_name = f"{prefix}/{relative_path}"

            progress.update(task, description=f"Uploading {relative_path}")

            try:
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(str(f))
                uploaded += 1
            except Exception as e:
                console.print(f"[red]Failed to upload {relative_path}: {e}[/red]")
                failed += 1

            progress.advance(task)

    # Summary
    console.print()
    console.print(f"[green]Uploaded: {uploaded} files[/green]")
    if failed:
        console.print(f"[red]Failed: {failed} files[/red]")


@app.command()
def list_bucket(
    bucket_name: str = typer.Argument(..., help="GCS bucket name"),
    prefix: str = typer.Option(
        "indexes",
        "--prefix",
        "-p",
        help="Path prefix to list",
    ),
):
    """List indexes in GCS bucket."""
    from google.cloud import storage

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)

        console.print(f"[bold]Contents of gs://{bucket_name}/{prefix}/[/bold]")
        console.print()

        count = 0
        total_size = 0

        for blob in blobs:
            size_kb = blob.size / 1024 if blob.size else 0
            total_size += blob.size or 0
            count += 1
            console.print(f"  {blob.name} ({size_kb:.1f} KB)")

        console.print()
        console.print(f"[bold]Total: {count} files, {total_size / 1024 / 1024:.2f} MB[/bold]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def verify(
    bucket_name: str = typer.Argument(..., help="GCS bucket name"),
    prefix: str = typer.Option(
        "indexes",
        "--prefix",
        "-p",
        help="Path prefix to verify",
    ),
    data_dir: Path = typer.Option(
        Path("data/reference/indexes"),
        "--data-dir",
        "-d",
        help="Local data directory to compare",
    ),
):
    """Verify GCS indexes match local files."""
    from google.cloud import storage

    if not data_dir.exists():
        console.print(f"[red]Error: Data directory not found: {data_dir}[/red]")
        raise typer.Exit(1)

    # Get local files
    local_files = {
        str(f.relative_to(data_dir)): f.stat().st_size
        for f in data_dir.rglob("*")
        if f.is_file()
    }

    # Get GCS files
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)

        gcs_files = {}
        for blob in blobs:
            # Remove prefix to get relative path
            rel_path = blob.name[len(prefix) + 1:] if blob.name.startswith(prefix + "/") else blob.name
            if rel_path:
                gcs_files[rel_path] = blob.size

    except Exception as e:
        console.print(f"[red]Error connecting to GCS: {e}[/red]")
        raise typer.Exit(1)

    # Compare
    missing_in_gcs = set(local_files.keys()) - set(gcs_files.keys())
    extra_in_gcs = set(gcs_files.keys()) - set(local_files.keys())
    size_mismatch = []

    for path in set(local_files.keys()) & set(gcs_files.keys()):
        if local_files[path] != gcs_files[path]:
            size_mismatch.append(path)

    # Report
    console.print(f"[bold]Verification Results[/bold]")
    console.print(f"  Local files: {len(local_files)}")
    console.print(f"  GCS files: {len(gcs_files)}")
    console.print()

    if missing_in_gcs:
        console.print(f"[yellow]Missing in GCS ({len(missing_in_gcs)}):[/yellow]")
        for path in sorted(missing_in_gcs)[:10]:
            console.print(f"  - {path}")
        if len(missing_in_gcs) > 10:
            console.print(f"  ... and {len(missing_in_gcs) - 10} more")

    if extra_in_gcs:
        console.print(f"[yellow]Extra in GCS ({len(extra_in_gcs)}):[/yellow]")
        for path in sorted(extra_in_gcs)[:10]:
            console.print(f"  + {path}")

    if size_mismatch:
        console.print(f"[yellow]Size mismatch ({len(size_mismatch)}):[/yellow]")
        for path in size_mismatch[:10]:
            console.print(f"  ~ {path}")

    if not missing_in_gcs and not extra_in_gcs and not size_mismatch:
        console.print("[green]All files match![/green]")
    else:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
