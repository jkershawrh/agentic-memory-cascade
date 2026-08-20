"""Synthetic corpus generator for benchmark workloads.

Generates varied inputs for each industry/task so benchmarks measure
quality across diverse data, not one hardcoded sample.

Usage:
  python -m benchmarks.corpus --size 100 --industries all
  python -m benchmarks.corpus --size 1000 --industries fsi,healthcare
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

CORPORA_DIR = Path(__file__).parent / "corpora"

# ── Shared pools ──────────────────────────────────────────────────

PERSON_NAMES = [
    "Sarah Chen", "Marcus Thompson", "Priya Patel", "James O'Brien",
    "Maria Rodriguez", "David Kim", "Emily Watson", "Ahmed Hassan",
    "Lisa Nakamura", "Robert Johansson", "Fatima Al-Rashid", "Carlos Mendez",
    "Anna Kowalski", "Wei Zhang", "Grace Okonkwo", "Thomas Mueller",
]

ATTENDING_NAMES = [
    "Dr. Patel", "Dr. Rivera", "Dr. Chang", "Dr. Williams",
    "Dr. Singh", "Dr. Anderson", "Dr. Moreau", "Dr. Tanaka",
]

# ── FSI ───────────────────────────────────────────────────────────

FSI_MERCHANTS = [
    "AMZN Digital", "Apple.com", "Uber *Trip", "Netflix.com",
    "Shell Oil", "Whole Foods", "Target", "Home Depot",
    "Delta Airlines", "Marriott Hotels", "Starbucks", "DoorDash",
    "Steam Games", "Adobe Creative", "Spotify Premium", "AT&T Wireless",
]

FSI_DISPUTE_REASONS = [
    "does not recognize the merchant",
    "was charged twice for the same transaction",
    "cancelled the subscription but was still charged",
    "returned the item but has not received a refund",
    "was charged a different amount than expected",
    "did not authorize this transaction",
    "card was lost/stolen and a fraudulent charge appeared",
    "received the wrong item and was charged full price",
]

CAYMAN_RECIPIENTS = [
    "Caribbean Holdings Ltd", "Pacific Trust Services", "Island Capital Group",
    "Meridian Offshore Inc", "Azure Financial Partners",
]


def gen_fsi_dispute(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    merchant = random.choice(FSI_MERCHANTS)
    amount = round(random.uniform(15, 2500), 2)
    reason = random.choice(FSI_DISPUTE_REASONS)
    day = random.randint(1, 28)
    text = (
        f"Customer {name} reports charge of ${amount} from '{merchant}' on 2026-07-{day:02d}. "
        f"Customer states they {reason}. "
        f"Card was {'not in their possession' if random.random() < 0.2 else 'in their possession'}. "
        f"{'Recent travel reported.' if random.random() < 0.15 else 'No recent travel.'}"
    )
    return {"id": f"dispute-{i}", "task": "dispute-classification", "text": text}


def gen_fsi_fraud(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    amount = round(random.uniform(100, 50000), 2)
    tx_type = random.choice(["wire transfer", "ACH payment", "card purchase", "crypto transfer"])
    dest = random.choice(["offshore account", "new domestic account", "business account", "crypto wallet"])
    hour = random.randint(0, 23)
    avg_balance = random.randint(1500, 50000)
    flags = []
    if random.random() < 0.3:
        flags.append("VPN detected")
    if random.random() < 0.2:
        flags.append("new device")
    if random.random() < 0.25:
        flags.append("first international transfer")
    if hour < 5:
        flags.append(f"unusual hour ({hour}:00 AM)")
    flag_str = ", ".join(flags) if flags else "no anomalous signals"
    text = (
        f"Transaction: ${amount:,.2f} {tx_type} from personal checking to {dest}. "
        f"Customer profile: {name}, avg monthly balance ${avg_balance:,}. "
        f"Time: {hour}:{random.randint(0,59):02d}. Flags: {flag_str}."
    )
    return {"id": f"fraud-{i}", "task": "fraud-scoring", "text": text}


def gen_fsi_compliance(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    country = random.choice(["Germany", "UK", "Switzerland", "Singapore", "UAE", "Brazil"])
    recipient = random.choice(CAYMAN_RECIPIENTS)
    dest = random.choice(["Cayman Islands", "British Virgin Islands", "Panama", "Luxembourg"])
    amounts = [round(random.uniform(7000, 9900), 2) for _ in range(random.randint(2, 5))]
    amounts_str = ", ".join(f"${a:,.2f}" for a in amounts)
    text = (
        f"A customer in {country} transfers ${amounts[0]:,.2f} to {recipient} in {dest}. "
        f"They have made {len(amounts)} similar transfers in the past month: {amounts_str}. "
        f"Customer is a {random.randint(30, 65)}-year-old "
        f"{random.choice(['import/export business owner', 'real estate investor', 'financial consultant', 'retired executive'])}. "
        f"Account opened {random.randint(1, 36)} months ago."
    )
    return {"id": f"compliance-{i}", "task": "compliance-screening", "text": text}


def gen_fsi_loan(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    loan_type = random.choice(["MORTGAGE", "AUTO", "PERSONAL", "BUSINESS"])
    amount = {
        "MORTGAGE": random.randint(200000, 800000),
        "AUTO": random.randint(15000, 65000),
        "PERSONAL": random.randint(5000, 50000),
        "BUSINESS": random.randint(50000, 500000),
    }[loan_type]
    income = random.randint(45000, 250000)
    credit = random.choice(["620-650", "650-700", "700-740", "740-780", "780-820"])
    missing = random.sample(["W-2 for 2025", "property appraisal", "bank statements", "tax returns", "proof of insurance", "employment verification"], k=random.randint(0, 3))
    missing_str = ", ".join(missing) if missing else "None"
    text = (
        f"APPLICATION FOR {loan_type} LOAN. Applicant: {name}. "
        f"Requested amount: ${amount:,}. "
        f"Employment: {random.choice(['Software Engineer', 'Nurse Practitioner', 'Sales Manager', 'Teacher', 'Accountant', 'Business Owner'])} "
        f"({random.randint(1, 15)} years). Annual income: ${income:,}. "
        f"Self-reported credit score: {credit}. "
        f"Missing documents: {missing_str}."
    )
    return {"id": f"loan-{i}", "task": "loan-document-extraction", "text": text}


# ── Healthcare ────────────────────────────────────────────────────

RECORD_TYPES = ["discharge_summary", "progress_note", "lab_report", "radiology_report"]

CONDITIONS = [
    "Type 2 Diabetes on Metformin 500mg", "Essential hypertension on Lisinopril 10mg",
    "COPD with acute exacerbation", "Coronary artery disease",
    "Chronic kidney disease stage 3", "Atrial fibrillation on Warfarin",
    "Primary osteoarthritis right knee", "Major depressive disorder",
    "Congestive heart failure", "Pneumonia with sepsis",
]

PROCEDURES = [
    "PCI to RCA with drug-eluting stent", "Total knee arthroplasty",
    "Bronchoscopy with BAL", "Cardiac catheterization",
    "CT angiogram of chest", "MRI brain with contrast",
    "Endoscopic retrograde cholangiopancreatography", "Lumbar puncture",
]

MEDS = [
    "Metformin 500mg", "Lisinopril 10mg", "Aspirin 81mg", "Clopidogrel 75mg",
    "Atorvastatin 40mg", "Omeprazole 20mg", "Amlodipine 5mg", "Furosemide 40mg",
    "Gabapentin 300mg", "Warfarin 5mg", "Levothyroxine 75mcg", "Prednisone 10mg",
]


def gen_healthcare_classify(i: int) -> dict:
    rec_type = random.choice(RECORD_TYPES)
    name = random.choice(PERSON_NAMES)
    age = random.randint(25, 88)
    gender = random.choice(["male", "female"])
    conds = random.sample(CONDITIONS, k=random.randint(1, 3))
    meds = random.sample(MEDS, k=random.randint(2, 5))
    proc = random.choice(PROCEDURES) if rec_type != "lab_report" else None

    if rec_type == "discharge_summary":
        text = f"DISCHARGE SUMMARY: {age}-year-old {gender} with {', '.join(conds)}. {f'Recent {proc}.' if proc else ''} Medications: {', '.join(meds)}."
    elif rec_type == "progress_note":
        text = f"PROGRESS NOTE: {name}, {age}yo {gender}. Current medications: {', '.join(meds)}. Assessment: {conds[0]} - stable. Plan: continue current regimen."
    elif rec_type == "lab_report":
        text = f"LAB REPORT: Patient {name}, {age}yo. WBC {random.uniform(3, 18):.1f}, Hgb {random.uniform(8, 17):.1f}, Plt {random.randint(100, 400)}, Creatinine {random.uniform(0.5, 4.0):.1f}, Troponin {random.uniform(0, 12):.2f}."
    else:
        text = f"RADIOLOGY REPORT: {random.choice(['CT', 'MRI', 'X-ray', 'Ultrasound'])} {random.choice(['chest', 'abdomen', 'brain', 'spine'])}. {name}, {age}yo {gender}. Findings: {random.choice(['No acute abnormality', 'Consolidation in right lower lobe', 'Small pleural effusion', '2cm hypodense lesion in liver'])}."

    return {"id": f"classify-{i}", "task": "clinical-classification", "text": text, "expected": rec_type}


def gen_healthcare_ner(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    age = random.randint(25, 88)
    gender = random.choice(["male", "female"])
    conds = random.sample(CONDITIONS, k=random.randint(2, 4))
    meds = random.sample(MEDS, k=random.randint(3, 6))
    proc = random.choice(PROCEDURES)
    text = (
        f"{age}-year-old {gender} with {', '.join(conds)}. "
        f"Recent {proc}. Current medications: {', '.join(meds)}."
    )
    return {"id": f"ner-{i}", "task": "medical-ner", "text": text, "expected_entities": meds[:2]}


def gen_healthcare_summary(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    age = random.randint(30, 85)
    gender = random.choice(["male", "female"])
    conds = random.sample(CONDITIONS, k=random.randint(2, 4))
    meds = random.sample(MEDS, k=random.randint(3, 5))
    proc = random.choice(PROCEDURES)
    text = (
        f"{age}-year-old {gender} admitted with {random.choice(['acute chest pain', 'shortness of breath', 'altered mental status', 'fall with hip pain'])}. "
        f"History of {', '.join(conds)}. "
        f"Emergent {proc}. "
        f"Current medications: {', '.join(meds)}. "
        f"Vitals stable. Plan: {random.choice(['discharge home', 'transfer to rehab', 'continue monitoring'])}."
    )
    return {"id": f"summary-{i}", "task": "clinical-summarization", "text": text}


# ── Insurance ─────────────────────────────────────────────────────

VEHICLE_MODELS = [
    "2024 Toyota Camry", "2023 Honda Civic", "2025 Tesla Model 3",
    "2022 Ford F-150", "2024 Chevrolet Equinox", "2023 BMW X3",
]

CLAIM_TYPES_DATA = [
    ("auto", "rear-end collision on I-35", "bumper, trunk lid, tail lights"),
    ("auto", "side-swipe in parking lot", "passenger door, mirror, quarter panel"),
    ("auto", "hail damage while parked", "hood, roof, windshield cracks"),
    ("property", "kitchen fire from unattended cooking", "cabinets, countertops, smoke damage throughout"),
    ("property", "burst pipe in second floor bathroom", "ceiling, walls, hardwood flooring"),
    ("property", "wind damage from severe thunderstorm", "roof shingles, siding, fence"),
    ("health", "emergency room visit for chest pain", "cardiac workup, overnight observation"),
    ("health", "outpatient knee surgery", "arthroscopic procedure, physical therapy"),
    ("liability", "customer slip and fall in store", "medical bills, potential litigation"),
]


def gen_insurance_claim(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    ct, incident, damage = random.choice(CLAIM_TYPES_DATA)
    vehicle = random.choice(VEHICLE_MODELS) if ct == "auto" else None
    est_cost = random.randint(500, 75000)
    injuries = random.choice(["No injuries reported", "Minor injuries reported", "Hospitalization required"])
    text = (
        f"Claimant {name} reports {incident} on 2026-07-{random.randint(1, 28):02d}. "
        f"{f'Vehicle: {vehicle}. ' if vehicle else ''}"
        f"Damage: {damage}. {injuries}. "
        f"Estimated cost: ${est_cost:,}. "
        f"{'Police report filed.' if random.random() < 0.6 else 'No police report.'}"
    )
    return {"id": f"claim-{i}", "task": "claims-triage", "text": text}


def gen_insurance_underwriting(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    age = random.randint(25, 70)
    gender = random.choice(["Male", "Female"])
    smoker = random.choice(["non-smoker", "former smoker (quit 5 years ago)", "current smoker"])
    bmi = round(random.uniform(19, 38), 1)
    conditions = random.sample([
        "hypertension (controlled)", "Type 2 diabetes", "asthma (mild)",
        "anxiety disorder", "hyperlipidemia", "none"
    ], k=random.randint(1, 3))
    family = random.sample([
        "father had MI at 61", "mother Type 2 diabetes", "sister breast cancer at 45",
        "no significant family history"
    ], k=random.randint(1, 2))
    coverage = random.choice([250000, 500000, 750000, 1000000, 2000000])
    term = random.choice([10, 15, 20, 30])
    text = (
        f"Life insurance application. Applicant: {gender}, {age}, {smoker}. BMI: {bmi}. "
        f"Conditions: {', '.join(conditions)}. Family history: {', '.join(family)}. "
        f"Occupation: {random.choice(['desk job', 'construction', 'healthcare worker', 'pilot', 'teacher'])}. "
        f"Requested coverage: ${coverage:,} term {term}yr."
    )
    return {"id": f"underwrite-{i}", "task": "underwriting-risk", "text": text}


def gen_insurance_policy(i: int) -> dict:
    policy_type = random.choice(["HOMEOWNERS", "AUTO", "COMMERCIAL", "UMBRELLA"])
    policy_num = f"HO-2026-{random.randint(1000000, 9999999)}"
    deductible = random.choice([500, 1000, 2500, 5000])
    premium = random.randint(800, 8000)
    coverage_a = random.randint(200000, 800000)
    text = (
        f"{policy_type} INSURANCE POLICY {policy_num}. "
        f"Effective 2026-01-{random.randint(1,28):02d} to 2027-01-{random.randint(1,28):02d}. "
        f"Coverage A (Dwelling): ${coverage_a:,}. "
        f"Deductible: ${deductible:,} all perils. "
        f"Exclusions: {', '.join(random.sample(['flood', 'earthquake', 'nuclear hazard', 'mold', 'war', 'intentional loss'], k=random.randint(2, 4)))}. "
        f"Annual Premium: ${premium:,}."
    )
    return {"id": f"policy-{i}", "task": "policy-extraction", "text": text}


# ── Retail ────────────────────────────────────────────────────────

PRODUCTS = [
    ("Sony WH-1000XM5 Wireless Headphones", "electronics", 349.99),
    ("Nike Air Max 270 Running Shoes", "clothing", 159.99),
    ("Instant Pot Duo 7-in-1 Pressure Cooker", "home", 89.99),
    ("LEGO Star Wars Millennium Falcon", "toys", 169.99),
    ("Dyson V15 Detect Cordless Vacuum", "home", 749.99),
    ("Apple AirPods Pro 2nd Gen", "electronics", 249.99),
    ("Patagonia Better Sweater Fleece", "clothing", 139.00),
    ("KitchenAid Stand Mixer", "home", 379.99),
    ("Samsung 65\" OLED 4K TV", "electronics", 1599.99),
    ("Osprey Atmos AG 65 Backpack", "sports", 289.99),
]

REVIEW_SENTIMENTS = [
    ("positive", "Absolutely love it. Best purchase I've made this year. Quality is outstanding and it arrived quickly."),
    ("positive", "Works exactly as described. Great value for the price. Would recommend to friends."),
    ("negative", "Broke after two weeks. Returning for refund. Very disappointed with the quality."),
    ("negative", "Not worth the price. Cheap materials, slow shipping, and poor customer service."),
    ("mixed", "Good product overall but the price is too high. Battery life is great but the case feels cheap."),
    ("mixed", "Love the features but had issues with setup. Once working, it's been solid. 3/5 stars."),
    ("neutral", "It's fine. Does what it says. Nothing special but nothing wrong either."),
]


def gen_retail_product(i: int) -> dict:
    name, dept, price = random.choice(PRODUCTS)
    text = (
        f"{name}. "
        f"Price: ${price}. "
        f"Available in {', '.join(random.sample(['Black', 'Silver', 'White', 'Blue', 'Red', 'Green'], k=random.randint(2, 4)))}. "
        f"{random.choice(['Free 2-day shipping.', 'Ships in 3-5 business days.', 'In stock, order within 2 hours for same-day delivery.'])}"
    )
    return {"id": f"product-{i}", "task": "product-categorization", "text": text}


def gen_retail_review(i: int) -> dict:
    name, dept, price = random.choice(PRODUCTS)
    sentiment, review_text = random.choice(REVIEW_SENTIMENTS)
    stars = {"positive": random.choice([4, 5]), "negative": random.choice([1, 2]), "mixed": 3, "neutral": 3}[sentiment]
    months = random.randint(1, 12)
    text = f"Product: {name}. Bought {months} months ago. {review_text} {stars}/5 stars."
    return {"id": f"review-{i}", "task": "review-sentiment", "text": text, "expected_sentiment": sentiment}


def gen_retail_demand(i: int) -> dict:
    name, dept, price = random.choice(PRODUCTS)
    sku = f"SKU-{random.randint(10000, 99999)}"
    stock = random.randint(5, 500)
    daily_sales = random.randint(1, 50)
    change = random.choice(["up 340%", "up 45%", "down 60%", "flat", "up 120%"])
    lead_time = random.choice([3, 7, 14, 21, 30])
    text = (
        f"{sku} ({name}): Current stock {stock} units. "
        f"Avg daily sales last 7d: {daily_sales} units ({change} from prior week). "
        f"Reorder lead time: {lead_time} days. "
        f"{random.choice(['Competitor showing limited stock.', 'Prime Day in 6 days.', 'Holiday season approaching.', 'No special events.'])}"
    )
    return {"id": f"demand-{i}", "task": "demand-classification", "text": text}


# ── Telecom ───────────────────────────────────────────────────────

TICKET_CATEGORIES = [
    ("outage", "complete loss of internet and phone service since this morning"),
    ("billing", "charged twice on this month's bill, $89.99 duplicate charge"),
    ("technical", "internet speed dropping to 2 Mbps during peak hours, paying for 500 Mbps"),
    ("equipment", "modem showing red light, rebooted 3 times with no change"),
    ("service_change", "wants to upgrade from 200 Mbps to 1 Gbps plan"),
    ("complaint", "technician no-showed for scheduled installation appointment"),
]

ACCOUNT_TYPES_TELECOM = ["residential", "business (dental office)", "business (law firm)", "enterprise"]


def gen_telecom_ticket(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    category, description = random.choice(TICKET_CATEGORIES)
    account = random.choice(ACCOUNT_TYPES_TELECOM)
    text = (
        f"Customer {name} ({account}) reports: {description}. "
        f"{'Neighbors also affected.' if category == 'outage' and random.random() < 0.4 else ''} "
        f"Account #{'BIZ' if 'business' in account else 'RES'}-2026-{random.randint(10000, 99999)}."
    )
    return {"id": f"ticket-{i}", "task": "ticket-routing", "text": text}


def gen_telecom_anomaly(i: int) -> dict:
    entries = []
    ts_base = f"2026-07-{random.randint(1, 28):02d} {random.randint(0, 23):02d}"
    anomaly = random.random() < 0.6
    if anomaly:
        anomaly_type = random.choice(["bgp_flap", "crc_errors", "packet_loss", "auth_failure"])
        if anomaly_type == "bgp_flap":
            entries.append(f"{ts_base}:12:44 [WARN] BGP peer 10.0.{random.randint(1,10)}.1 flap count: {random.randint(20, 100)} in last 300s (threshold: 5)")
        if anomaly_type == "crc_errors":
            entries.append(f"{ts_base}:12:45 [ERR] Interface ge-0/0/{random.randint(0,7)} CRC errors: {random.randint(500, 5000)}/min (baseline: <10/min)")
        if anomaly_type == "packet_loss":
            entries.append(f"{ts_base}:12:46 [WARN] Packet loss on trunk-east: {random.uniform(1, 15):.1f}% (SLA: <0.1%)")
        entries.append(f"{ts_base}:12:47 [INFO] Traffic volume {'normal' if random.random() < 0.7 else 'elevated'}. CPU utilization: {random.randint(10, 85)}%")
    else:
        entries.append(f"{ts_base}:12:44 [INFO] All BGP peers stable. No flaps.")
        entries.append(f"{ts_base}:12:45 [INFO] Interface errors within baseline.")
        entries.append(f"{ts_base}:12:46 [INFO] Packet loss: 0.01%. CPU: {random.randint(10, 30)}%.")
    text = "\n".join(entries)
    return {"id": f"anomaly-{i}", "task": "network-anomaly", "text": text, "has_anomaly": anomaly}


def gen_telecom_churn(i: int) -> dict:
    name = random.choice(PERSON_NAMES)
    months_in = random.randint(3, 36)
    plan_cost = random.choice([45, 65, 85, 105, 135])
    data_trend = random.choice(["dropped 60%", "stable", "increased 20%", "dropped 30%"])
    support_calls = random.randint(0, 8)
    nps = random.randint(1, 10)
    competitor_visit = random.random() < 0.3
    autopay = random.random() > 0.4
    lines = random.randint(1, 5)
    text = (
        f"Customer {months_in} months into contract. Plan: ${plan_cost}/mo. "
        f"Data usage trend: {data_trend} over 3 months. "
        f"Support calls last 30d: {support_calls}. NPS score: {nps}. "
        f"{'Visited competitor store. ' if competitor_visit else ''}"
        f"Payment: {'auto-pay' if autopay else 'manual'}. "
        f"Family plan: {lines} lines."
    )
    return {"id": f"churn-{i}", "task": "churn-prediction", "text": text}


# ── Generator registry ────────────────────────────────────────────

GENERATORS = {
    "fsi": {
        "dispute-classification": gen_fsi_dispute,
        "fraud-scoring": gen_fsi_fraud,
        "compliance-screening": gen_fsi_compliance,
        "loan-document-extraction": gen_fsi_loan,
    },
    "healthcare": {
        "clinical-classification": gen_healthcare_classify,
        "medical-ner": gen_healthcare_ner,
        "clinical-summarization": gen_healthcare_summary,
    },
    "insurance": {
        "claims-triage": gen_insurance_claim,
        "underwriting-risk": gen_insurance_underwriting,
        "policy-extraction": gen_insurance_policy,
    },
    "retail": {
        "product-categorization": gen_retail_product,
        "review-sentiment": gen_retail_review,
        "demand-classification": gen_retail_demand,
    },
    "telecom": {
        "ticket-routing": gen_telecom_ticket,
        "network-anomaly": gen_telecom_anomaly,
        "churn-prediction": gen_telecom_churn,
    },
}


def generate_corpus(industry: str, size: int, seed: int = 42) -> dict:
    """Generate a corpus for an industry with `size` records per task."""
    random.seed(seed)
    tasks = GENERATORS.get(industry, {})
    corpus = {"industry": industry, "size_per_task": size, "seed": seed, "tasks": {}}
    for task_id, gen_fn in tasks.items():
        corpus["tasks"][task_id] = [gen_fn(i) for i in range(size)]
    return corpus


def load_corpus(industry: str, size: int = 100) -> dict | None:
    """Load a pre-generated corpus from disk."""
    path = CORPORA_DIR / f"{industry}-{size}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def main():
    parser = argparse.ArgumentParser(description="Generate benchmark corpora")
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--industries", default="all")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    industries = args.industries.split(",") if args.industries != "all" else list(GENERATORS.keys())
    CORPORA_DIR.mkdir(exist_ok=True)

    for industry in industries:
        if industry not in GENERATORS:
            print(f"Unknown industry: {industry}")
            continue
        corpus = generate_corpus(industry, args.size, args.seed)
        path = CORPORA_DIR / f"{industry}-{args.size}.json"
        with open(path, "w") as f:
            json.dump(corpus, f, indent=2)
        total = sum(len(records) for records in corpus["tasks"].values())
        print(f"  {industry}: {total} records ({len(corpus['tasks'])} tasks) → {path}")


if __name__ == "__main__":
    main()
