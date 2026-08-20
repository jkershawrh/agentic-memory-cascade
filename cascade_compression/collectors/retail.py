"""Retail signal collector — maps POS/inventory events to cascade Signal protocol."""

import json
import logging
from typing import List
from .base import BaseCollector

log = logging.getLogger(__name__)


class RetailSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "retail"
        self.namespace = evt.get("store_id", "unknown")
        self.resource_kind = "retail_event"
        self.resource_name = evt.get("sku", "") or evt.get("register_id", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        self.evidence = {
            "message": evt.get("description", ""),
            "event_type": evt.get("event_type", ""),
            "amount": evt.get("amount", 0),
            "quantity": evt.get("quantity", 0),
            "department": evt.get("department", ""),
            "employee_id": evt.get("employee_id", ""),
            "store_id": evt.get("store_id", ""),
        }
        self.labels = {"domain": "retail", "department": evt.get("department", "")}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        amount = evt.get("amount", 0)
        if etype == "pos_return" and amount > 100:
            return "high_value_return"
        return etype

    def _map_severity(self, evt):
        label = evt.get("label", "")
        etype = evt.get("event_type", "")
        amount = evt.get("amount", 0)
        if label in ("shrinkage", "compliance"):
            return "high"
        if label == "operational":
            return "medium"
        if etype == "pos_return" and amount > 200:
            return "medium"
        return "info"


class RetailCollector(BaseCollector):
    name = "retail"

    def __init__(self, data_path: str = "", synthetic_count: int = 0):
        self._data_path = data_path
        self._synthetic_count = synthetic_count
        self._events = []
        self._poll_index = 0

    def connect(self, config: dict) -> bool:
        self._data_path = config.get("data_path", self._data_path)
        self._synthetic_count = config.get("synthetic_count", self._synthetic_count)
        if self._data_path:
            with open(self._data_path) as f:
                self._events = json.load(f)
            return True
        if self._synthetic_count:
            from cascade_compression.benchmarks.synthetic_retail import generate, asdict
            self._events = [asdict(e) for e in generate(self._synthetic_count)]
            return True
        return False

    def collect(self) -> List[RetailSignal]:
        if self._poll_index >= len(self._events):
            return []
        batch = self._events[self._poll_index:self._poll_index + 500]
        self._poll_index += 500
        return [RetailSignal(e) for e in batch]

    def collect_all(self) -> List[RetailSignal]:
        return [RetailSignal(e) for e in self._events]
