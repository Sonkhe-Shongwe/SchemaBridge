"""Safe, source-scoped CSV ingestion for fictional Phase 3 demo records."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


def _confined_source_path(root: Path, relative: str) -> Path:
    """Only allow input files in the synthetic fixture folder."""
    root = root.resolve()
    data_dir = (root / "data" / "synthetic").resolve()
    # The synthetic directory itself must remain inside this project. Without
    # this check a redirected data/synthetic symlink could make an external
    # CSV look confined to the synthetic directory when called via the core.
    if not data_dir.is_relative_to(root):
        raise ValueError("Unsafe synthetic source directory")
    path = (root / relative).resolve()
    if not path.is_relative_to(data_dir) or path.suffix.lower() != ".csv":
        raise ValueError(f"Unsafe input path: {relative}")
    return path


def read_source(
    root: Path,
    system: str,
    source_config: Mapping[str, Any],
    expected_paths: set[str],
) -> tuple[list[dict], dict]:
    """Preserve all CSV cell values as text, including IDs such as 00125.

    The generated positional provenance field is unique even for duplicate
    incident identifiers. A duplicate is flagged, never removed.
    """
    relative = source_config.get("csv")
    if not isinstance(relative, str) or relative not in expected_paths:
        raise ValueError(f"Unapproved synthetic source path for {system}")
    path = _confined_source_path(root, relative)
    declared = source_config.get("columns")
    if not isinstance(declared, dict) or not declared:
        raise ValueError(f"Missing declared source columns for {system}")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError(f"Missing or repeated column names for {system}")
        if reader.fieldnames != list(declared):
            raise ValueError(f"Source header disagrees with metadata: {system}")
        for i, raw in enumerate(reader, start=1):
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"Malformed or extra CSV cell at {system} row {i}")
            rows.append({
                "source_system": system,
                "source_record_id": f"{path.name}:row:{i}",
                "csv_row": i,
                "raw": dict(raw),
            })
    return rows, {"system": system, "file": relative, "rows_read": len(rows),
                  "fields": list(declared)}


def duplicate_identifiers(rows: list[dict], source_column: str) -> set[str]:
    """Return repeated nonempty identifiers, scoped to one source only."""
    nonempty = [row["raw"][source_column].strip() for row in rows
                if row["raw"][source_column].strip()]
    counts = Counter(nonempty)
    return {value for value, count in counts.items() if count > 1}
