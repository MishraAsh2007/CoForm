"""The generalisation benchmark itself: valid bases, breakages with known answers, held-out requests kept apart."""
from coform import evaluation
from coform import freeform as ff


def test_every_known_good_design_is_valid_and_gets_its_breakages():
    for name, base in evaluation.known_good().items():
        raw = ff.DesignSpec.model_validate(base).model_dump(mode="json")
        cases = evaluation.breakages(raw, evidence=[])
        names = [c[0] for c in cases]
        assert "part pushed 5 mm into its mount" in names and "part pulled 3 mm off its mount" in names, name
        assert sum(should for _, should, _ in cases) == 2  # the two harmless wall-thickness changes
        for _, _, spec in cases:
            ff.DesignSpec.model_validate(spec)  # every breakage is still a well-formed spec: only its function is broken
        if any(b["role"] == "hardware" for b in raw["environment"]):
            assert "screw holes filled in" in names, name


def test_held_out_requests_are_not_the_ones_the_prompts_were_tuned_on():
    prompts = " ".join(case["prompt"].lower() for case in evaluation.HELD_OUT)
    assert "bottle" not in prompts and len(evaluation.HELD_OUT) >= 12
