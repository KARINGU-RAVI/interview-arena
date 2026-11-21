"""The ONLY custom CSS in the app (spec: small, scoped, no JS). Everything else — colours,
fonts, heading sizes, radius — comes from the theme in .streamlit/config.toml.

What the theme can't do:
1. Content max width ~860 px (Streamlit's centred layout is narrower/wider by version).
2. Question line-height 1.4.
3. On phones, buttons full width (spec §1: auto width except on mobile).
Selectors use Streamlit's own stable class names (checked in the 1.67 frontend bundle).
"""

import streamlit as st

_CSS = """
<style>
.stMainBlockContainer { max-width: 860px; }
.stMainBlockContainer h4 { line-height: 1.4; }
@media (max-width: 640px) {
  .stMainBlockContainer .stButton button,
  .stMainBlockContainer .stFormSubmitButton button,
  .stMainBlockContainer .stDownloadButton button { width: 100%; }
}
</style>
"""


def apply_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
