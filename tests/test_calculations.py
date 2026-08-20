"""Stage 1: Calculation accuracy tests.

Tests that the TCO calculation engine produces mathematically correct results.
These are the RED tests — written before implementation.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cascade_compression.tco.calculator import (
    UnsupportedThroughputError,
    calculate_cascade,
    calculate_cloud_tco,
    calculate_full_comparison,
    calculate_hardware_tco,
)
from cascade_compression.tco.models import (
    Assumptions,
    ModelByTier,
    SignalDistribution,
    TokensPerSignal,
    WorkloadProfile,
)

# --- Helper to build a test workload ---

def make_workload(
    daily_volume=100000,
    routine_pct=85,
    ambiguous_pct=12,
    complex_pct=3,
    input_tokens=150,
    output_tokens=20,
    latency_ms=500,
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
        latency_requirement_ms=latency_ms,
        model_by_tier=ModelByTier(nano=None, micro=micro_model, macro=macro_model),
    )


DEFAULT_ASSUMPTIONS = Assumptions()


# --- Cascade calculation tests ---

class TestCascadeCalculation:
    """Test signal distribution across the three-tier cascade."""

    def test_signal_split_adds_up(self):
        """Total of nano + micro + macro signals equals daily volume."""
        workload = make_workload(daily_volume=500000, routine_pct=85,
                                  ambiguous_pct=12, complex_pct=3)
        cascade = calculate_cascade(workload)
        total = (cascade.nano_signals_per_day +
                 cascade.micro_signals_per_day +
                 cascade.macro_signals_per_day)
        assert total == 500000

    def test_nano_signals_are_routine(self):
        """Nano-agent signals = daily_volume * routine_pct / 100."""
        workload = make_workload(daily_volume=100000, routine_pct=85)
        cascade = calculate_cascade(workload)
        assert cascade.nano_signals_per_day == 85000

    def test_micro_signals_are_ambiguous(self):
        """Micro-agent signals = daily_volume * ambiguous_pct / 100."""
        workload = make_workload(daily_volume=100000, ambiguous_pct=12)
        cascade = calculate_cascade(workload)
        assert cascade.micro_signals_per_day == 12000

    def test_macro_signals_are_complex(self):
        """Macro-agent signals = daily_volume * complex_pct / 100."""
        workload = make_workload(daily_volume=100000, complex_pct=3)
        cascade = calculate_cascade(workload)
        assert cascade.macro_signals_per_day == 3000

    def test_inference_signals_excludes_nano(self):
        """Inference signals = micro + macro (nano has zero inference)."""
        workload = make_workload(daily_volume=100000, routine_pct=85,
                                  ambiguous_pct=12, complex_pct=3)
        cascade = calculate_cascade(workload)
        assert cascade.inference_signals_per_day == 15000

    def test_compression_ratio(self):
        """Compression ratio = signals handled without inference / total."""
        workload = make_workload(daily_volume=100000, routine_pct=85,
                                  ambiguous_pct=12, complex_pct=3)
        cascade = calculate_cascade(workload)
        assert cascade.compression_ratio == pytest.approx(0.85, abs=0.001)

    def test_high_routine_compression(self):
        """92% routine gives 0.92 compression ratio."""
        workload = make_workload(daily_volume=1000000, routine_pct=92,
                                  ambiguous_pct=6, complex_pct=2)
        cascade = calculate_cascade(workload)
        assert cascade.compression_ratio == pytest.approx(0.92, abs=0.001)

    def test_rounding_with_odd_percentages(self):
        """Signal counts are integers even with percentages that don't divide evenly."""
        workload = make_workload(daily_volume=333, routine_pct=85,
                                  ambiguous_pct=12, complex_pct=3)
        cascade = calculate_cascade(workload)
        # All counts must be integers
        assert isinstance(cascade.nano_signals_per_day, int)
        assert isinstance(cascade.micro_signals_per_day, int)
        assert isinstance(cascade.macro_signals_per_day, int)
        # Must sum to total
        total = (cascade.nano_signals_per_day +
                 cascade.micro_signals_per_day +
                 cascade.macro_signals_per_day)
        assert total == 333


# --- Hardware TCO tests (CPU and GPU) ---

