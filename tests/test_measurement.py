import csv
import io

import pytest

from coform.domain import Geometry, Observation, Protocol
from coform.measurement import TRACE_COLUMNS, acceptance_summary, analyze_trace, classify_observation


def observation(**overrides):
    values = dict(candidate_id="candidate", specimen_id="specimen-1", experiment_id="experiment-1",
                  geometry=Geometry(), force_n=0.3, load_position_m=0.12, displacement_m=0.001,
                  uncertainty_m=0.00002, dwell_s=3, recovery_s=3, source="physical", purpose="acceptance")
    values.update(overrides)
    return Observation(**values)


def trace(*, fixture_shift=0, root_shift=0, noise=0, skip_frame=False, missing=False):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=TRACE_COLUMNS)
    writer.writeheader()
    frame = 0
    for phase, displacement in (("unloaded", 0), ("loaded", 100), ("recovered", 2)):
        for i in range(5):
            frame += 2 if skip_frame and i == 3 else 1
            offset = fixture_shift if phase != "unloaded" else 0
            row = dict(frame=frame, time_s=frame / 30, phase=phase,
                       fixture_x_px=50, fixture_y_px=50 + offset,
                       root_x_px=60, root_y_px=50 + offset + (root_shift if phase == "loaded" else 0),
                       load_x_px=150, load_y_px=50 + offset + displacement + noise * (i % 2))
            if missing and phase == "loaded" and i == 1:
                row["load_y_px"] = ""
            writer.writerow(row)
    return stream.getvalue()


def test_physical_and_simulated_pass_are_distinct():
    protocol = Protocol()
    assert classify_observation(observation(), protocol)["status"] == "physical_pass"
    assert classify_observation(observation(source="simulated"), protocol)["status"] == "simulated_pass"


def test_guard_band_and_nonproof_conditions_cannot_pass():
    protocol = Protocol()
    assert classify_observation(observation(displacement_m=0.00198), protocol)["status"] == "uncertain"
    assert classify_observation(observation(displacement_m=0.0021), protocol)["status"] == "fail"
    assert classify_observation(observation(force_n=0.15), protocol)["status"] == "uncertain"
    assert classify_observation(observation(load_position_m=0.06), protocol)["status"] == "uncertain"


@pytest.mark.parametrize("change", [
    dict(quality_flags=("tracking_loss",)), dict(fixture_motion_m=0.0002),
    dict(dwell_s=2), dict(recovery_s=1), dict(displacement_m=0.01),
    dict(displacement_m=-0.001),
])
def test_invalid_cycles_never_pass(change):
    assert classify_observation(observation(**change), Protocol())["status"] == "invalid"


def test_permanent_set_failure_and_uncertainty():
    assert classify_observation(observation(residual_m=0.0003), Protocol())["status"] == "fail"
    assert classify_observation(observation(residual_m=0.00019), Protocol())["status"] == "uncertain"


def test_three_independent_acceptance_copies_required():
    copies = [observation(specimen_id=f"copy-{i}", experiment_id=f"exp-{i}") for i in range(3)]
    result = acceptance_summary(copies, Protocol())
    assert result["status"] == "physical_pass"
    assert result["passing_specimens"] == 3
    repeats = [observation(experiment_id=f"repeat-{i}") for i in range(3)]
    result = acceptance_summary(repeats, Protocol())
    assert result["status"] == "incomplete"
    assert result["independent_specimens_tested"] == 1


def test_acceptance_rejects_source_mixing_reused_specimens_and_bad_repeats():
    copies = [observation(specimen_id=f"copy-{i}", experiment_id=f"exp-{i}") for i in range(3)]
    assert acceptance_summary(copies + [observation(purpose="search", specimen_id="copy-1")], Protocol())["status"] == "invalid"
    assert acceptance_summary(copies + [observation(source="simulated")], Protocol())["status"] == "invalid"
    assert acceptance_summary(copies + [observation(specimen_id="copy-1", displacement_m=0.003)], Protocol())["status"] == "fail"
    assert acceptance_summary([observation(purpose="search")], Protocol())["status"] == "incomplete"


def test_trace_known_motion_and_conservative_uncertainty():
    result = analyze_trace(trace(root_shift=10), pixels_per_m=100000)
    assert result["displacement_m"] == pytest.approx(0.001)
    assert result["residual_m"] == pytest.approx(0.00002)
    assert result["root_motion_m"] == pytest.approx(0.0001)
    assert result["quality_flags"] == []
    assert result["uncertainty_m"] > 0
    assert result["independent_specimens"] == 1


def test_fixture_subtraction_does_not_hide_fixture_motion():
    result = analyze_trace(trace(fixture_shift=20), pixels_per_m=100000)
    assert result["displacement_m"] == pytest.approx(0.001)
    assert result["fixture_motion_m"] == pytest.approx(0.0002)
    assert "fixture_motion_exceeds_limit" in result["quality_flags"]


def test_missing_samples_and_tracking_jumps_are_flagged():
    result = analyze_trace(trace(skip_frame=True, missing=True), pixels_per_m=100000)
    assert "missing_frames_within_phase" in result["quality_flags"]
    assert "missing_or_invalid_marker_sample" in result["quality_flags"]
    result = analyze_trace(trace(noise=300), pixels_per_m=100000)
    assert "abrupt_marker_jump" in result["quality_flags"]
    assert "noisy_trace" in result["quality_flags"]


def test_bad_calibration_or_incomplete_trace_rejected():
    with pytest.raises(ValueError):
        analyze_trace(trace(), pixels_per_m=0)
    with pytest.raises(ValueError):
        analyze_trace(trace(), pixels_per_m=float("nan"))
    with pytest.raises(ValueError):
        analyze_trace("\n".join(trace().splitlines()[:4]), pixels_per_m=100000)
