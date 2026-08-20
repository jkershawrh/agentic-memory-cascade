"""Synthetic financial transaction generator for cascade benchmarking.

Generates labeled transactions with realistic patterns:
- Normal: recurring merchants, regular amounts, domestic
- Fraud: first-time international wires, rapid small charges, amount anomalies
- Dispute: double charges, unauthorized, merchant disputes
- Compliance: structuring (sub-$10K deposits), sanctions patterns
"""

import hashlib
import json
import random
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class SyntheticTransaction:
    id: int
    account_id: str
    amount: float
    merchant: str
    merchant_category: str
    location: str
    country: str
    transaction_type: str
    channel: str
    timestamp_offset_hours: float
    is_recurring: bool = False
    is_first_time_merchant: bool = False
    is_first_time_country: bool = False
    velocity_1h: int = 1
    velocity_24h: int = 1
    label: str = "normal"
    label_detail: str = ""


RECURRING_MERCHANTS = [
    ("Netflix", "streaming", "US", 15.99),
    ("Spotify", "streaming", "US", 9.99),
    ("AT&T Wireless", "telecom", "US", 85.00),
    ("Comcast", "telecom", "US", 120.00),
    ("State Farm Insurance", "insurance", "US", 175.00),
    ("Planet Fitness", "fitness", "US", 24.99),
    ("Amazon Prime", "ecommerce", "US", 14.99),
    ("Adobe Creative", "software", "US", 54.99),
    ("Allstate", "insurance", "US", 142.00),
    ("Verizon", "telecom", "US", 95.00),
]

NORMAL_MERCHANTS = [
    ("Walmart", "retail", "US"),
    ("Target", "retail", "US"),
    ("Kroger", "grocery", "US"),
    ("Shell Gas", "fuel", "US"),
    ("Starbucks", "food", "US"),
    ("McDonald's", "food", "US"),
    ("CVS Pharmacy", "pharmacy", "US"),
    ("Home Depot", "hardware", "US"),
    ("Costco", "wholesale", "US"),
    ("Walgreens", "pharmacy", "US"),
    ("Publix", "grocery", "US"),
    ("Chick-fil-A", "food", "US"),
    ("Uber", "rideshare", "US"),
    ("DoorDash", "delivery", "US"),
    ("Best Buy", "electronics", "US"),
]

FRAUD_MERCHANTS = [
    ("QuickCash ATM", "atm", "NG"),
    ("GlobalPay Transfer", "money_transfer", "RO"),
    ("CryptoExchange Ltd", "crypto", "KY"),
    ("FastWire Services", "money_transfer", "HK"),
    ("TechStore Online", "electronics", "CN"),
]

ACCOUNTS = [f"ACCT-{i:05d}" for i in range(500)]


