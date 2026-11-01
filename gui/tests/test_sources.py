"""P-027 live synthetic Sources page tests.

These are headless Streamlit UI checks using the original fixture data only.
No model inference, user upload or export should occur during navigation.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def test_real_fixture_counts_are_visible_on_initial_source_page():
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    assert len(app.exception) == 0
    assert app.title[0].value == "01 · Data Sources"
    assert [str(x.value) for x in app.metric] == ["3", "180", "22"]
    assert any("Displaying 5 of 60 source records" in x.value for x in app.caption)
    assert any("cover all 60 records" in x.value for x in app.caption)
    assert len(app.file_uploader) == 0
    assert len(app.button) == 0


@pytest.mark.parametrize(
    ("system", "name", "filename", "field_count"),
    [
        ("system_a", "ServiceDesk Alpha", "source_a.csv", 7),
        ("system_b", "SupportTrack Beta", "source_b.csv", 7),
        ("system_c", "IncidentCloud Gamma", "source_c.csv", 8),
    ],
)
def test_source_filter_shows_real_source_not_hardcoded_metrics(system, name, filename, field_count):
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.selectbox[0].set_value(system).run()
    assert len(app.exception) == 0
    assert any(name in x.value for x in app.subheader)
    assert any(filename in x.value and str(field_count) + " declared columns" in x.value
               for x in app.caption)
    assert [str(x.value) for x in app.metric] == ["3", "180", "22"]
    assert any("Displaying 5 of 60" in x.value for x in app.caption)
    assert len(app.button) == 0
    assert len(app.file_uploader) == 0


def test_browsing_sources_never_invokes_matching_reconciliation_or_exports(monkeypatch):
    import gui.adapter as adapter

    def no_action(*args, **kwargs):
        raise AssertionError("Source page attempted an engine action")

    monkeypatch.setattr(adapter, "rank_candidates", no_action)
    monkeypatch.setattr(adapter, "inspect_decisions", no_action)
    monkeypatch.setattr(adapter, "reconcile_candidates", no_action)
    monkeypatch.setattr(adapter, "prepare_audit_export", no_action)
    app = AppTest.from_file(str(ENTRYPOINT)).run()
    app.selectbox[0].set_value("system_c").run()
    assert len(app.exception) == 0
    assert len(app.button) == 0
