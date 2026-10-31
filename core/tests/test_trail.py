"""Decision trail + AI summary: numbers must come from the log, never from model text."""
import shutil

import pytest

from cad.common import ROOT
from core.board import Board
from core.trail import ai_summary, trail

RUN = ROOT / "results/runs/a_pi5_standard__collective__generic__s0__claude-haiku-5-5"


class FakeLLM:
    def __init__(self, text):
        self.text = text

    def complete(self, system, prompt, images=(), mock_fn=None):
        return self.text, {}


@pytest.fixture
def board(tmp_path):
    if not (RUN / "board.sqlite").exists():
        pytest.skip("reference run not present")
    shutil.copy(RUN / "board.sqlite", tmp_path / "b.sqlite")
    return Board(tmp_path / "b.sqlite")


def _evaluated_version(board):
    return next(d["version_id"] for d in board.decisions(1) if d["decision"] == "accepted")


def test_trail_order_and_numbers(board):
    vid = _evaluated_version(board)
    entries, nums = trail(board, vid)
    kinds = [e["kind"] for e in entries]
    assert "proposal" in kinds and kinds[-1] == "decision"
    assert kinds.index("proposal") < kinds.index("decision")
    assert nums["decision"] == "accepted" and nums["failures_before"] >= nums["failures_after"] - nums["broke"]


def test_model_digits_are_removed_and_flagged(board):
    vid = _evaluated_version(board)
    s = ai_summary(board, vid, FakeLLM("The edit fixed 999 failures, going from {failures_before} to "
                                       "{failures_after} [E1]."))
    _, nums = trail(board, vid)
    assert s["flagged"] and "999" not in s["text"]
    assert str(nums["failures_before"]) in s["text"] and s["entries_cited"] == ["E1"]


def test_clean_summary_not_flagged(board):
    vid = _evaluated_version(board)
    s = ai_summary(board, vid, FakeLLM("{author} changed the design and it was {decision} [E2]."))
    assert not s["flagged"] and "accepted" in s["text"]
