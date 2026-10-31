"""Shared contracts. All engineering quantities use SI units."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Geometry(Record):
    section_family: Literal["u_channel", "solid_bar", "rectangular_tube"] = "u_channel"
    length_m: float = Field(default=0.16, gt=0)
    clamp_length_m: float = Field(default=0.025, gt=0)
    base_width_m: float = Field(default=0.025, gt=0)
    wall_height_m: float = Field(default=0.010, ge=0)
    thickness_m: float = Field(default=0.0012, gt=0)
    material_batch: str = Field(default="pilot-resin", min_length=1)

    @model_validator(mode="after")
    def dimensions(self):
        if self.clamp_length_m >= self.length_m:
            raise ValueError("Clamp length must be less than total length")
        if self.thickness_m >= self.base_width_m:
            raise ValueError("Thickness must be smaller than base width")
        if self.section_family != "solid_bar" and self.wall_height_m > 0 and self.base_width_m <= 2 * self.thickness_m:
            raise ValueError("U-channel base must be wider than two walls")
        if self.section_family == "rectangular_tube" and self.wall_height_m <= self.thickness_m:
            raise ValueError("Tube outside height must exceed two wall thicknesses")
        return self

    @property
    def free_length_m(self) -> float:
        return self.length_m - self.clamp_length_m


class ModelParameters(Record):
    youngs_modulus_pa: float = Field(default=2.5e9, gt=0)
    rotational_compliance_rad_per_nm: float = Field(default=0.0, ge=0)


class SimulationScenario(Record):
    """Declared synthetic truth. Never included in the agents' context."""
    name: str = "default"
    youngs_modulus_pa: float = Field(default=1.3e9, gt=0)
    rotational_compliance_rad_per_nm: float = Field(default=0.25, ge=0)
    noise_std_m: float = Field(default=0.000015, gt=0)
    fixture_motion_m: float = 0.0
    residual_m: float = 0.0
    nonlinear_force_coefficient: float = Field(default=0, ge=0)
    # "fea": measurements come from the virtual test rig (3D finite elements of the CAD
    # geometry, datasheet resin, elastic clamp jaws) instead of the beam formula above.
    physics: Literal["beam", "fea"] = "beam"
    material: Literal["alpha_fast", "standard_resin", "rigid_10k"] = "alpha_fast"
    fixture: Literal["rigid_vise", "printed_jaws", "rubber_lined"] = "printed_jaws"
    cure_fraction: float | None = Field(default=None, ge=0, le=1)
    # Requirements that make this scenario a real trade-off (applied by comparisons).
    protocol_overrides: dict[str, float] = Field(default_factory=dict)


class Protocol(Record):
    proof_force_n: float = Field(default=0.3, gt=0)
    proof_position_m: float = Field(default=0.12, gt=0)
    max_deflection_m: float = Field(default=0.002, gt=0)
    max_residual_m: float = Field(default=0.0002, gt=0)
    max_fixture_motion_m: float = Field(default=0.0001, gt=0)
    max_uncertainty_m: float = Field(default=0.00015, gt=0)
    dwell_s: float = Field(default=3.0, gt=0)
    recovery_s: float = Field(default=3.0, gt=0)
    max_deflection_ratio: float = Field(default=0.05, gt=0, lt=1)
    search_cycles: int = Field(default=8, ge=1, le=100)
    max_designs: int = Field(default=3, ge=1, le=30)
    # Optional design requirements; None means "not required".
    max_volume_m3: float | None = Field(default=None, gt=0)
    max_root_stress_pa: float | None = Field(default=None, gt=0)
    final_copies: int = Field(default=3, ge=1, le=20)
    max_rounds: int = Field(default=3, ge=1, le=10)
    token_budget: int = Field(default=200000, ge=1000, le=1000000)
    base_width_min_m: float = Field(default=0.015, gt=0)
    base_width_max_m: float = Field(default=0.040, gt=0)
    wall_height_min_m: float = Field(default=0.005, ge=0)
    wall_height_max_m: float = Field(default=0.015, gt=0)
    geometry_step_m: float = Field(default=0.001, gt=0)
    diagnostic_positions_m: tuple[float, ...] = (0.06, 0.12)
    reserved_position_m: float = Field(default=0.09, gt=0)

    @model_validator(mode="after")
    def bounds(self):
        if self.base_width_max_m < self.base_width_min_m or self.wall_height_max_m < self.wall_height_min_m:
            raise ValueError("Geometry bounds are reversed")
        if len(set(self.diagnostic_positions_m)) < 2 or any(p <= 0 for p in self.diagnostic_positions_m):
            raise ValueError("At least two distinct positive diagnostic positions required")
        if self.reserved_position_m in self.diagnostic_positions_m:
            raise ValueError("Reserved position must not be in the diagnostic menu")
        if self.reserved_position_m == self.proof_position_m:
            raise ValueError("Reserved position must differ from the proof condition")
        return self


class TeamMember(Record):
    """Provider and exact model for one agent role; frozen with the study."""
    provider: Literal["openai", "anthropic", "gemini", "nvidia"]
    model: str = Field(pattern=r"^[A-Za-z0-9._:/-]+$", min_length=1, max_length=120)


class Observation(Record):
    id: str = Field(default_factory=lambda: identifier("obs"))
    candidate_id: str
    specimen_id: str = Field(min_length=1)
    experiment_id: str
    geometry: Geometry
    force_n: float = Field(gt=0)
    load_position_m: float = Field(gt=0)
    displacement_m: float
    uncertainty_m: float = Field(gt=0)
    dwell_s: float = Field(gt=0)
    recovery_s: float = Field(gt=0)
    residual_m: float = 0.0
    fixture_motion_m: float = 0.0
    quality_flags: tuple[str, ...] = ()
    source: Literal["simulated", "physical"]
    purpose: Literal["initial", "search", "reserved", "acceptance"] = "search"
    raw_artifact: str | None = None
    mass_kg: float | None = Field(default=None, gt=0)
    human_notes: str = ""
    timestamp: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def position(self):
        if self.load_position_m > self.geometry.free_length_m:
            raise ValueError("Load position exceeds free length")
        return self
