"""Regression checks for the SchemaBridge interface configuration.

These source-level checks cover visual tokens and selected UI constraints.
Browser behaviour and assistive-technology support need separate testing.
"""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / ".streamlit/config.toml"
VISUAL = ROOT / "design/probes/ux2_portfolio_visual_refresh.css"
FOCUS = ROOT / "design/probes/ux2_light_focus_candidate.css"
SHELL = ROOT / "gui/workbench_shell.py"
RESULTS = ROOT / "gui/pages/results.py"
ROUTER = ROOT / "gui/streamlit_app.py"
MATCHING = ROOT / "gui/pages/matching.py"
VALIDATION = ROOT / "gui/pages/validation.py"
WORDMARK = ROOT / "design/brand/schemabridge-wordmark.svg"
MARK = ROOT / "design/brand/schemabridge-mark.svg"


def _luminance(hex_value: str) -> float:
    channels = [int(hex_value[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        for v in channels
    ]
    return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_local_safety_settings_are_unchanged_by_candidate_theme():
    conf = tomllib.loads(THEME.read_text(encoding="utf-8"))
    assert conf["server"]["address"] == "127.0.0.1"
    assert conf["client"]["disableDataExport"] is True
    assert conf["theme"]["base"] == "light"
    assert _contrast(conf["theme"]["textColor"], conf["theme"]["backgroundColor"]) >= 4.5
    assert _contrast(conf["theme"]["primaryColor"], conf["theme"]["backgroundColor"]) >= 4.5
    sidebar = conf["theme"]["sidebar"]
    assert _contrast(sidebar["textColor"], sidebar["backgroundColor"]) >= 4.5
    assert _contrast("#65D5D1", sidebar["backgroundColor"]) >= 3.0


def test_visual_refresh_uses_native_controls_not_client_script():
    css = VISUAL.read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")
    focus = FOCUS.read_text(encoding="utf-8")
    assert "design/probes/ux2_portfolio_visual_refresh.css" in shell
    assert "design/probes/ux2_light_focus_candidate.css" in shell
    assert 'st.html("<style>\\n"' in shell
    assert "outline: 3px solid #1554B5 !important" in focus
    assert "[data-testid=\"stSidebar\"] a:focus-visible" in css
    assert "outline-color: #65D5D1 !important" in css
    assert ".st-emotion-cache" not in css
    assert "<script" not in css.lower()
    assert not re.search(r"https?://", css)
    ast.parse(shell)


def test_record_outcome_view_keeps_original_backend_and_writer_guard():
    source = RESULTS.read_text(encoding="utf-8")
    ast.parse(source)
    assert "summary[\"counts\"]" in source
    assert "sb-outcome-track" in source
    assert "st.progress(count / summary[\"input_records\"])" in source
    assert 'st.button(\n    "Prepare audited reports (write once)"' in source
    assert "export_view.prepare(result, validation, fingerprint)" in source
    assert "write_demo_output(" not in source
    assert "st.file_uploader(" not in source


def test_wordmark_is_native_above_sidebar_navigation_and_not_duplicated():
    router = ROUTER.read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")
    ast.parse(router)
    assert "st.logo(" in router
    assert router.index("st.logo(") < router.index("st.navigation(")
    assert "schemabridge-wordmark.svg" in router
    assert "schemabridge-mark.svg" in router
    assert router.count("st.navigation(") == 1
    assert '{"WORKSPACE": [' in router
    for label in (
        "01  Data Sources", "02  AI Mapping",
        "03  Validation", "04  Results & Export",
    ):
        assert label in router
    assert "st.sidebar.image(" not in shell
    assert "st.sidebar.markdown(\"### SchemaBridge\")" not in shell
    assert "Session stage: **" in shell
    assert WORDMARK.is_file()
    assert MARK.is_file()


def test_backend_derived_method_cards_and_validation_counts_are_presentation_only():
    match = MATCHING.read_text(encoding="utf-8")
    validate = VALIDATION.read_text(encoding="utf-8")
    ast.parse(match)
    ast.parse(validate)
    for snippet in (
        'source_row["lexical_top"]', 'source_row["semantic_top"]',
        'source_row["lexical_similarity"]',
        'source_row["semantic_similarity"]',
        "sb-method-compare", "NOT_EVALUATED",
    ):
        assert snippet in match
    assert 'outcome["counts"][_status]' in validate
    assert "sb-outcome-track" in validate
    assert "adapter.inspect_decisions(report)" in validate
    assert "write_demo_output(" not in match + validate
    assert "st.file_uploader(" not in match + validate
