"""SchemaBridge UX2 light shared shell and protected four-stage router.

The Sources page reads approved fictional data via the tested adapter.
AI Mapping performs candidate-only ranking only after an explicit Run click.
Validation exposes independent decisions; Results permits separately gated reconciliation and audited export.
No model loads or report writes occur in this entrypoint.
Run from repository root with python -m streamlit run gui/streamlit_app.py.
"""

from pathlib import Path

import streamlit as st

from gui.workbench_shell import render_shell


PAGES = Path(__file__).resolve().parent / "pages"

st.set_page_config(
    page_title="SchemaBridge | Synthetic demo",
    page_icon=":material/compare_arrows:",
    layout="wide",
    initial_sidebar_state="expanded",
)

page = st.navigation(
    [
        st.Page(PAGES / "sources.py", title="01  Data Sources", icon=":material/database:", default=True),
        st.Page(PAGES / "matching.py", title="02  AI Mapping", icon=":material/account_tree:"),
        st.Page(PAGES / "validation.py", title="03  Validation", icon=":material/verified_user:"),
        st.Page(PAGES / "results.py", title="04  Results & Export", icon=":material/file_download:"),
    ],
    position="sidebar",
)

# Status derives from current session artifacts, never the selected page.
# The shared shell cannot run matching, validation, reconciliation or export.
render_shell(st.session_state)
page.run()
