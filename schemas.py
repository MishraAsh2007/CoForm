# Defines the structure of agent proposals and evaluation results.
#
# Provenance rules are enforced here so no other module can bypass them:
#   - every number carries its source and analysis status;
#   - a value exists only when its analysis completed;
#   - "converged" belongs to FEA only and "validated" to calibrated models only;
#   - a PreForm check passes only if it completed and reported a pass.
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import config

Source = Literal["analytical", "fea", "calibrated", "surrogate", "measured", "synthetic", "preform"]
Status = Literal["completed", "failed", "not_evaluated"]
AgentName = Literal["mechanic", "fabrication", "measurement", "single"]


def _now():
    return datetime.now(timezone.utc)


class Quantity(BaseModel):
    """One number with its unit and provenance."""
    model_config = ConfigDict(frozen=True)

    value: float | None = None
    unit: str
    source: Source
    status: Status
    uncertainty: float | None = Field(default=None, ge=0)  # 1 sigma, same unit as value
    converged: bool | None = None   # FEA mesh convergence; FEA only
    validated: bool | None = None   # held-out validation passed; calibrated models only
    error: str | None = None

    @model_validator(mode="after")
    def _provenance(self):
        if self.status == "completed" and self.value is None:
            raise ValueError("a completed analysis must report a value")
        if self.status != "completed" and self.value is not None:
            raise ValueError(f"a {self.status} analysis must not report a value")
        if self.converged is not None and self.source != "fea":
            raise ValueError("only FEA results carry a convergence flag")
        if self.source == "fea" and self.status == "completed" and self.converged is None:
            raise ValueError("a completed FEA result must state whether it converged")
        if self.validated is not None and self.source != "calibrated":
            raise ValueError("only calibrated-model results carry a validation flag")
        return self

    @property
    def counts_for_selection(self):
        """True only for evidence strong enough to select a final design."""
        if self.status != "completed":
            return False
        return (self.source == "measured"
                or (self.source == "fea" and self.converged is True)
                or (self.source == "calibrated" and self.validated is True))


class GeometryParams(BaseModel):
    """The adjustable bracket dimensions, in mm, checked against config bounds."""
    model_config = ConfigDict(frozen=True)

    plate_thickness: float
    rib_height: float
    rib_thickness: float

    @model_validator(mode="after")
    def _bounds(self):
        for name, (low, high) in config.PARAMETER_BOUNDS.items():
            value = getattr(self, name)
            if not low <= value <= high:
                raise ValueError(f"{name}={value} outside bounds [{low}, {high}] mm")
        legs = min(config.ARM_LENGTH, config.WALL_HEIGHT) - self.plate_thickness
        if self.rib_height > legs:
            raise ValueError(f"rib_height={self.rib_height} exceeds available leg length {legs} mm")
        if self.rib_thickness > config.BRACKET_WIDTH:
            raise ValueError(f"rib_thickness={self.rib_thickness} exceeds width {config.BRACKET_WIDTH} mm")
        if config.INNER_FILLET_RADIUS >= self.rib_height:
            raise ValueError("rib_height must exceed the inner fillet radius")
        return self

    def candidate_id(self):
        """Stable id from the parameters and the fixed geometry they are built in."""
        key = {**self.model_dump(), "envelope": [config.BRACKET_WIDTH, config.ARM_LENGTH,
                                                 config.WALL_HEIGHT, config.INNER_FILLET_RADIUS]}
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        return f"g{digest[:10]}"


class PrintSettings(BaseModel):
    """Print preparation choices; None means use PreForm's automatic setting."""
    model_config = ConfigDict(frozen=True)

    orientation_deg: tuple[float, float, float] | None = None  # rotations about X, Y, Z
    support_density: float | None = None
    touchpoint_size_mm: float | None = None


