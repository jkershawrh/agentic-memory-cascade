"""Insurance signal collector — maps claims/policy events to cascade Signal protocol."""

import json
import logging
from typing import List
from .base import BaseCollector

log = logging.getLogger(__name__)


class InsuranceSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "insurance"
        self.namespace = evt.get("line_of_business", "general")
        self.resource_kind = "insurance_event"
        self.resource_name = evt.get("claim_id", "") or evt.get("policy_id", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        pid = evt.get("policy_id", "") or evt.get("claim_id", "")
        self.evidence = {
            "message": f"{evt.get('description', '')} [{pid} {evt.get('state', '')} {evt.get('line_of_business', '')}]",
            "event_type": evt.get("event_type", ""),
            "amount": evt.get("amount", 0),
            "line_of_business": evt.get("line_of_business", ""),
            "state": evt.get("state", ""),
            "provider": evt.get("provider", ""),
            "adjuster": evt.get("adjuster", ""),
        }
        self.labels = {"domain": "insurance", "lob": evt.get("line_of_business", "")}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        amount = evt.get("amount", 0)
        if etype == "claim_filed" and amount > 25000:
            return "large_claim"
        return etype

    def _map_severity(self, evt):
        amount = evt.get("amount", 0)
        etype = evt.get("event_type", "")
        desc = evt.get("description", "").lower()
        if etype in ("licensing_gap", "reserve_deficiency", "regulatory_filing",
                      "market_conduct", "solvency_warning"):
            return "high"
        if etype in ("sla_breach", "adjuster_overload", "reinsurance_trigger",
                      "system_outage", "backlog_alert"):
            return "medium"
        if etype == "claim_filed" and amount > 25000:
            return "medium"
        if etype == "claim_filed" and any(w in desc for w in
                ("staged", "inflated", "phantom", "identity", "duplicate", "mismatch",
                 "po box", "npi", "impossible", "pre-existing", "attorney",
                 "aftermarket", "oem", "soft tissue", "upcoded", "bundled",
                 "ssn", "vin", "classified", "manufacturing", "clerical")):
            return "high"
        if etype == "claim_filed" and amount > 10000:
            return "medium"
        return "info"


class InsuranceCollector(BaseCollector):
    name = "insurance"

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
            from cascade_compression.benchmarks.synthetic_insurance import generate, asdict
            self._events = [asdict(e) for e in generate(self._synthetic_count)]
            return True
        return False

    def collect(self) -> List[InsuranceSignal]:
        if self._poll_index >= len(self._events):
            return []
        batch = self._events[self._poll_index:self._poll_index + 500]
        self._poll_index += 500
        return [InsuranceSignal(e) for e in batch]

    def collect_all(self) -> List[InsuranceSignal]:
        return [InsuranceSignal(e) for e in self._events]
