"""Streamlit AppTest checks: every page of the new UI and the classic UI renders without an exception,
using the print-candidate run (committed copy in results/demo_run)."""
import pytest
from streamlit.testing.v1 import AppTest

PAGES = ["home", "run_page", "agents_page", "story_page", "viewer_page", "why_page", "print_page",
         "downloads_page", "charts_page", "workshop_page"]


def _page_script(name):
    return f"""
import streamlit as st
from ui import pages as P, theme
theme.apply()
for k in ["home","run","agents","story","viewer","why","print","downloads","charts","workshop"]:
    P.PAGES[k] = st.Page(lambda: None, title=k, url_path=k)
P.{name}()
"""


@pytest.mark.parametrize("name", PAGES)
def test_page_renders(name):
    at = AppTest.from_string(_page_script(name), default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]


def test_new_app_entry():
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]


def test_classic_app():
    at = AppTest.from_file("../app_classic.py", default_timeout=120)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
