"""P2 candidate ranking only. No automatic data mapping or transformations.

The four *business* canonical columns can be ranked. Technical provenance fields
are generated at ingestion and deliberately excluded from matching. Source metadata
contains observable author definitions and never includes reference/gold answers.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"


def normalize_label(name: str) -> str:
    """Normalise separators and camel case for a transparent name-only baseline."""
    if not isinstance(name, str):
        raise TypeError("Column name must be text")
    words = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)
    return " ".join(re.findall(r"[a-z0-9]+", words.lower()))


def _take_top(scores: Mapping[str, float], top_k: int) -> list[dict[str, Any]]:
    if not 1 <= top_k <= len(scores):
        raise ValueError("top_k must be between 1 and the number of business targets")
    if not all(math.isfinite(float(value)) for value in scores.values()):
        raise ValueError("Non-finite similarity score; refusing candidate rankings")
    return [
        {"canonical_field": name, "similarity": round(float(score), 6)}
        for name, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:top_k]
    ]


def lexical_candidates(
    source_name: str, canonical_fields: Mapping[str, Any], top_k: int = 4
) -> list[dict[str, Any]]:
    """RapidFuzz WRatio of normalized names; similarity range is 0..1, NOT probability."""
    from rapidfuzz import fuzz

    normalized_source = normalize_label(source_name)
    if not normalized_source:
        raise ValueError("Source column has an empty normalized name")
    scores = {
        canonical: fuzz.WRatio(normalized_source, normalize_label(canonical)) / 100.0
        for canonical in canonical_fields
    }
    return _take_top(scores, top_k)


def _definition(name: str, attributes: Mapping[str, Any]) -> str:
    """The source's own description, not its hidden canonical evaluation label."""
    description = attributes.get("description", "")
    if not isinstance(description, str):
        raise ValueError(f"Description for {name} must be text")
    return f"Field name: {normalize_label(name)}. Meaning: {description.strip()}"


def semantic_candidates(
    source_columns: Mapping[str, Mapping[str, Any]],
    canonical_fields: Mapping[str, Mapping[str, Any]],
    model: Any,
    top_k: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """Encode descriptions once per source and rank by cosine, not confidence.

    Accepts an encoder with SentenceTransformer.encode interface, which makes
    tests possible with synthetic vectors and no model download.
    """
    import numpy as np

    if not canonical_fields or not source_columns:
        raise ValueError("Nonempty source and canonical fields are required")
    names = list(source_columns)
    targets = list(canonical_fields)
    texts = ([_definition(name, source_columns[name]) for name in names]
             + [_definition(name, canonical_fields[name]) for name in targets])
    vectors = np.asarray(
        model.encode(texts, normalize_embeddings=True, convert_to_numpy=True,
                     show_progress_bar=False), dtype=np.float64,
    )
    if vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] < 1:
        raise ValueError("Unexpected embedding array shape")
    if not np.isfinite(vectors).all():
        raise ValueError("Embedding contained NaN or infinity")
    lengths = np.linalg.norm(vectors, axis=1)
    if (lengths <= 0).any():
        raise ValueError("Embedding contained a zero-length vector")
    vectors = vectors / lengths[:, None]
    similarities = vectors[:len(names)] @ vectors[len(names):].T
    return {
        source_name: _take_top(
            {target: float(similarities[i, j]) for j, target in enumerate(targets)},
            top_k,
        )
        for i, source_name in enumerate(names)
    }


def read_demo_contract(config_path: Path) -> tuple[dict, dict, dict]:
    """Read ONLY runtime config, canonical schema and source metadata, never gold data."""
    config_path = Path(config_path).resolve()
    root = config_path.parent.parent

    def _read(relative_path: str) -> dict:
        location = (root / relative_path).resolve()
        if not location.is_relative_to(root):
            raise ValueError("Config reference is outside the project root")
        if "evaluation" in location.relative_to(root).parts:
            raise ValueError("Evaluation labels are not runtime matching inputs")
        with location.open(encoding="utf-8") as file:
            return json.load(file)

    with config_path.open(encoding="utf-8") as file:
        demo = json.load(file)
    if demo.get("synthetic_only") is not True:
        raise ValueError("Phase 2 preview is limited to the synthetic demo")
    schema = _read(demo["canonical_schema"])
    source = _read(demo["source_metadata"])
    if not isinstance(schema.get("business_fields"), dict) or not isinstance(source.get("systems"), dict):
        raise ValueError("Invalid canonical/source metadata document")
    return demo, schema, source


def preview_candidates(
    config_path: Path, method: str = "both", *, offline: bool = False,
    model: Any = None,
) -> dict[str, Any]:
    """Return reproducible candidate rankings only, never ACCEPT/REVIEW/REJECT.

    No data rows, gold labels, evaluation or transformations are read or written.
    """
    if method not in {"lexical", "semantic", "both"}:
        raise ValueError("method must be lexical, semantic or both")
    demo, schema, sources = read_demo_contract(config_path)
    fields = schema["business_fields"]
    if set(fields) != {"incident_id", "region", "incident_status", "resolution_minutes"}:
        raise ValueError("Unexpected business schema; provenance may not be ranked")
    top_k = demo.get("matcher", {}).get("top_k", len(fields))
    if type(top_k) is not int or not 1 <= top_k <= len(fields):
        raise ValueError("Invalid top_k; expecting 1..4")

    if method in {"semantic", "both"} and model is None:
        # SentenceTransformer is imported lazily so lexical-only mode remains fast.
        if offline:
            import os
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from sentence_transformers import SentenceTransformer
        configured = demo.get("matcher", {}).get("semantic")
        if configured != MODEL_ID:
            raise ValueError("Model ID differs from approved Phase 0 model")
        model = SentenceTransformer(MODEL_ID, device="cpu", local_files_only=offline)

    all_results: list[dict[str, Any]] = []
    for system, details in sorted(sources["systems"].items()):
        columns = details["columns"]
        semantic = (
            semantic_candidates(columns, fields, model, top_k=top_k)
            if method in {"semantic", "both"} else {}
        )
        for name in sorted(columns):
            all_results.append({
                "source_system": system,
                "source_column": name,
                "lexical_candidates": (
                    lexical_candidates(name, fields, top_k=top_k)
                    if method in {"lexical", "both"} else None
                ),
                "semantic_candidates": semantic.get(name) if method in {"semantic", "both"} else None,
                "mapping_decision": "NOT_EVALUATED",
                "validation_required": True,
            })
    return {
        "stage": "P2_CANDIDATE_RANKING_ONLY",
        "methods": method,
        "semantic_model_id": MODEL_ID if method in {"semantic", "both"} else None,
        "semantic_model_local_files_only": offline if method in {"semantic", "both"} else None,
        "similarity_is_probability": False,
        "mapping_validation_performed": False,
        "data_transformed": False,
        "gold_labels_accessed": False,
        "business_target_count": len(fields),
        "source_column_count": len(all_results),
        "results": all_results,
    }
