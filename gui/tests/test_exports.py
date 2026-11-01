"""P-031 synthetic-only audited exports and Streamlit action-gate tests.

These tests use an injected offline fake encoder and the original Python
validator/reconciler. They never rerun the spent holdout or download real data.
No assertion is a substitute for Windows/Chrome acceptance.
"""

from __future__ import annotations

import csv
import io
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from gui import adapter, export_view, matching_state

ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"
FINGERPRINT = "a" * 64


class FakeEncoder:
    def encode(self, texts, *, normalize_embeddings, convert_to_numpy, show_progress_bar):
        assert normalize_embeddings and convert_to_numpy and not show_progress_bar
        return np.eye(len(texts), dtype=np.float64)


@pytest.fixture(scope="module")
def bundle():
    ranked = adapter.rank_candidates(model=FakeEncoder())
    validation = adapter.inspect_decisions(ranked)
    result = adapter.reconcile_candidates(ranked)
    return ranked, validation, result


def ready_app(bundle):
    ranked, validation, result = bundle
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    fingerprint = matching_state.input_fingerprint()
    app.session_state[matching_state.REPORT_KEY] = deepcopy(ranked)
    app.session_state[matching_state.FINGERPRINT_KEY] = fingerprint
    app.session_state["sb_validation_report"] = deepcopy(validation)
    app.session_state["sb_validation_fingerprint"] = fingerprint
    app.switch_page("pages/results.py").run()
    return app


