"""Pydantic domain models for TCO calculator.

Strict validation. All models match the contracts/schemas/*.json definitions.
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

# --- Input models ---


class SignalDistribution(BaseModel):
    """How signals distribute across the three-tier cascade."""
    routine_pct: float = Field(ge=0, le=100)
    ambiguous_pct: float = Field(ge=0, le=100)
    complex_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def distribution_sums_to_100(self):
        total = self.routine_pct + self.ambiguous_pct + self.complex_pct
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"Signal distribution must sum to 100%, got {total}%"
            )
        return self


class TokensPerSignal(BaseModel):
    """Token counts per signal for cost calculation."""
    input: int = Field(ge=1)
    output: int = Field(ge=1)


class ModelByTier(BaseModel):
    """Which model handles each tier of the cascade."""
    nano: Optional[str] = None  # None = deterministic rules, no model
    micro: str
    macro: str


class WorkloadProfile(BaseModel):
    """FSI business process workload definition."""
    id: str
    name: str
    description: str
    daily_volume: int = Field(ge=1)
    signal_distribution: SignalDistribution
    tokens_per_signal: TokensPerSignal
    latency_requirement_ms: int = Field(ge=1)
    model_by_tier: ModelByTier


class Assumptions(BaseModel):
    """Configurable assumptions for TCO calculation."""
    power_cost_per_kwh: float = Field(default=0.10, ge=0)
    hours_per_year: int = Field(default=8760, ge=0)
    tco_years: int = Field(default=3, ge=1)
    idle_hardware: bool = Field(default=False)
    pue_factor: float = Field(default=1.0, ge=1.0, le=2.0)
    redundancy_factor: float = Field(default=1.0, ge=1.0, le=3.0)


class CalculateRequest(BaseModel):
    """Request body for POST /api/v1/calculate."""
    workload: WorkloadProfile
    assumptions: Optional[Assumptions] = None


# --- Output models ---


class CascadeSummary(BaseModel):
    """Summary of signal distribution across the cascade."""
    total_signals_per_day: int
    nano_signals_per_day: int
    micro_signals_per_day: int
    macro_signals_per_day: int
    inference_signals_per_day: int
    compression_ratio: float


class HardwareComparison(BaseModel):
    """TCO result for a single hardware option."""
    hardware_id: str
    hardware_name: str
    hardware_type: str
    units_required: int = Field(ge=0)
    hardware_cost_usd: float = Field(ge=0)
    annual_power_cost_usd: float = Field(ge=0)
    annual_inference_cost_usd: float = Field(ge=0)
    three_year_tco_usd: float = Field(ge=0)
    cost_per_signal_usd: float = Field(ge=0)
    idle_hardware_tco_usd: Optional[float] = Field(default=None, ge=0)
    notes: list[str] = Field(default_factory=list)


class UnsupportedHardwareOption(BaseModel):
    """Hardware option omitted because required throughput is unavailable."""
    hardware_id: str
    hardware_name: str
    reason: str


class TCOResult(BaseModel):
    """Full TCO comparison result."""
    workload_id: str
    workload_name: str
    daily_volume: int
    cascade_summary: CascadeSummary
    comparisons: list[HardwareComparison]
    unsupported_options: list[UnsupportedHardwareOption] = Field(default_factory=list)


# --- Scenario model ---


class Scenario(BaseModel):
    """Pre-built FSI scenario combining a workload with assumptions."""
    id: str
    name: str
    description: str
    workload_id: str
    assumptions: Assumptions