def generate(count: int = 100_000, seed: int = 42) -> List[SyntheticTransaction]:
    rng = random.Random(seed)
    transactions = []
    txn_id = 0

    # Real-world ratios: Nilson Report (0.064% fraud), FinCEN FY2024 (<0.01% SAR)
    normal_count = int(count * 0.9970)
    fraud_count = max(1, int(count * 0.0006))
    dispute_count = max(1, int(count * 0.0015))
    compliance_count = max(1, count - normal_count - fraud_count - dispute_count)

    # Normal transactions (92%)
    for _ in range(normal_count):
        txn_id += 1
        account = rng.choice(ACCOUNTS)

        if rng.random() < 0.3:
            merchant, cat, country, amount = rng.choice(RECURRING_MERCHANTS)
            is_recurring = True
            is_first_merchant = False
        else:
            merchant, cat, country = rng.choice(NORMAL_MERCHANTS)
            amount = round(rng.uniform(3.0, 250.0), 2)
            is_recurring = False
            is_first_merchant = rng.random() < 0.05

        transactions.append(SyntheticTransaction(
            id=txn_id, account_id=account, amount=amount,
            merchant=merchant, merchant_category=cat,
            location=rng.choice(["New York", "Chicago", "Dallas", "Miami", "Seattle", "Denver", "Atlanta"]),
            country=country,
            transaction_type=rng.choice(["card_purchase", "card_purchase", "card_purchase", "ach_debit"]),
            channel=rng.choice(["pos", "online", "mobile"]),
            timestamp_offset_hours=rng.uniform(0, count / 100),
            is_recurring=is_recurring,
            is_first_time_merchant=is_first_merchant,
            velocity_1h=rng.randint(1, 3),
            velocity_24h=rng.randint(1, 8),
            label="normal",
        ))

    # Fraud transactions (3%)
    for _ in range(fraud_count):
        txn_id += 1
        account = rng.choice(ACCOUNTS)
        fraud_type = rng.choice(["international_wire", "rapid_test", "amount_anomaly", "stolen_card", "account_takeover"])

        if fraud_type == "international_wire":
            merchant, cat, country = rng.choice(FRAUD_MERCHANTS)
            amount = round(rng.uniform(5000, 50000), 2)
            transactions.append(SyntheticTransaction(
                id=txn_id, account_id=account, amount=amount,
                merchant=merchant, merchant_category=cat,
                location="Unknown", country=country,
                transaction_type="wire_transfer",
                channel="online",
                timestamp_offset_hours=rng.uniform(0, count / 100),
                is_first_time_merchant=True, is_first_time_country=True,
                velocity_1h=1, velocity_24h=1,
                label="fraud", label_detail="international_wire_first_time",
            ))
        elif fraud_type == "rapid_test":
            for j in range(rng.randint(3, 8)):
                txn_id += 1
                transactions.append(SyntheticTransaction(
                    id=txn_id, account_id=account,
                    amount=round(rng.uniform(0.50, 5.00), 2),
                    merchant=rng.choice(NORMAL_MERCHANTS)[0],
                    merchant_category="retail", location="Online", country="US",
                    transaction_type="card_purchase", channel="online",
                    timestamp_offset_hours=rng.uniform(0, count / 100),
                    is_first_time_merchant=True,
                    velocity_1h=j + 1, velocity_24h=j + 1,
                    label="fraud", label_detail="rapid_small_charges",
                ))
        elif fraud_type == "amount_anomaly":
            transactions.append(SyntheticTransaction(
                id=txn_id, account_id=account,
                amount=round(rng.uniform(2000, 15000), 2),
                merchant=rng.choice(NORMAL_MERCHANTS)[0],
                merchant_category="retail", location="Unknown", country="US",
                transaction_type="card_purchase", channel="online",
                timestamp_offset_hours=rng.uniform(0, count / 100),
                is_first_time_merchant=True,
                velocity_1h=1, velocity_24h=rng.randint(5, 15),
                label="fraud", label_detail="amount_anomaly",
            ))
        elif fraud_type == "stolen_card":
            for j in range(rng.randint(3, 6)):
                txn_id += 1
                transactions.append(SyntheticTransaction(
                    id=txn_id, account_id=account,
                    amount=round(rng.uniform(100, 1500), 2),
                    merchant=rng.choice(NORMAL_MERCHANTS)[0],
                    merchant_category="retail",
                    location=rng.choice(["Unknown", "Lagos", "Bucharest", "São Paulo"]),
                    country=rng.choice(["NG", "RO", "BR"]),
                    transaction_type="card_purchase", channel="online",
                    timestamp_offset_hours=rng.uniform(0, count / 100),
                    is_first_time_merchant=True, is_first_time_country=True,
                    velocity_1h=j + 1, velocity_24h=j + 3,
                    label="fraud", label_detail="stolen_card_foreign",
                ))
        else:
            transactions.append(SyntheticTransaction(
                id=txn_id, account_id=account,
                amount=round(rng.uniform(500, 10000), 2),
                merchant="Online Transfer", merchant_category="transfer",
                location="Online", country="US",
                transaction_type="ach_credit", channel="online",
                timestamp_offset_hours=rng.uniform(0, count / 100),
                velocity_1h=rng.randint(3, 8), velocity_24h=rng.randint(10, 20),
                label="fraud", label_detail="account_takeover_transfer",
            ))

    # Dispute transactions (3%)
    for _ in range(dispute_count):
        txn_id += 1
        account = rng.choice(ACCOUNTS)
        dispute_type = rng.choice(["double_charge", "unauthorized", "merchant_dispute"])

        if dispute_type == "double_charge":
            merchant = rng.choice(NORMAL_MERCHANTS)[0]
            amount = round(rng.uniform(20, 500), 2)
            for j in range(2):
                txn_id += 1
                transactions.append(SyntheticTransaction(
                    id=txn_id, account_id=account, amount=amount,
                    merchant=merchant, merchant_category="retail",
                    location="Chicago", country="US",
                    transaction_type="card_purchase", channel="pos",
                    timestamp_offset_hours=rng.uniform(0, count / 100),
                    velocity_1h=2, velocity_24h=3,
                    label="dispute", label_detail="double_charge",
                ))
        elif dispute_type == "unauthorized":
            transactions.append(SyntheticTransaction(
                id=txn_id, account_id=account,
                amount=round(rng.uniform(50, 2000), 2),
                merchant=rng.choice(NORMAL_MERCHANTS)[0],
                merchant_category="retail", location="Unknown", country="US",
                transaction_type="card_purchase", channel="online",
                timestamp_offset_hours=rng.uniform(0, count / 100),
                is_first_time_merchant=True,
                label="dispute", label_detail="unauthorized_charge",
            ))
        else:
            transactions.append(SyntheticTransaction(
                id=txn_id, account_id=account,
                amount=round(rng.uniform(100, 1000), 2),
                merchant=rng.choice(NORMAL_MERCHANTS)[0],
                merchant_category="retail", location="Dallas", country="US",
                transaction_type="card_purchase", channel="pos",
                timestamp_offset_hours=rng.uniform(0, count / 100),
                label="dispute", label_detail="merchant_dispute",
            ))

    # Compliance transactions (2%)
    for _ in range(compliance_count):
        txn_id += 1
        account = rng.choice(ACCOUNTS)
        comp_type = rng.choice(["structuring", "structuring", "sanctions_adjacent"])

        if comp_type == "structuring":
            for j in range(rng.randint(3, 6)):
                txn_id += 1
                transactions.append(SyntheticTransaction(
                    id=txn_id, account_id=account,
                    amount=round(rng.uniform(8000, 9900), 2),
                    merchant="Bank Deposit", merchant_category="banking",
                    location=rng.choice(["New York", "Miami", "Chicago"]),
                    country="US",
                    transaction_type="cash_deposit",
                    channel="branch",
                    timestamp_offset_hours=rng.uniform(0, count / 100),
                    velocity_1h=1, velocity_24h=j + 1,
                    label="compliance", label_detail="structuring_sub_10k",
                ))
        else:
            transactions.append(SyntheticTransaction(
                id=txn_id, account_id=account,
                amount=round(rng.uniform(1000, 25000), 2),
                merchant="International Wire", merchant_category="wire",
                location="Online", country=rng.choice(["IR", "KP", "SY", "CU"]),
                transaction_type="wire_transfer", channel="online",
                timestamp_offset_hours=rng.uniform(0, count / 100),
                is_first_time_country=True,
                label="compliance", label_detail="sanctions_adjacent_country",
            ))

    rng.shuffle(transactions)

    for i, txn in enumerate(transactions):
        txn.id = i + 1

    return transactions


def save_to_json(transactions: List[SyntheticTransaction], path: str):
    with open(path, "w") as f:
        json.dump([asdict(t) for t in transactions], f, indent=2)


def label_summary(transactions: List[SyntheticTransaction]) -> dict:
    counts = {}
    details = {}
    for t in transactions:
        counts[t.label] = counts.get(t.label, 0) + 1
        if t.label_detail:
            details[t.label_detail] = details.get(t.label_detail, 0) + 1
    return {"total": len(transactions), "by_label": counts, "by_detail": details}


if __name__ == "__main__":
    txns = generate(100_000)
    summary = label_summary(txns)
    print(f"Generated {summary['total']} transactions:")
    for label, count in sorted(summary["by_label"].items()):
        print(f"  {label}: {count}")
    print(f"\nDetail breakdown:")
    for detail, count in sorted(summary["by_detail"].items(), key=lambda x: -x[1]):
        print(f"  {detail}: {count}")
