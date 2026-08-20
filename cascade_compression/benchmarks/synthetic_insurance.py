"""Synthetic insurance signal generator for cascade benchmarking.

Generates ACORD-style insurance events:
- Normal: routine claims, policy renewals, premium payments, endorsements
- Fraud: staged accidents, inflated claims, phantom providers, identity fraud
- Compliance: licensing gaps, reserve deficiencies, regulatory filings
- Operational: SLA breaches, adjuster workload, reinsurance triggers
"""

import random
from dataclasses import dataclass, asdict
from typing import List


@dataclass
class SyntheticInsuranceEvent:
    id: int
    policy_id: str
    claim_id: str
    event_type: str
    line_of_business: str
    amount: float
    claimant: str
    provider: str
    adjuster: str
    state: str
    description: str
    label: str = "normal"
    label_detail: str = ""


LINES = ["auto", "homeowners", "workers_comp", "commercial_liability", "health", "life"]
STATES = ["CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "NC", "MI"]
ADJUSTERS = [f"ADJ-{i:03d}" for i in range(50)]
PROVIDERS = ["CityMedical", "StateFarm Body Shop", "AllCare Clinic", "Metro Hospital",
             "Regional Rehab Center", "Downtown Dental", "Sunset Chiropractic",
             "Valley Auto Repair", "Lakeside Orthopedics", "Heritage Restoration"]
FRAUD_PROVIDERS = ["QuickFix Auto", "CashCare Medical", "FastClaim Services",
                   "NoWait Rehab", "PhantomCare LLC"]


def generate(count: int = 100_000, seed: int = 42) -> List[SyntheticInsuranceEvent]:
    rng = random.Random(seed)
    events = []
    eid = 0

    # Real-world ratios: NAIC/FBI (~10% flagged, 1-2.5% investigated), Coalition Against Insurance Fraud
    normal_count = int(count * 0.9750)
    fraud_count = max(1, int(count * 0.0100))
    compliance_count = max(1, int(count * 0.0050))
    operational_count = max(1, count - normal_count - fraud_count - compliance_count)

    for _ in range(normal_count):
        eid += 1
        etype = rng.choice(["claim_filed", "claim_filed", "claim_filed",
                            "policy_renewal", "premium_payment", "endorsement",
                            "claim_closed", "inspection_completed"])
        lob = rng.choice(LINES)
        amount = round(rng.uniform(200, 15000), 2) if "claim" in etype else round(rng.uniform(50, 3000), 2)

        events.append(SyntheticInsuranceEvent(
            id=eid, policy_id=f"POL-{rng.randint(10000,99999)}",
            claim_id=f"CLM-{rng.randint(100000,999999)}" if "claim" in etype else "",
            event_type=etype, line_of_business=lob, amount=amount,
            claimant=f"Customer-{rng.randint(1000,9999)}",
            provider=rng.choice(PROVIDERS), adjuster=rng.choice(ADJUSTERS),
            state=rng.choice(STATES),
            description=f"Routine {etype.replace('_', ' ')} for {lob}",
            label="normal",
        ))

    for _ in range(fraud_count):
        eid += 1
        fraud_type = rng.choice(["staged_accident", "inflated_claim", "phantom_provider",
                                 "identity_fraud", "premium_fraud", "claim_padding"])
        lob = rng.choice(["auto", "workers_comp", "health"])

        if fraud_type == "staged_accident":
            amount = round(rng.uniform(15000, 75000), 2)
            desc = "Multiple claimants, soft tissue injuries only, pre-existing attorney"
            detail = "staged_accident"
        elif fraud_type == "inflated_claim":
            amount = round(rng.uniform(10000, 50000), 2)
            desc = "Repair estimate 3x market value, aftermarket parts billed as OEM"
            detail = "inflated_claim"
        elif fraud_type == "phantom_provider":
            amount = round(rng.uniform(5000, 30000), 2)
            desc = "Provider address is PO Box, no NPI on file, billing for impossible visit volume"
            detail = "phantom_provider"
        elif fraud_type == "identity_fraud":
            amount = round(rng.uniform(5000, 25000), 2)
            desc = "Policy opened 30 days before claim, SSN mismatch, VIN duplicated across policies"
            detail = "identity_fraud"
        elif fraud_type == "premium_fraud":
            amount = round(rng.uniform(1000, 8000), 2)
            desc = "Business classified as clerical, actual operations are manufacturing"
            detail = "premium_fraud"
        else:
            amount = round(rng.uniform(3000, 20000), 2)
            desc = "Unrelated charges bundled, duplicate billing codes, upcoded procedures"
            detail = "claim_padding"

        events.append(SyntheticInsuranceEvent(
            id=eid, policy_id=f"POL-{rng.randint(10000,99999)}",
            claim_id=f"CLM-{rng.randint(100000,999999)}",
            event_type="claim_filed", line_of_business=lob, amount=amount,
            claimant=f"Customer-{rng.randint(1000,9999)}",
            provider=rng.choice(FRAUD_PROVIDERS) if fraud_type == "phantom_provider" else rng.choice(PROVIDERS),
            adjuster=rng.choice(ADJUSTERS), state=rng.choice(STATES),
            description=desc, label="fraud", label_detail=detail,
        ))

    for _ in range(compliance_count):
        eid += 1
        comp_type = rng.choice(["licensing_gap", "reserve_deficiency", "regulatory_filing",
                                "market_conduct", "solvency_warning"])
        state = rng.choice(STATES)
        lob = rng.choice(LINES)
        events.append(SyntheticInsuranceEvent(
            id=eid, policy_id=f"REG-{state}-{rng.randint(1000,9999)}", claim_id="",
            event_type=comp_type, line_of_business=lob,
            amount=round(rng.uniform(10000, 500000), 2) if "reserve" in comp_type else 0,
            claimant="", provider="", adjuster="",
            state=state,
            description=f"{comp_type.replace('_', ' ').title()} — {lob} in {state}",
            label="compliance", label_detail=comp_type,
        ))

    for _ in range(operational_count):
        eid += 1
        op_type = rng.choice(["sla_breach", "adjuster_overload", "reinsurance_trigger",
                               "system_outage", "backlog_alert"])
        events.append(SyntheticInsuranceEvent(
            id=eid, policy_id="", claim_id="",
            event_type=op_type, line_of_business=rng.choice(LINES),
            amount=0, claimant="", provider="",
            adjuster=rng.choice(ADJUSTERS) if "adjuster" in op_type else "",
            state=rng.choice(STATES),
            description=f"{op_type.replace('_', ' ').title()}",
            label="operational", label_detail=op_type,
        ))

    rng.shuffle(events)
    for i, e in enumerate(events):
        e.id = i + 1
    return events


def label_summary(events):
    counts = {}
    for e in events:
        counts[e.label] = counts.get(e.label, 0) + 1
    return {"total": len(events), "by_label": counts}


if __name__ == "__main__":
    evts = generate(100_000)
    s = label_summary(evts)
    print(f"Generated {s['total']} insurance events:")
    for label, count in sorted(s["by_label"].items()):
        print(f"  {label}: {count}")