class TestHardwareTCO:
    """Test on-premises hardware TCO calculations."""

    def test_hardware_count_rounds_up(self, xeon6_profile):
        """Can't buy half a server — hardware count rounds up."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        assert isinstance(result.units_required, int)
        assert result.units_required >= 1

    def test_low_volume_needs_few_units(self, xeon6_profile):
        """Low volume workload should need very few servers (1-2).

        With micro and macro tiers each needing at least 1 unit, a low-volume
        workload with both tiers may need 2 units minimum.
        """
        workload = make_workload(daily_volume=1000, routine_pct=85,
                                  ambiguous_pct=12, complex_pct=3)
        cascade = calculate_cascade(workload)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.units_required <= 2

    def test_hardware_cost_is_units_times_price(self, xeon6_profile):
        """Hardware cost = units_required * cost_per_unit."""
        workload = make_workload(daily_volume=1000)
        cascade = calculate_cascade(workload)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        expected_hw_cost = result.units_required * xeon6_profile["cost_per_unit_usd"]
        assert result.hardware_cost_usd == expected_hw_cost

    def test_power_cost_formula(self, xeon6_profile):
        """Annual power = units * watts * hours_per_year * cost_per_kwh / 1000."""
        workload = make_workload(daily_volume=1000)
        cascade = calculate_cascade(workload)
        assumptions = Assumptions(power_cost_per_kwh=0.10, hours_per_year=8760)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, assumptions
        )
        expected_power = (
            result.units_required
            * xeon6_profile["power_watts"]
            * 8760
            * 0.10
            / 1000
        )
        assert result.annual_power_cost_usd == pytest.approx(expected_power, rel=0.01)

    def test_three_year_tco_formula(self, xeon6_profile):
        """3-year TCO = hardware_cost + 3 * (annual_power + annual_inference)."""
        workload = make_workload(daily_volume=1000)
        cascade = calculate_cascade(workload)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        expected_tco = (
            result.hardware_cost_usd
            + 3 * (result.annual_power_cost_usd + result.annual_inference_cost_usd)
        )
        assert result.three_year_tco_usd == pytest.approx(expected_tco, rel=0.01)

    def test_hardware_inference_cost_is_zero(self, xeon6_profile):
        """On-prem hardware has zero annual inference cost (it's built into HW cost)."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.annual_inference_cost_usd == 0

    def test_idle_hardware_zeros_capex(self, xeon6_profile):
        """Idle hardware mode sets hardware cost to 0."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        assumptions = Assumptions(idle_hardware=True)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, assumptions
        )
        assert result.hardware_cost_usd == 0
        assert result.idle_hardware_tco_usd is not None

    def test_idle_hardware_tco_is_power_only(self, xeon6_profile):
        """Idle hardware TCO = 3 * annual_power (no capex)."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        assumptions = Assumptions(idle_hardware=True, tco_years=3)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, assumptions
        )
        expected = 3 * result.annual_power_cost_usd
        assert result.idle_hardware_tco_usd == pytest.approx(expected, rel=0.01)

    def test_cost_per_signal_calculated(self, xeon6_profile):
        """Cost per signal = 3-year TCO / (daily_volume * 365 * 3)."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        total_signals = 100000 * 365 * 3
        expected_cps = result.three_year_tco_usd / total_signals
        assert result.cost_per_signal_usd == pytest.approx(expected_cps, rel=0.01)

    def test_cpu_cheaper_per_unit_than_gpu(self, xeon6_profile, h100_profile):
        """CPU hardware cost per unit is lower than GPU."""
        assert xeon6_profile["cost_per_unit_usd"] < h100_profile["cost_per_unit_usd"]


# --- Cloud API TCO tests ---

class TestCloudTCO:
    """Test cloud API TCO calculations."""

    def test_cloud_has_zero_hardware_cost(self, cloud_api_profile):
        """Cloud API has no hardware purchase cost."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_cloud_tco(
            cascade, workload, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.hardware_cost_usd == 0

    def test_cloud_has_zero_power_cost(self, cloud_api_profile):
        """Cloud API has no power cost."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_cloud_tco(
            cascade, workload, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.annual_power_cost_usd == 0

    def test_cloud_has_zero_units(self, cloud_api_profile):
        """Cloud API requires zero hardware units."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_cloud_tco(
            cascade, workload, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.units_required == 0

    def test_cloud_inference_cost_scales_linearly(self, cloud_api_profile):
        """Doubling volume should roughly double cloud API cost."""
        workload_1x = make_workload(daily_volume=100000)
        workload_2x = make_workload(daily_volume=200000)
        cascade_1x = calculate_cascade(workload_1x)
        cascade_2x = calculate_cascade(workload_2x)
        result_1x = calculate_cloud_tco(
            cascade_1x, workload_1x, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        result_2x = calculate_cloud_tco(
            cascade_2x, workload_2x, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        ratio = result_2x.annual_inference_cost_usd / result_1x.annual_inference_cost_usd
        assert ratio == pytest.approx(2.0, rel=0.01)

    def test_cloud_three_year_tco_is_inference_only(self, cloud_api_profile):
        """Cloud 3-year TCO = 3 * annual_inference_cost."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        result = calculate_cloud_tco(
            cascade, workload, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        expected = 3 * result.annual_inference_cost_usd
        assert result.three_year_tco_usd == pytest.approx(expected, rel=0.01)

    def test_cloud_cost_includes_input_and_output_tokens(self, cloud_api_profile):
        """Cloud cost accounts for both input and output token pricing."""
        workload = make_workload(daily_volume=100000, input_tokens=100,
                                  output_tokens=50)
        cascade = calculate_cascade(workload)
        result = calculate_cloud_tco(
            cascade, workload, cloud_api_profile, DEFAULT_ASSUMPTIONS
        )
        # Annual cost should be > 0 (we have inference signals)
        assert result.annual_inference_cost_usd > 0


# --- Edge cases ---

class TestEdgeCases:
    """Test edge cases in the calculation engine."""

    def test_zero_complex_signals(self, xeon6_profile):
        """Zero complex signals should still work (no macro-agent needed)."""
        workload = make_workload(daily_volume=100000, routine_pct=90,
                                  ambiguous_pct=10, complex_pct=0)
        cascade = calculate_cascade(workload)
        assert cascade.macro_signals_per_day == 0
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.three_year_tco_usd > 0

    def test_all_routine_signals(self, xeon6_profile):
        """100% routine means zero inference, but still need minimum hardware."""
        workload = make_workload(daily_volume=100000, routine_pct=100,
                                  ambiguous_pct=0, complex_pct=0)
        cascade = calculate_cascade(workload)
        assert cascade.inference_signals_per_day == 0
        assert cascade.compression_ratio == 1.0

    def test_missing_throughput_is_rejected(self, h100_profile):
        workload = make_workload(
            micro_model="granite-350m",
            macro_model="granite-4.1-8b",
        )
        cascade = calculate_cascade(workload)
        with pytest.raises(UnsupportedThroughputError, match="granite-350m"):
            calculate_hardware_tco(
                cascade, workload, h100_profile, DEFAULT_ASSUMPTIONS
            )

    def test_minimum_volume(self, xeon6_profile):
        """Volume of 1 signal/day should work."""
        workload = make_workload(daily_volume=1, routine_pct=0,
                                  ambiguous_pct=0, complex_pct=100)
        cascade = calculate_cascade(workload)
        assert cascade.macro_signals_per_day == 1
        result = calculate_hardware_tco(
            cascade, workload, xeon6_profile, DEFAULT_ASSUMPTIONS
        )
        assert result.units_required >= 1

    def test_custom_tco_years(self, xeon6_profile):
        """Custom TCO period (5 years) should scale power cost."""
        workload = make_workload(daily_volume=100000)
        cascade = calculate_cascade(workload)
        assumptions_3y = Assumptions(tco_years=3)
        assumptions_5y = Assumptions(tco_years=5)
        result_3y = calculate_hardware_tco(
            cascade, workload, xeon6_profile, assumptions_3y
        )
        result_5y = calculate_hardware_tco(
            cascade, workload, xeon6_profile, assumptions_5y
        )
        # 5-year should have higher power cost component
        assert result_5y.three_year_tco_usd > result_3y.three_year_tco_usd


# --- Full comparison tests ---

class TestFullComparison:
    """Test the full comparison pipeline."""

    def test_full_comparison_returns_all_hardware(self, hardware_profiles):
        """Full comparison returns a result for each hardware profile."""
        workload = make_workload(daily_volume=100000)
        result = calculate_full_comparison(workload, hardware_profiles, DEFAULT_ASSUMPTIONS)
        assert len(result.comparisons) == len(hardware_profiles["profiles"])

    def test_full_comparison_has_cascade_summary(self, hardware_profiles):
        """Full comparison includes cascade summary."""
        workload = make_workload(daily_volume=100000)
        result = calculate_full_comparison(workload, hardware_profiles, DEFAULT_ASSUMPTIONS)
        assert result.cascade_summary is not None
        assert result.cascade_summary.total_signals_per_day == 100000

    def test_full_comparison_workload_metadata(self, hardware_profiles):
        """Full comparison includes workload metadata."""
        workload = make_workload(daily_volume=100000)
        result = calculate_full_comparison(workload, hardware_profiles, DEFAULT_ASSUMPTIONS)
        assert result.workload_id == "test-workload"
        assert result.workload_name == "Test Workload"
        assert result.daily_volume == 100000
