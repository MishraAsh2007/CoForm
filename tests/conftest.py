import pytest


@pytest.fixture(autouse=True)
def freehand_drafting(request, monkeypatch):
    """Tests written for the freehand drafter and its repairs skip the concept step (their fake agents answer
    only draft and review calls); tests/test_concepts.py covers the concept step."""
    if request.module.__name__.rsplit(".", 1)[-1] != "test_concepts":
        from coform.designer import Designer
        monkeypatch.setattr(Designer, "_concept", lambda self, *args, **kwargs: None)
