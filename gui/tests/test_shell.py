"""P-025 Streamlit shell checks, run ONLY in the GUI environment.

These are GUI-specific pytest checks and are not part of the original 105
core unittest tests. They require Streamlit installed in .venv-gui.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def test_initial_app_shows_real_read_only_synthetic_sources():
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    assert len(app.exception) == 0
    assert app.title[0].value == "01 · Data Sources"
    assert any("SYNTHETIC DEMO" in item.value for item in app.info)
    assert any("Session stage: **IDLE**" in item.value for item in app.info)
    assert any("No candidate report in this browser session" in item.value for item in app.caption)
    assert not any("GUI V1 in development" in item.value for item in app.caption)
    assert not any("Engine integration is not yet connected." in item.value for item in app.info)
    assert [str(metric.value) for metric in app.metric] == ["3", "180", "22"]
    assert len(app.button) == 0
    assert len(app.file_uploader) == 0


def test_results_page_requires_validated_session_before_reconciliation():
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.switch_page("pages/results.py").run()
    assert len(app.exception) == 0
    assert app.title[0].value == "04 · Results & Export"
    assert any("No current independently validated candidate report" in item.value for item in app.info)
    assert len(app.button) == 0
    assert len(app.file_uploader) == 0


def test_matching_page_idle_has_explicit_run_action_without_inference(monkeypatch):
    import gui.adapter as adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("Model must not load merely by switching to matching")

    monkeypatch.setattr(adapter, "rank_candidates", forbidden)
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.switch_page("pages/matching.py").run()
    assert len(app.exception) == 0
    assert app.title[0].value == "02 · AI Mapping"
    assert len(app.button) == 1
    assert app.button[0].label == "Run matching (offline)"
    assert len(app.file_uploader) == 0
    assert any("No candidate report" in item.value for item in app.info)


def test_validation_page_without_current_matching_report_is_read_only():
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.switch_page("pages/validation.py").run()
    assert len(app.exception) == 0
    assert app.title[0].value == "03 · Validation"
    assert any("No current candidate report" in item.value for item in app.info)
    assert len(app.button) == 0
    assert len(app.file_uploader) == 0


def test_native_streamlit_table_download_is_disabled_before_p031():
    """Streamlit st.dataframe has its own toolbar CSV button; prevent bypass."""
    import tomllib

    config = ENTRYPOINT.parents[1] / ".streamlit" / "config.toml"
    assert config.is_file()
    with config.open("rb") as fp:
        settings = tomllib.load(fp)
    assert settings["client"]["disableDataExport"] is True
    assert settings["server"]["address"] == "127.0.0.1"
