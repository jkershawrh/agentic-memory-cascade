"""Synthetic retail signal generator for cascade benchmarking.

Generates GS1 GPC-style retail events:
- Normal: POS transactions, inventory receipts, loyalty check-ins, price changes
- Shrinkage: return fraud, sweethearting, ticket switching, employee theft
- Operational: stockouts, planogram violations, register discrepancies
- Compliance: age-restricted sales, recall alerts, food safety violations
"""

import random
from dataclasses import dataclass, asdict
from typing import List


@dataclass
class SyntheticRetailEvent:
    id: int
    store_id: str
    register_id: str
    event_type: str
    department: str
    sku: str
    amount: float
    quantity: int
    employee_id: str
    customer_id: str
    description: str
    label: str = "normal"
    label_detail: str = ""


STORES = [f"STR-{i:04d}" for i in range(200)]
DEPARTMENTS = ["Grocery", "Electronics", "Apparel", "Home", "Pharmacy", "Produce", "Deli", "Garden"]
EMPLOYEES = [f"EMP-{i:04d}" for i in range(500)]


def generate(count: int = 100_000, seed: int = 42) -> List[SyntheticRetailEvent]:
    rng = random.Random(seed)
    events = []
    eid = 0

    # Real-world ratios: NRF 2023 (1.6% shrinkage), NRF 2025 (36% external theft)
    normal_count = int(count * 0.9800)
    shrinkage_count = max(1, int(count * 0.0160))
    operational_count = max(1, int(count * 0.0020))
    compliance_count = max(1, count - normal_count - shrinkage_count - operational_count)

    for _ in range(normal_count):
        eid += 1
        etype = rng.choice(["pos_sale", "pos_sale", "pos_sale", "pos_sale",
                            "inventory_receipt", "loyalty_checkin", "price_change",
                            "pos_return", "markdown_applied"])
        store = rng.choice(STORES)
        dept = rng.choice(DEPARTMENTS)
        amount = round(rng.uniform(1.50, 300.00), 2)
        qty = rng.randint(1, 5)

        events.append(SyntheticRetailEvent(
            id=eid, store_id=store, register_id=f"REG-{rng.randint(1,12)}",
            event_type=etype, department=dept,
            sku=f"SKU-{rng.randint(10000,99999)}", amount=amount, quantity=qty,
            employee_id=rng.choice(EMPLOYEES),
            customer_id=f"LOYALTY-{rng.randint(1000,9999)}" if etype == "loyalty_checkin" else "",
            description=f"Routine {etype.replace('_', ' ')}",
            label="normal",
        ))

    for _ in range(shrinkage_count):
        eid += 1
        shrink_type = rng.choice(["return_fraud", "sweethearting", "ticket_switch",
                                  "employee_theft", "refund_abuse", "wardrobing"])
        store = rng.choice(STORES)
        emp = rng.choice(EMPLOYEES)

        if shrink_type == "return_fraud":
            amount = round(rng.uniform(50, 500), 2)
            desc = "Return without receipt, high-value item, no original payment method"
            detail = "return_fraud_no_receipt"
        elif shrink_type == "sweethearting":
            amount = round(rng.uniform(20, 200), 2)
            desc = "Items scanned but voided, employee-customer pattern, repeat offender register"
            detail = "sweethearting_void_pattern"
        elif shrink_type == "ticket_switch":
            amount = round(rng.uniform(100, 800), 2)
            desc = "Barcode mismatch, high-value item rung as clearance, weight discrepancy"
            detail = "ticket_switch"
        elif shrink_type == "employee_theft":
            amount = round(rng.uniform(50, 1000), 2)
            desc = "After-hours register access, no customer present, cash drawer discrepancy"
            detail = "employee_theft"
        elif shrink_type == "refund_abuse":
            amount = round(rng.uniform(30, 300), 2)
            desc = "Multiple refunds same day, refund to different payment method, pattern customer"
            detail = "refund_abuse"
        else:
            amount = round(rng.uniform(100, 500), 2)
            desc = "Purchase and return of worn item, tags reattached, seasonal merchandise"
            detail = "wardrobing"

        events.append(SyntheticRetailEvent(
            id=eid, store_id=store, register_id=f"REG-{rng.randint(1,12)}",
            event_type="pos_return" if "return" in shrink_type or shrink_type == "wardrobing" else "pos_sale",
            department=rng.choice(["Electronics", "Apparel", "Home"]),
            sku=f"SKU-{rng.randint(10000,99999)}", amount=amount, quantity=1,
            employee_id=emp, customer_id="",
            description=desc, label="shrinkage", label_detail=detail,
        ))

    for _ in range(operational_count):
        eid += 1
        op_type = rng.choice(["stockout", "planogram_violation", "register_discrepancy",
                               "delivery_delay", "temperature_alert"])
        store = rng.choice(STORES)

        if op_type == "stockout":
            desc = f"SKU out of stock, {rng.randint(3,14)} days since last receipt"
            detail = "stockout"
        elif op_type == "planogram_violation":
            desc = "Shelf arrangement doesn't match planogram, compliance audit flag"
            detail = "planogram_violation"
        elif op_type == "register_discrepancy":
            desc = f"End-of-day variance ${rng.uniform(5, 200):.2f}, exceeds threshold"
            detail = "register_discrepancy"
        elif op_type == "delivery_delay":
            desc = f"Scheduled delivery {rng.randint(1,3)} days late"
            detail = "delivery_delay"
        else:
            desc = f"Cooler temperature {rng.uniform(42, 55):.1f}F, exceeds 41F limit"
            detail = "temperature_alert"

        events.append(SyntheticRetailEvent(
            id=eid, store_id=store, register_id="",
            event_type=op_type, department=rng.choice(DEPARTMENTS),
            sku="", amount=0, quantity=0,
            employee_id="", customer_id="",
            description=desc, label="operational", label_detail=detail,
        ))

    for _ in range(compliance_count):
        eid += 1
        comp_type = rng.choice(["age_restricted_sale", "recall_alert", "food_safety",
                                 "weight_measure_violation"])
        store = rng.choice(STORES)

        if comp_type == "age_restricted_sale":
            desc = rng.choice(["Alcohol sale, no ID verification recorded",
                               "Tobacco sale to minor flagged", "Age-restricted product, override used"])
            detail = "age_restricted"
        elif comp_type == "recall_alert":
            desc = f"SKU-{rng.randint(10000,99999)} on active recall list, still on shelf"
            detail = "product_recall"
        elif comp_type == "food_safety":
            desc = rng.choice(["Deli temperature log gap", "Expired product on shelf",
                               "Cross-contamination risk flagged"])
            detail = "food_safety"
        else:
            desc = "Scale calibration overdue, produce weights may be inaccurate"
            detail = "weight_measure"

        events.append(SyntheticRetailEvent(
            id=eid, store_id=store, register_id="",
            event_type=comp_type, department=rng.choice(["Grocery", "Produce", "Deli", "Pharmacy"]),
            sku=f"SKU-{rng.randint(10000,99999)}", amount=0, quantity=0,
            employee_id=rng.choice(EMPLOYEES), customer_id="",
            description=desc, label="compliance", label_detail=detail,
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
    print(f"Generated {s['total']} retail events:")
    for label, count in sorted(s["by_label"].items()):
        print(f"  {label}: {count}")
