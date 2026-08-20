"""Core TCO calculation engine.

Takes a workload profile and hardware profiles, calculates total cost of
ownership across Intel Xeon 6, NVIDIA H100, and cloud API options.

Key insight: The three-tier cascade (nano/micro/macro) means most signals
are handled by deterministic rules with zero inference cost. Only the
ambiguous and complex signals need model inference.
"""

import math
from typing import Any

from .models import (
    Assumptions,
    CascadeSummary,
    HardwareComparison,
    TCOResult,
    UnsupportedHardwareOption,
    WorkloadProfile,
)


class UnsupportedThroughputError(ValueError):
    """Raised when a hardware profile lacks throughput for a required model."""


def calculate_cascade(workload: WorkloadProfile) -> CascadeSummary:
    """Calculate how signals distribute across the three-tier cascade.

    - Nano: routine signals handled by deterministic rules (zero inference)
    - Micro: ambiguous signals handled by small CPU models
    - Macro: complex signals handled by larger models

    Signal counts are integers. We use floor for nano and micro, then assign
    the remainder to macro to guarantee the total equals daily_volume exactly.
    """
    total = workload.daily_volume
    dist = workload.signal_distribution

    nano = int(math.floor(total * dist.routine_pct / 100))
    micro = int(math.floor(total * dist.ambiguous_pct / 100))
    # Assign remainder to macro to ensure exact sum
    macro = total - nano - micro

    inference = micro + macro
    compression = nano / total if total > 0 else 0.0

    return CascadeSummary(
        total_signals_per_day=total,
        nano_signals_per_day=nano,
        micro_signals_per_day=micro,
        macro_signals_per_day=macro,
        inference_signals_per_day=inference,
        compression_ratio=round(compression, 6),
    )


def _lookup_throughput(model_name: str, models: dict, legacy_throughput: dict) -> float:
    """Look up throughput for a model, trying new models dict then legacy."""
    if model_name in models:
        return models[model_name].get("tokens_per_second", 0)
    if model_name in legacy_throughput:
        return legacy_throughput[model_name].get("tokens_per_second", 0)
    return 0


def _required_units(
    cascade: CascadeSummary,
    workload: WorkloadProfile,
    hardware: dict[str, Any],
) -> int:
    """Calculate how many hardware units are needed for the inference workload.

    Uses the micro-tier model throughput for micro signals and the macro-tier
    model throughput for macro signals. Sums the required capacity and rounds up.
    """
    throughput = hardware.get("inference_throughput", {})
    micro_model = workload.model_by_tier.micro
    macro_model = workload.model_by_tier.macro

    # Tokens needed per day for each tier
    tokens_per_signal = workload.tokens_per_signal.input + workload.tokens_per_signal.output

    # Micro tier throughput — try new models structure first, fall back to legacy
    models = hardware.get("models", {})
    micro_tps = _lookup_throughput(micro_model, models, throughput)
    macro_tps = _lookup_throughput(macro_model, models, throughput)

    # Daily token demand
    micro_tokens_per_day = cascade.micro_signals_per_day * tokens_per_signal
    macro_tokens_per_day = cascade.macro_signals_per_day * tokens_per_signal

    missing_models = []
    if micro_tokens_per_day > 0 and micro_tps <= 0:
        missing_models.append(micro_model)
    if macro_tokens_per_day > 0 and macro_tps <= 0:
        missing_models.append(macro_model)
    if missing_models:
        hardware_id = hardware.get("id", "unknown")
        missing = ", ".join(sorted(set(missing_models)))
        raise UnsupportedThroughputError(
            f"hardware '{hardware_id}' has no measured throughput for: {missing}"
        )

    # Tokens each unit can serve per day (assuming 24h operation)
    seconds_per_day = 86400

    units_for_micro = 0
    if micro_tokens_per_day > 0 and micro_tps > 0:
        micro_capacity_per_unit = micro_tps * seconds_per_day
        units_for_micro = math.ceil(micro_tokens_per_day / micro_capacity_per_unit)

    units_for_macro = 0
    if macro_tokens_per_day > 0 and macro_tps > 0:
        macro_capacity_per_unit = macro_tps * seconds_per_day
        units_for_macro = math.ceil(macro_tokens_per_day / macro_capacity_per_unit)

    return units_for_micro + units_for_macro


