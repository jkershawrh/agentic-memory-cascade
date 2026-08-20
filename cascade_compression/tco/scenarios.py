"""Pre-built FSI scenarios for the TCO calculator.

Each scenario combines a workload profile with specific assumptions
for a realistic sales conversation.
"""

from .models import Assumptions, Scenario


def get_scenarios() -> list[Scenario]:
    """Return pre-built FSI scenarios."""
    return [
        Scenario(
            id="amex-dispute-resolution",
            name="Large Card Issuer Dispute Resolution",
            description=(
                "500K disputes/day at a major card issuer. 85% of disputes "
                "follow standard patterns (wrong amount, duplicate charge) that "
                "rules engines handle. 12% need classification by a small model. "
                "3% are complex multi-party disputes requiring deeper analysis. "
                "Assumes US average power cost and 3-year TCO window."
            ),
            workload_id="dispute-resolution",
            assumptions=Assumptions(
                power_cost_per_kwh=0.10,
                hours_per_year=8760,
                tco_years=3,
                idle_hardware=False,
            ),
        ),
        Scenario(
            id="midsize-bank-fraud-triage",
            name="Mid-Size Bank Fraud Triage",
            description=(
                "1M transactions/day screened for fraud. 92% are clearly "
                "legitimate (amount, merchant, location all match patterns). "
                "6% need model-based risk scoring. 2% are genuinely suspicious "
                "and need deeper analysis. Customer already owns Xeon servers "
                "in their data center — marginal cost is just power."
            ),
            workload_id="fraud-case-triage",
            assumptions=Assumptions(
                power_cost_per_kwh=0.10,
                hours_per_year=8760,
                tco_years=3,
                idle_hardware=True,
            ),
        ),
        Scenario(
            id="insurance-compliance-screening",
            name="Insurance Compliance Screening",
            description=(
                "200K communications/day screened for regulatory compliance. "
                "Higher complexity ratio than other workloads — regulatory "
                "language is nuanced. 75% routine, 18% ambiguous, 7% complex. "
                "Assumes higher power cost (data center in California)."
            ),
            workload_id="compliance-screening",
            assumptions=Assumptions(
                power_cost_per_kwh=0.15,
                hours_per_year=8760,
                tco_years=3,
                idle_hardware=False,
            ),
        ),
        Scenario(
            id="mortgage-lender-intake",
            name="Mortgage Lender Document Intake",
            description=(
                "50K loan documents/day. Longer documents (500 input tokens) "
                "with more complex extraction. 70% routine (standard forms), "
                "22% need model parsing, 8% are complex edge cases. "
                "5-year TCO window to match equipment depreciation schedule."
            ),
            workload_id="loan-document-intake",
            assumptions=Assumptions(
                power_cost_per_kwh=0.10,
                hours_per_year=8760,
                tco_years=5,
                idle_hardware=False,
            ),
        ),
    ]
