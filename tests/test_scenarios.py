"""Stage 2: Business scenario BDD tests.

Behavior-driven tests that validate expected TCO outcomes for real
FSI business scenarios. These encode the key sales insights.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cascade_compression.tco.calculator import (
    calculate_cascade,
    calculate_full_comparison,
)
from cascade_compression.tco.models import (
    Assumptions,
    ModelByTier,
    SignalDistribution,
    TokensPerSignal,
    WorkloadProfile,
)
from cascade_compression.tco.scenarios import get_scenarios


def make_workload(
    daily_volume=100000,
    routine_pct=85,
    ambiguous_pct=12,
    complex_pct=3,
    input_tokens=150,
    output_tokens=20,
    micro_model="granite-2b",
    macro_model="phi3-mini",
):
    return WorkloadProfile(
        id="test-workload",
        name="Test Workload",
        description="Test",
        daily_volume=daily_volume,
        signal_distribution=SignalDistribution(
            routine_pct=routine_pct,
            ambiguous_pct=ambiguous_pct,
            complex_pct=complex_pct,
        ),
        tokens_per_signal=TokensPerSignal(input=input_tokens, output=output_tokens),
        latency_requirement_ms=500,
        model_by_tier=ModelByTier(nano=None, micro=micro_model, macro=macro_model),
    )


# --- TCO ordering scenarios ---

class TestTCOOrdering:
    """Test that TCO orderings match expected sales outcomes.

    NOTE: With placeholder throughput numbers, the H100's higher throughput
    means fewer units needed, so H100 TCO < Xeon TCO on pure capex+power.
    The Xeon value proposition is strongest in idle hardware mode (customer
    already owns servers) and for data sovereignty requirements. Replace
    throughput numbers with RHAIIS 3.5 benchmarks to see actual crossover.
    """

    def test_idle_xeon_beats_h100(self, hardware_profiles):
        """Idle Xeon (power-only) beats H100 at high volume.

        GIVEN a customer who already owns Xeon 6 servers
        AND a workload with 500K signals/day
        WHEN we calculate 3-year TCO with idle_hardware=True
        THEN Xeon idle TCO is lower than H100 greenfield TCO
        """
        workload = make_workload(daily_volume=500000)
        result_idle = calculate_full_comparison(
            workload, hardware_profiles, Assumptions(idle_hardware=True)
        )
        result_normal = calculate_full_comparison(
            workload, hardware_profiles, Assumptions(idle_hardware=False)
        )
        xeon_idle = next(c for c in result_idle.comparisons
                         if c.hardware_id == "xeon6-6780e")
        h100 = next(c for c in result_normal.comparisons
                     if c.hardware_id == "h100-sxm")
        assert xeon_idle.three_year_tco_usd < h100.three_year_tco_usd, (
            f"Idle Xeon TCO ${xeon_idle.three_year_tco_usd:,.0f} should be less than "
            f"H100 TCO ${h100.three_year_tco_usd:,.0f} at 500K/day"
        )

    def test_xeon_lower_power_per_unit_than_h100(self, hardware_profiles):
        """Xeon uses less power per unit than H100.

        GIVEN both hardware profiles
        THEN Xeon wattage per unit < H100 wattage per unit
        """
        xeon = next(p for p in hardware_profiles["profiles"] if p["id"] == "xeon6-6780e")
        h100 = next(p for p in hardware_profiles["profiles"] if p["id"] == "h100-sxm")
        assert xeon["power_watts"] < h100["power_watts"]

    def test_xeon_lower_power_per_unit(self, hardware_profiles):
        """Xeon has lower power per unit than H100.

        GIVEN the hardware profiles
        THEN Xeon 6 power_watts < H100 power_watts per unit
        """
        xeon = next(p for p in hardware_profiles["profiles"]
                     if p["id"] == "xeon6-6780e")
        h100 = next(p for p in hardware_profiles["profiles"]
                     if p["id"] == "h100-sxm")
        assert xeon["power_watts"] < h100["power_watts"]

    def test_low_volume_cloud_mini_cheapest(self, hardware_profiles):
        """At low volume (<1000/day), cloud API (mini model) has lowest 3-year TCO.

        GIVEN a workload with 500 signals/day
        WHEN we calculate 3-year TCO
        THEN Cloud API (GPT-4o-mini) TCO is lower than Xeon 6 TCO
        """
        workload = make_workload(daily_volume=500)
        result = calculate_full_comparison(
            workload, hardware_profiles, Assumptions()
        )
        xeon = next(c for c in result.comparisons if c.hardware_id == "xeon6-6780e")
        cloud_mini = next(c for c in result.comparisons
                          if c.hardware_id == "cloud-api-economy")
        assert cloud_mini.three_year_tco_usd < xeon.three_year_tco_usd, (
            f"Cloud mini TCO ${cloud_mini.three_year_tco_usd:,.0f} should be less than "
            f"Xeon TCO ${xeon.three_year_tco_usd:,.0f} at 500/day"
        )


# --- Idle hardware scenarios ---

class TestIdleHardware:
    """Test idle hardware (customer already owns servers) scenarios."""

    def test_idle_hardware_lowest_tco(self, hardware_profiles):
        """Idle hardware Xeon produces the lowest possible TCO.

        GIVEN a customer who already owns Xeon 6 servers
        WHEN idle_hardware=True
        THEN their Xeon TCO is lower than their normal Xeon TCO
        """
        workload = make_workload(daily_volume=100000)
        normal = calculate_full_comparison(
            workload, hardware_profiles, Assumptions(idle_hardware=False)
        )
        idle = calculate_full_comparison(
            workload, hardware_profiles, Assumptions(idle_hardware=True)
        )
        xeon_normal = next(c for c in normal.comparisons
                           if c.hardware_id == "xeon6-6780e")
        xeon_idle = next(c for c in idle.comparisons
                          if c.hardware_id == "xeon6-6780e")
        assert xeon_idle.three_year_tco_usd < xeon_normal.three_year_tco_usd

    def test_idle_hardware_capex_is_zero(self, hardware_profiles):
        """Idle hardware mode zeroes out hardware purchase cost.

        GIVEN idle_hardware=True
        WHEN we calculate Xeon TCO
        THEN hardware_cost_usd is 0
        """
        workload = make_workload(daily_volume=100000)
        result = calculate_full_comparison(
            workload, hardware_profiles, Assumptions(idle_hardware=True)
        )
        xeon = next(c for c in result.comparisons if c.hardware_id == "xeon6-6780e")
        assert xeon.hardware_cost_usd == 0


# --- Cascade compression scenarios ---

class TestCascadeCompression:
    """Test cascade compression impact on TCO."""

    def test_compression_reduces_inference_by_80_percent(self):
        """Signal cascade with 85% routine reduces inference volume by >80%.

        GIVEN 85% routine signals
        WHEN we calculate cascade
        THEN effective inference volume is <20% of total
        """
        workload = make_workload(daily_volume=100000, routine_pct=85,
                                  ambiguous_pct=12, complex_pct=3)
        cascade = calculate_cascade(workload)
        compression = cascade.inference_signals_per_day / cascade.total_signals_per_day
        assert compression < 0.20, (
            f"Compression ratio {compression:.2%} should be < 20%"
        )

    def test_fraud_triage_92_percent_compression(self):
        """Fraud triage with 92% routine gives 92% compression.

        GIVEN a fraud triage workload with 92% routine
        WHEN we calculate cascade
        THEN compression ratio is 0.92
        """
        workload = make_workload(daily_volume=1000000, routine_pct=92,
                                  ambiguous_pct=6, complex_pct=2)
        cascade = calculate_cascade(workload)
        assert cascade.compression_ratio == pytest.approx(0.92, abs=0.001)


# --- Named FSI scenario tests ---

class TestFSIScenarios:
    """Test pre-built FSI scenarios."""

    def test_dispute_resolution_scenario(self, hardware_profiles, workload_profiles):
        """Dispute resolution at 500K/day produces valid TCO comparison.

        GIVEN the dispute resolution workload
        WHEN we run a full comparison
        THEN all comparisons have positive TCO values
        """
        dispute = None
        for p in workload_profiles["profiles"]:
            if p["id"] == "dispute-resolution":
                dispute = WorkloadProfile(**p)
                break
        assert dispute is not None

        result = calculate_full_comparison(
            dispute, hardware_profiles, Assumptions()
        )
        for comp in result.comparisons:
            assert comp.three_year_tco_usd > 0, (
                f"{comp.hardware_name} has zero or negative TCO"
            )
            assert comp.cost_per_signal_usd > 0

    def test_fraud_triage_scenario(self, hardware_profiles, workload_profiles):
        """Fraud triage at 1M/day produces valid TCO comparison.

        GIVEN the fraud triage workload (1M transactions/day)
        WHEN we run a full comparison
        THEN all comparisons have positive TCO values
        AND the cascade compression ratio is >= 0.92
        """
        fraud = None
        for p in workload_profiles["profiles"]:
            if p["id"] == "fraud-case-triage":
                fraud = WorkloadProfile(**p)
                break
        assert fraud is not None

        result = calculate_full_comparison(
            fraud, hardware_profiles, Assumptions()
        )
        assert result.cascade_summary.compression_ratio >= 0.92
        for comp in result.comparisons:
            assert comp.three_year_tco_usd > 0

    def test_scenarios_list_not_empty(self):
        """Pre-built scenarios list is not empty."""
        scenarios = get_scenarios()
        assert len(scenarios) >= 3

    def test_scenarios_have_required_fields(self):
        """Each pre-built scenario has all required fields."""
        scenarios = get_scenarios()
        for s in scenarios:
            assert s.id, "Scenario missing id"
            assert s.name, "Scenario missing name"
            assert s.description, "Scenario missing description"
            assert s.workload_id, "Scenario missing workload_id"
            assert s.assumptions is not None, "Scenario missing assumptions"