def calculate_hardware_tco(
    cascade: CascadeSummary,
    workload: WorkloadProfile,
    hardware: dict[str, Any],
    assumptions: Assumptions,
) -> HardwareComparison:
    """Calculate TCO for an on-premises hardware option (CPU or GPU).

    Formula:
      units = ceil(inference_demand / throughput_per_unit)
      hardware_cost = units * cost_per_unit (0 if idle_hardware)
      annual_power = units * watts * hours_per_year * cost_per_kwh / 1000
      annual_inference = 0 (included in hardware cost)
      N-year TCO = hardware_cost + N * annual_power
    """
    base_units = _required_units(cascade, workload, hardware)
    units = math.ceil(base_units * assumptions.redundancy_factor)

    cost_per_unit = hardware.get("cost_per_unit_usd", 0)
    power_watts = hardware.get("power_watts", 0)

    # Hardware capex
    if assumptions.idle_hardware:
        hardware_cost = 0.0
    else:
        hardware_cost = units * cost_per_unit

    # Annual power (convert watts to kW), with PUE overhead
    annual_power = (
        units * power_watts * assumptions.hours_per_year
        * assumptions.power_cost_per_kwh * assumptions.pue_factor / 1000
    )

    # On-prem has zero per-inference cost
    annual_inference = 0.0

    # N-year TCO
    tco = hardware_cost + assumptions.tco_years * (annual_power + annual_inference)

    # Cost per signal over the TCO period
    total_signals = workload.daily_volume * 365 * assumptions.tco_years
    cost_per_signal = tco / total_signals if total_signals > 0 else 0.0

    # Idle hardware TCO (power-only, regardless of idle_hardware flag)
    idle_tco = assumptions.tco_years * annual_power

    notes = []
    if assumptions.idle_hardware:
        notes.append("Idle hardware mode: customer already owns servers, capex = $0")
    if assumptions.pue_factor > 1.0:
        notes.append(f"PUE factor {assumptions.pue_factor:.1f}x applied to power cost")
    if assumptions.redundancy_factor > 1.0:
        notes.append(f"Redundancy factor {assumptions.redundancy_factor:.1f}x applied ({units} units, {base_units} base)")
    provenance = hardware.get("benchmark_provenance", {})
    if provenance:
        notes.append(f"Throughput measured on {provenance.get('cluster', 'benchmark cluster')}")

    return HardwareComparison(
        hardware_id=hardware["id"],
        hardware_name=hardware["name"],
        hardware_type=hardware["type"],
        units_required=units,
        hardware_cost_usd=round(hardware_cost, 2),
        annual_power_cost_usd=round(annual_power, 2),
        annual_inference_cost_usd=round(annual_inference, 2),
        three_year_tco_usd=round(tco, 2),
        cost_per_signal_usd=round(cost_per_signal, 8),
        idle_hardware_tco_usd=round(idle_tco, 2),
        notes=notes,
    )


def calculate_cloud_tco(
    cascade: CascadeSummary,
    workload: WorkloadProfile,
    hardware: dict[str, Any],
    assumptions: Assumptions,
) -> HardwareComparison:
    """Calculate TCO for a cloud API option.

    Formula:
      daily_inference_signals = micro_signals + macro_signals
      daily_input_tokens = daily_inference_signals * tokens_per_signal.input
      daily_output_tokens = daily_inference_signals * tokens_per_signal.output
      daily_cost = (input_tokens * cost_per_1k_input / 1000)
               + (output_tokens * cost_per_1k_output / 1000)
      annual_inference = daily_cost * 365
      N-year TCO = N * annual_inference (no hardware, no power)
    """
    cost_per_1k_input = hardware.get("cost_per_1k_input_tokens", 0)
    cost_per_1k_output = hardware.get("cost_per_1k_output_tokens", 0)

    # Only inference signals incur cost
    daily_input_tokens = cascade.inference_signals_per_day * workload.tokens_per_signal.input
    daily_output_tokens = cascade.inference_signals_per_day * workload.tokens_per_signal.output

    daily_cost = (
        (daily_input_tokens * cost_per_1k_input / 1000)
        + (daily_output_tokens * cost_per_1k_output / 1000)
    )
    annual_inference = daily_cost * 365

    tco = assumptions.tco_years * annual_inference

    total_signals = workload.daily_volume * 365 * assumptions.tco_years
    cost_per_signal = tco / total_signals if total_signals > 0 else 0.0

    notes = [
        "Cloud API: no hardware or power cost, pay per token",
        "Nano-tier signals (deterministic rules) incur zero API cost",
        "Data leaves your infrastructure — review data sovereignty and compliance requirements",
    ]

    return HardwareComparison(
        hardware_id=hardware["id"],
        hardware_name=hardware["name"],
        hardware_type=hardware["type"],
        units_required=0,
        hardware_cost_usd=0.0,
        annual_power_cost_usd=0.0,
        annual_inference_cost_usd=round(annual_inference, 2),
        three_year_tco_usd=round(tco, 2),
        cost_per_signal_usd=round(cost_per_signal, 8),
        idle_hardware_tco_usd=None,
        notes=notes,
    )


def calculate_full_comparison(
    workload: WorkloadProfile,
    hardware_profiles: dict[str, Any],
    assumptions: Assumptions,
) -> TCOResult:
    """Calculate TCO across all hardware options for a workload.

    Returns a TCOResult with cascade summary and per-hardware comparisons.
    """
    cascade = calculate_cascade(workload)

    comparisons = []
    unsupported_options = []
    for hw in hardware_profiles["profiles"]:
        try:
            if hw["type"] == "cloud_api":
                comp = calculate_cloud_tco(cascade, workload, hw, assumptions)
            else:
                comp = calculate_hardware_tco(cascade, workload, hw, assumptions)
            comparisons.append(comp)
        except UnsupportedThroughputError as exc:
            unsupported_options.append(UnsupportedHardwareOption(
                hardware_id=hw.get("id", "unknown"),
                hardware_name=hw.get("name", hw.get("id", "unknown")),
                reason=str(exc),
            ))

    return TCOResult(
        workload_id=workload.id,
        workload_name=workload.name,
        daily_volume=workload.daily_volume,
        cascade_summary=cascade,
        comparisons=comparisons,
        unsupported_options=unsupported_options,
    )
