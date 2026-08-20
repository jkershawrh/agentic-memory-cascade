"""Financial transaction collector.

Maps transactions to the cascade Signal protocol. Supports:
- Synthetic data from the generator (for benchmarking)
- JSON/CSV file replay
- Database polling (when configured)
"""

import csv
import json
import logging
from pathlib import Path
from typing import List

from .base import BaseCollector

log = logging.getLogger(__name__)

_FLOAT_FIELDS = {"amount"}
_INTEGER_FIELDS = {"velocity_1h", "velocity_24h"}
_BOOLEAN_FIELDS = {
    "is_recurring",
    "is_first_time_merchant",
    "is_first_time_country",
}


def _coerce_csv_row(row: dict) -> dict:
    """Convert CSV strings used by FinanceSignal into their runtime types."""
    converted = dict(row)
    for field in _FLOAT_FIELDS:
        if converted.get(field) not in (None, ""):
            converted[field] = float(converted[field])
    for field in _INTEGER_FIELDS:
        if converted.get(field) not in (None, ""):
            converted[field] = int(converted[field])
    for field in _BOOLEAN_FIELDS:
        if converted.get(field) not in (None, ""):
            converted[field] = str(converted[field]).strip().lower() in {
                "1", "true", "yes", "y"
            }
    return converted


class FinanceSignal:
    """Maps a financial transaction to the cascade Signal interface."""

    def __init__(self, txn: dict):
        self.signal_id = txn.get("id", 0)
        self.cluster_id = "finance"
        self.namespace = txn.get("account_id", "unknown")
        self.resource_kind = "transaction"
        self.resource_name = txn.get("merchant", "")
        self.signal_type = self._map_type(txn)
        self.severity = self._map_severity(txn)
        self.evidence = {
            "message": self._build_message(txn),
            "amount": txn.get("amount", 0),
            "merchant": txn.get("merchant", ""),
            "merchant_category": txn.get("merchant_category", ""),
            "location": txn.get("location", ""),
            "country": txn.get("country", ""),
            "channel": txn.get("channel", ""),
            "is_recurring": txn.get("is_recurring", False),
            "is_first_time_merchant": txn.get("is_first_time_merchant", False),
            "is_first_time_country": txn.get("is_first_time_country", False),
            "velocity_1h": txn.get("velocity_1h", 1),
            "velocity_24h": txn.get("velocity_24h", 1),
        }
        self.labels = {
            "domain": "finance",
            "transaction_type": txn.get("transaction_type", ""),
            "merchant_category": txn.get("merchant_category", ""),
        }
        self._ground_truth = txn.get("label", "")
        self._ground_truth_detail = txn.get("label_detail", "")

    def _map_type(self, txn):
        txn_type = txn.get("transaction_type", "")
        amount = txn.get("amount", 0)
        velocity = txn.get("velocity_1h", 1)
        is_first_country = txn.get("is_first_time_country", False)

        if txn_type == "wire_transfer":
            if is_first_country:
                return "wire_international_first"
            return "wire_transfer"
        if txn_type == "cash_deposit" and 8000 <= amount < 10000:
            return "cash_deposit_sub_10k"
        if velocity >= 5:
            return "rapid_transactions"
        if txn.get("is_recurring"):
            return "recurring_charge"
        if amount > 5000:
            return "large_transaction"
        return f"{txn_type}_standard"

    def _map_severity(self, txn):
        amount = txn.get("amount", 0)
        velocity = txn.get("velocity_1h", 1)
        is_first_country = txn.get("is_first_time_country", False)

        if is_first_country and txn.get("transaction_type") == "wire_transfer":
            return "high"
        if velocity >= 5:
            return "high"
        if amount > 10000:
            return "high"
        if is_first_country:
            return "high"
        if velocity >= 3:
            return "medium"
        if amount > 5000:
            return "medium"
        if txn.get("is_first_time_merchant") and amount > 500:
            return "medium"
        if txn.get("is_first_time_merchant") and velocity >= 2:
            return "medium"
        if txn.get("transaction_type") == "cash_deposit" and 8000 <= amount < 10000:
            return "medium"
        return "info"

    def _build_message(self, txn):
        parts = [
            f"${txn.get('amount', 0):,.2f}",
            f"at {txn.get('merchant', '?')}",
            f"({txn.get('transaction_type', '?')})",
            f"in {txn.get('country', '?')}",
        ]
        flags = []
        if txn.get("is_first_time_merchant"):
            flags.append("first-time merchant")
        if txn.get("is_first_time_country"):
            flags.append("first-time country")
        if txn.get("is_recurring"):
            flags.append("recurring")
        if txn.get("velocity_1h", 1) >= 3:
            flags.append(f"velocity={txn['velocity_1h']}/hr")
        if flags:
            parts.append(f"[{', '.join(flags)}]")
        return " ".join(parts)


class FinanceCollector(BaseCollector):
    """Collects financial transactions from synthetic data or file."""

    name = "finance"

    def __init__(self, data_path: str = "", synthetic_count: int = 0):
        self._data_path = data_path
        self._synthetic_count = synthetic_count
        self._transactions: List[dict] = []
        self._poll_index = 0
        self._batch_size = 500

    def connect(self, config: dict) -> bool:
        self._data_path = config.get("data_path", self._data_path)
        self._synthetic_count = config.get("synthetic_count", self._synthetic_count)

        if self._data_path:
            try:
                data_path = Path(self._data_path)
                with open(data_path, newline="") as f:
                    if data_path.suffix.lower() == ".csv":
                        transactions = [_coerce_csv_row(row) for row in csv.DictReader(f)]
                    else:
                        transactions = json.load(f)
                if isinstance(transactions, dict):
                    transactions = transactions.get("transactions")
                if not isinstance(transactions, list) or not all(
                    isinstance(item, dict) for item in transactions
                ):
                    raise ValueError(
                        "finance replay data must be a list of transaction objects"
                    )
                self._transactions = transactions
                log.info("Finance collector loaded %d transactions from %s",
                         len(self._transactions), self._data_path)
                return True
            except Exception as e:
                log.warning("Failed to load %s: %s", self._data_path, e)
                return False

        if self._synthetic_count:
            from cascade_compression.benchmarks.synthetic_finance import (
                asdict,
                generate,
            )
            txns = generate(self._synthetic_count)
            self._transactions = [asdict(t) for t in txns]
            log.info("Finance collector generated %d synthetic transactions", len(self._transactions))
            return True

        return False

    def collect(self) -> List[FinanceSignal]:
        if self._poll_index >= len(self._transactions):
            return []
        batch = self._transactions[self._poll_index:self._poll_index + self._batch_size]
        self._poll_index += self._batch_size
        return [FinanceSignal(t) for t in batch]

    def collect_all(self) -> List[FinanceSignal]:
        return [FinanceSignal(t) for t in self._transactions]

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": len(self._transactions) > 0,
            "total_transactions": len(self._transactions),
            "remaining": max(0, len(self._transactions) - self._poll_index),
        }