class StructuralResult(BaseModel):
    level: Literal[1, 2, 3]
    tip_deflection: Quantity   # mm
    max_stress: Quantity       # MPa, von Mises (L2) or bending (L1)
    load_case: dict
    material: dict
    assumptions: list[str] = []
    warnings: list[str] = []
    details: dict = {}         # e.g. the FEA mesh-convergence study
    created_at: datetime = Field(default_factory=_now)


class Evaluation(BaseModel):
    """What the Mechanic's level selection ran for one candidate, and why."""
    candidate_id: str
    accuracy: Literal["screening", "selection"]
    results: list[StructuralResult] = []
    decisions: list[str] = []      # human-readable reasons for each level run or skipped
    l1_verdict: Literal["feasible", "infeasible", "ambiguous"] | None = None


class CheckResult(BaseModel):
    """One PreForm printability check. `passed` is None unless the check completed."""
    name: str
    status: Status
    passed: bool | None = None
    details: dict = {}

    @model_validator(mode="after")
    def _status_first(self):
        if self.status != "completed" and self.passed is not None:
            raise ValueError(f"check {self.name} is {self.status}; it cannot pass or fail")
        if self.status == "completed" and self.passed is None:
            raise ValueError(f"completed check {self.name} must report passed")
        return self


class PrintabilityResult(BaseModel):
    print_settings: PrintSettings
    checks: dict[str, CheckResult]
    print_time: Quantity | None = None       # s, source="preform"
    resin_volume: Quantity | None = None     # mL incl. supports, source="preform"
    form_path: str | None = None
    scene_settings: dict = {}                # machine/material/layer/profile actually used
    preform_version: str | None = None
    error: str | None = None                 # preparation failure; checks are then not_evaluated
    created_at: datetime = Field(default_factory=_now)

    @property
    def all_checks_passed(self):
        return bool(self.checks) and all(c.status == "completed" and c.passed
                                         for c in self.checks.values())


class MeasurementRecord(BaseModel):
    """One physical (or synthetic) test of one specimen."""
    specimen_id: str
    candidate_id: str
    source: Literal["measured", "synthetic"]
    load_n: float
    deflection_mm: float
    mass_g: float | None = None
    fixture_id: str
    print_settings: PrintSettings = PrintSettings()
    post_processing: str = ""
    operator: str = ""
    date: str = ""
    notes: str = ""


class Proposal(BaseModel):
    agent: AgentName
    params: GeometryParams
    print_settings: PrintSettings | None = None
    parent_id: str | None = None
    hypothesis: str
    expected_tradeoff: str = ""
    cites: list[str] = []   # candidate / result ids the proposal relies on


class Message(BaseModel):
    agent: AgentName
    round: int
    text: str
    cites: list[str] = []
    created_at: datetime = Field(default_factory=_now)


class CandidateRecord(BaseModel):
    candidate_id: str
    params: GeometryParams
    proposed_by: AgentName | Literal["baseline", "human"]
    parent_id: str | None = None
    volume: Quantity | None = None   # mm^3, from CAD
    mass: Quantity | None = None     # g
    structural: list[StructuralResult] = []
    printability: list[PrintabilityResult] = []
    decisions: list[str] = []        # why each evaluation level ran or was skipped
    files: dict[str, str] = {}
    created_at: datetime = Field(default_factory=_now)


class CandidateAssessment(BaseModel):
    """One candidate's standing in final selection, from selection-grade evidence only."""
    candidate_id: str
    params: GeometryParams
    mass_g: float | None
    status: Literal["selected", "feasible", "uncertain", "infeasible", "unverified",
                    "not_printable", "print_not_evaluated"]
    p_structural: float | None = None   # P(all structural limits met)
    print_time_s: float | None = None
    reasons: list[str] = []


class SelectionReport(BaseModel):
    selected: str | None              # candidate id, or None if nothing qualifies
    summary: str
    candidates: list[CandidateAssessment]  # sorted by mass
    min_probability: float
    limits: dict
