"""Runtime-configurable retry limits; model token and spending guards still apply."""
from dataclasses import dataclass, fields

from .domain import setting


MAX_CONFIGURED_ITERATIONS = 50


@dataclass(frozen=True)
class DesignerLimits:
    max_draft_attempts: int = 12
    max_rounds: int = 12
    max_review_cycles: int = 8
    max_stalled_rounds: int = 8
    max_stalled_draft_attempts: int = 3

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or not 1 <= value <= MAX_CONFIGURED_ITERATIONS:
                raise ValueError(f"{field.name} must be an integer from 1 to {MAX_CONFIGURED_ITERATIONS}")

    @classmethod
    def from_environment(cls):
        defaults = cls()
        values = {}
        for field in fields(cls):
            key = f"DESIGN_{field.name.upper()}"
            try:
                values[field.name] = int(setting(key, str(getattr(defaults, field.name))))
            except ValueError:
                raise ValueError(f"COFORM_{key} must be an integer from 1 to {MAX_CONFIGURED_ITERATIONS}") from None
        return cls(**values)
