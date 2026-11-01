r"""Web UI.  .venv\Scripts\python.exe -m streamlit run app.py
(The previous UI is kept as app_classic.py: .venv\Scripts\python.exe -m streamlit run app_classic.py)

Home -> Run -> Agents -> Story -> 3D -> Why -> Print -> Downloads -> Charts -> Workshop.
The ★ print candidate run is pre-selected; ?run=<run folder name> picks another one.
"""
import streamlit as st

st.set_page_config(page_title="ScienceClaw · Enclosure agents", page_icon="◈", layout="wide")

from ui import pages as P  # noqa: E402
from ui import theme  # noqa: E402

theme.apply()
P.PAGES.update({
    "home": st.Page(P.home, title="Home", icon="🏠", url_path="home", default=True),
    "run": st.Page(P.run_page, title="Run", icon="▶️", url_path="run"),
    "agents": st.Page(P.agents_page, title="Agents", icon="🤝", url_path="agents"),
    "story": st.Page(P.story_page, title="Story", icon="📜", url_path="story"),
    "viewer": st.Page(P.viewer_page, title="3D", icon="🧊", url_path="viewer"),
    "why": st.Page(P.why_page, title="Why", icon="💡", url_path="why"),
    "print": st.Page(P.print_page, title="Print", icon="🖨️", url_path="print"),
    "downloads": st.Page(P.downloads_page, title="Downloads", icon="⬇️", url_path="downloads"),
    "charts": st.Page(P.charts_page, title="Charts", icon="📊", url_path="charts"),
    "workshop": st.Page(P.workshop_page, title="Workshop", icon="🛠️", url_path="workshop"),
})
st.navigation(list(P.PAGES.values()), position="top").run()