def test_real_four_file_export_and_round_trip(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = export_view.prepare(result, validation, FINGERPRINT)
    downloaded = export_view.download_payloads(manifest, result, FINGERPRINT)

    folder = tmp_path / "output" / "gui_runs" / manifest["run_id"]
    assert folder.is_dir()
    assert {p.name for p in folder.iterdir()} == set(adapter.AUDIT_FILENAMES)
    assert set(downloaded) == set(adapter.AUDIT_FILENAMES)
    assert manifest["data_rows"] == len(result["rows"])
    assert manifest["reason_events"] == len(result["issues"])
    assert all(downloaded[name] == (folder / name).read_bytes() for name in downloaded)
    assert all(sha256(downloaded[name]).hexdigest() == manifest["sha256"][name] for name in downloaded)

    with io.StringIO(downloaded["standardised.csv"].decode("utf-8-sig"), newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(result["rows"])
    assert rows == result["rows"]
    with io.StringIO(downloaded["rejected_rows.csv"].decode("utf-8-sig"), newline="") as fh:
        events = list(csv.DictReader(fh))
    assert events == result["issues"]


def test_export_never_overwrites_old_folder(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    history = tmp_path / "output" / "gui_runs" / "gui_previous_run"
    history.mkdir(parents=True)
    original = history / "keep.txt"
    original.write_text("untouched", encoding="utf-8")
    first = export_view.prepare(result, validation, FINGERPRINT)
    second = export_view.prepare(result, validation, FINGERPRINT)
    assert first["run_id"] != second["run_id"]
    assert original.read_text(encoding="utf-8") == "untouched"
    assert (tmp_path / "output" / "gui_runs" / first["run_id"]).is_dir()
    assert (tmp_path / "output" / "gui_runs" / second["run_id"]).is_dir()


def test_tampered_file_revokes_downloads(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = export_view.prepare(result, validation, FINGERPRINT)
    target = tmp_path / "output" / "gui_runs" / manifest["run_id"] / "standardised.csv"
    target.write_bytes(target.read_bytes() + b"\nTAMPER")
    with pytest.raises(ValueError, match="integrity mismatch"):
        export_view.download_payloads(manifest, result, FINGERPRINT)


def test_stale_input_and_result_revoke_downloads(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = export_view.prepare(result, validation, FINGERPRINT)
    with pytest.raises(ValueError, match="Stale"):
        export_view.download_payloads(manifest, result, "b" * 64)
    # Fake-encoder fixtures may have zero approved rows. Tamper with a
    # guaranteed backend report field instead, to exercise stale-result
    # fingerprint enforcement independently of accepted-row count.
    changed = deepcopy(result)
    changed["mapping_report"]["limitations"] = "Tampered after export"
    with pytest.raises(ValueError, match="Stale"):
        export_view.download_payloads(manifest, changed, FINGERPRINT)
    assert set(export_view.download_payloads(manifest, result, FINGERPRINT)) == set(adapter.AUDIT_FILENAMES)


def test_invalid_validation_is_blocked_before_export(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    altered = deepcopy(validation)
    altered["decisions"][0]["decision"] = "REVIEW"
    with pytest.raises(ValueError, match="current independent validation"):
        export_view.prepare(result, altered, FINGERPRINT)
    assert not (tmp_path / "output").exists()


def test_missing_file_revokes_downloads(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = export_view.prepare(result, validation, FINGERPRINT)
    path = tmp_path / "output" / "gui_runs" / manifest["run_id"] / "mapping_report.json"
    path.unlink()
    with pytest.raises(ValueError, match="missing or contains unexpected"):
        export_view.download_payloads(manifest, result, FINGERPRINT)



def test_unexpected_extra_audit_file_revokes_downloads_without_altering_reports(
    bundle, tmp_path, monkeypatch
):
    """One extra artifact is not an approved four-file report."""
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = export_view.prepare(result, validation, FINGERPRINT)
    folder = tmp_path / "output" / "gui_runs" / manifest["run_id"]
    preserved = {
        name: (folder / name).read_bytes() for name in adapter.AUDIT_FILENAMES
    }
    unexpected = folder / "unexpected-extra.txt"
    unexpected.write_text("not an audited report", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected files"):
        export_view.download_payloads(manifest, result, FINGERPRINT)

    assert unexpected.read_text(encoding="utf-8") == "not an audited report"
    assert all((folder / name).read_bytes() == data for name, data in preserved.items())


@pytest.mark.parametrize(
    "forged_run_id",
    ["../outside", "../../outside", "/absolute/path", "gui_safe/../outside"],
)
def test_forged_download_manifest_run_id_cannot_escape_output(
    bundle, tmp_path, monkeypatch, forged_run_id
):
    """Downloads must reject untrusted manifest paths, not only writer paths."""
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)
    manifest = export_view.prepare(result, validation, FINGERPRINT)
    legitimate_folder = tmp_path / "output" / "gui_runs" / manifest["run_id"]
    legitimate = {
        name: (legitimate_folder / name).read_bytes()
        for name in adapter.AUDIT_FILENAMES
    }
    external = tmp_path / "outside"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("unmodified", encoding="utf-8")

    compromised = deepcopy(manifest)
    compromised["run_id"] = forged_run_id
    with pytest.raises(ValueError, match="Untrusted audited run ID"):
        export_view.download_payloads(compromised, result, FINGERPRINT)

    assert sentinel.read_text(encoding="utf-8") == "unmodified"
    assert all(
        (legitimate_folder / name).read_bytes() == data
        for name, data in legitimate.items()
    )


def test_failed_writer_does_not_publish_a_complete_manifest(bundle, tmp_path, monkeypatch):
    _ranked, validation, result = bundle
    monkeypatch.setattr(adapter, "PROJECT_ROOT", tmp_path)

    def interrupted_writer(_root, destination, _result):
        (destination / "standardised.csv").write_text("incomplete\n", encoding="utf-8")
        raise OSError("simulated disk-write failure")

    monkeypatch.setattr(adapter, "write_demo_output", interrupted_writer)
    with pytest.raises(OSError, match="simulated disk-write failure"):
        export_view.prepare(result, validation, FINGERPRINT)
    run_dirs = list((tmp_path / "output" / "gui_runs").iterdir())
    assert len(run_dirs) == 1
    assert set(p.name for p in run_dirs[0].iterdir()) == {"standardised.csv"}


def test_no_export_on_page_load_or_reconciliation(bundle, monkeypatch):
    _ranked, _validation, result = bundle
    calls = []

    def no_export(*args, **kwargs):
        calls.append("export")
        raise AssertionError("Export must require its separate Prepare action")

    monkeypatch.setattr(export_view, "prepare", no_export)
    monkeypatch.setattr(adapter, "reconcile_candidates", lambda _c: deepcopy(result))
    app = ready_app(bundle)
    assert len(app.exception) == 0
    assert calls == []
    app.button[0].click().run()
    assert len(app.exception) == 0
    assert len(app.button) == 2
    assert app.button[1].label == "Prepare audited reports (write once)"
    assert len(app.download_button) == 0
    app.selectbox[0].set_value("system_b").run()
    app.selectbox[1].set_value("QUARANTINED").run()
    assert calls == []
    assert len(app.download_button) == 0


def test_explicit_prepare_shows_exactly_four_downloads_once(bundle, monkeypatch):
    _ranked, _validation, result = bundle
    writes = []

    def fake_prepare(_result, _validation, fingerprint):
        writes.append(fingerprint)
        return {
            "run_id": "gui_mock_run_123",
            "data_rows": len(result["rows"]),
            "reason_events": len(result["issues"]),
        }

    monkeypatch.setattr(adapter, "reconcile_candidates", lambda _: deepcopy(result))
    monkeypatch.setattr(export_view, "prepare", fake_prepare)
    monkeypatch.setattr(
        export_view, "download_payloads",
        lambda _manifest, _result, _fingerprint: {
            name: name.encode("utf-8") for name in adapter.AUDIT_FILENAMES
        },
    )
    app = ready_app(bundle)
    app.button[0].click().run()
    assert app.button[1].disabled is False
    app.button[1].click().run()
    assert len(app.exception) == 0
    assert len(writes) == 1
    # Regression: the first successful Prepare must immediately rerender the
    # button as disabled, without waiting for a filter/widget interaction.
    assert app.button[1].disabled is True
    assert len(app.download_button) == 4
    assert [x.label for x in app.download_button] == [
        "Download " + name for name in adapter.AUDIT_FILENAMES
    ]
    app.selectbox[0].set_value("system_a").run()
    app.selectbox[1].set_value("HELD_FOR_REVIEW").run()
    assert len(writes) == 1
    assert len(app.download_button) == 4
    app.button[0].click().run()
    assert len(app.download_button) == 0
    assert app.button[1].disabled is False


def test_export_failure_shows_no_downloads(bundle, monkeypatch):
    _ranked, _validation, result = bundle
    monkeypatch.setattr(adapter, "reconcile_candidates", lambda _: deepcopy(result))

    def fail(*_args):
        raise OSError("intentional export failure")

    monkeypatch.setattr(export_view, "prepare", fail)
    app = ready_app(bundle)
    app.button[0].click().run()
    app.button[1].click().run()
    assert len(app.exception) == 0
    assert len(app.download_button) == 0
    assert any("Audited export was blocked" in e.value for e in app.error)


def test_fingerprint_change_revokes_prepared_links(bundle, monkeypatch):
    _ranked, _validation, result = bundle
    current = [FINGERPRINT]
    monkeypatch.setattr(matching_state, "input_fingerprint", lambda: current[0])
    monkeypatch.setattr(adapter, "reconcile_candidates", lambda _: deepcopy(result))
    monkeypatch.setattr(
        export_view, "prepare",
        lambda _result, _validation, fingerprint: {
            "run_id": "gui_mock_run_123",
            "data_rows": len(result["rows"]),
            "reason_events": len(result["issues"]),
        },
    )
    monkeypatch.setattr(
        export_view, "download_payloads",
        lambda _manifest, _result, _fp: {name: b"ok" for name in adapter.AUDIT_FILENAMES},
    )
    app = ready_app(bundle)
    app.button[0].click().run()
    app.button[1].click().run()
    assert len(app.download_button) == 4
    current[0] = "b" * 64
    app.run()
    assert len(app.exception) == 0
    assert len(app.download_button) == 0
    assert export_view.MANIFEST_KEY not in app.session_state
