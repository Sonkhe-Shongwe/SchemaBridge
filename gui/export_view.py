"""P-031 session-bound presentation of the original four-file audited export.

This layer never builds CSV/JSON, scores labels or decides mappings. It checks
existing backend results, records hashes and serves only verified bytes from
the original writer's unique, ignored output directory.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from gui import adapter, reconciliation_view

MANIFEST_KEY = "sb_export_manifest"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FILE_BYTES = 8 * 1024 * 1024


def _digest(result: Mapping[str, Any]) -> str:
    raw = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _folder(run_id: str) -> Path:
    if not isinstance(run_id, str) or adapter._RUN_ID.fullmatch(run_id) is None:
        raise ValueError("Untrusted audited run ID")
    root = Path(adapter.PROJECT_ROOT).resolve()
    base = root / "output" / "gui_runs"
    if base.is_symlink() or not base.resolve().is_relative_to(root):
        raise ValueError("Unsafe audited output root")
    directory = base / run_id
    if directory.is_symlink() or directory.resolve().parent != base.resolve():
        raise ValueError("Unsafe audited run directory")
    return directory


def _read_artifacts(run_id: str) -> dict[str, bytes]:
    folder = _folder(run_id)
    if not folder.is_dir() or {p.name for p in folder.iterdir()} != set(adapter.AUDIT_FILENAMES):
        raise ValueError("Audited export is missing or contains unexpected files")
    payloads = {}
    for name in adapter.AUDIT_FILENAMES:
        path = folder / name
        if (path.is_symlink() or not path.is_file()
                or path.resolve().parent != folder.resolve()):
            raise ValueError("Unsafe audited output file")
        size = path.stat().st_size
        if not 0 < size <= _MAX_FILE_BYTES:
            raise ValueError("Missing or oversized audited output")
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError("Audited output file changed during read")
        payloads[name] = data
    return payloads


def prepare(result: Mapping[str, Any], validation: Mapping[str, Any], fingerprint: str) -> dict[str, Any]:
    """Explicit write once, only after full independent validation."""
    if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
        raise ValueError("Invalid approved input fingerprint")
    summary = reconciliation_view.summarize(result, validation)
    digest = _digest(result)
    original = adapter.prepare_audit_export(result)
    if original.get("evaluation_is_placeholder") is not True:
        raise ValueError("Unscored evaluation disclosure missing")
    if original.get("data_rows") != summary["standardisable_records"]:
        raise ValueError("Audited rows differ from reconciliation")
    if set(original.get("files", {})) != set(adapter.AUDIT_FILENAMES):
        raise ValueError("Audited report must contain four exact files")
    if Path(original["directory"]).resolve() != _folder(original["run_id"]).resolve():
        raise ValueError("Audited output folder mismatch")
    payloads = _read_artifacts(original["run_id"])
    if json.loads(payloads["mapping_report.json"]) != result["mapping_report"]:
        raise ValueError("Audited mapping report differs from backend")
    if json.loads(payloads["evaluation.json"]) != result["evaluation"]:
        raise ValueError("Audited evaluation placeholder differs from backend")
    return {
        "run_id": original["run_id"],
        "input_fingerprint": fingerprint,
        "reconciliation_digest": digest,
        "data_rows": summary["standardisable_records"],
        "reason_events": summary["reason_events"],
        "sha256": {name: hashlib.sha256(blob).hexdigest() for name, blob in payloads.items()},
    }


def download_payloads(
    manifest: Mapping[str, Any], result: Mapping[str, Any], fingerprint: str,
) -> dict[str, bytes]:
    """Read-only, fail-closed downloads for current unmodified session."""
    if (not isinstance(manifest, Mapping)
            or manifest.get("input_fingerprint") != fingerprint
            or not isinstance(fingerprint, str)
            or _SHA256.fullmatch(fingerprint) is None
            or manifest.get("reconciliation_digest") != _digest(result)
            or manifest.get("data_rows") != adapter._checked_counts(result)
            or manifest.get("reason_events") != len(result["issues"])):
        raise ValueError("Stale or invalid audited session")
    expected = manifest.get("sha256")
    if not isinstance(expected, Mapping) or set(expected) != set(adapter.AUDIT_FILENAMES):
        raise ValueError("Incomplete audited artifact hashes")
    payloads = _read_artifacts(manifest["run_id"])
    if any(not isinstance(expected[name], str)
           or not _SHA256.fullmatch(expected[name])
           or hashlib.sha256(payloads[name]).hexdigest() != expected[name]
           for name in adapter.AUDIT_FILENAMES):
        raise ValueError("Audited file integrity mismatch")
    if (json.loads(payloads["mapping_report.json"]) != result["mapping_report"]
            or json.loads(payloads["evaluation.json"]) != result["evaluation"]):
        raise ValueError("Audited report changed")
    return payloads
