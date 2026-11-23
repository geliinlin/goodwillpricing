"""Centralized stylesheet loading for the Goodwill Streamlit UI."""
from pathlib import Path
import streamlit as st

STYLE_DIR = Path(__file__).resolve().parent / "styles"
ALLOWED_STYLES = frozenset({
    "global.css", "navigation.css", "login.css",
    "dashboard.css", "guidelines.css", "pricing.css", "reports.css", "users.css", "configuration.css",
})


def load_styles(*names: str) -> None:
    """Load only bundled CSS files; CSS-only st.html calls add no visible widgets."""
    for name in names:
        if name not in ALLOWED_STYLES:
            raise ValueError(f"Unexpected stylesheet: {name!r}")
        path = STYLE_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing Goodwill stylesheet: {path}")
        st.html(path)
