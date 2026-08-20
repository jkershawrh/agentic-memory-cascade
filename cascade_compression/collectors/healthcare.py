"""Healthcare signal collector — maps clinical events to cascade Signal protocol."""

import json
import logging
from typing import List
from .base import BaseCollector

log = logging.getLogger(__name__)


class HealthcareSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "healthcare"
        self.namespace = evt.get("department", "general")
        self.resource_kind = "clinical_event"
        self.resource_name = evt.get("patient_id", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        self.evidence = {
            "message": f"{evt.get('event_type','')} {evt.get('value','')} {evt.get('unit','')} [{evt.get('patient_id','')} {evt.get('department','')}]",
            "event_type": evt.get("event_type", ""),
            "value": evt.get("value", ""),
            "reference_range": evt.get("reference_range", ""),
            "provider": evt.get("provider", ""),
            "department": evt.get("department", ""),
        }
        self.labels = {"domain": "healthcare"}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        sev = evt.get("severity_flag", "")
        if sev == "critical":
            return f"critical_{etype}"
        if sev == "compliance":
            return f"compliance_{etype}"
        if sev == "operational":
            return f"ops_{etype}"
        return etype

    def _map_severity(self, evt):
        sev = evt.get("severity_flag", "normal")
        if sev == "critical":
            return "critical"
        if sev == "compliance":
            return "high"
        if sev == "operational":
            return "medium"
        return "info"


class HealthcareCollector(BaseCollector):
    name = "healthcare"

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
            from cascade_compression.benchmarks.synthetic_healthcare import generate, asdict
            self._events = [asdict(e) for e in generate(self._synthetic_count)]
            return True
        return False

    def collect(self) -> List[HealthcareSignal]:
        if self._poll_index >= len(self._events):
            return []
        batch = self._events[self._poll_index:self._poll_index + 500]
        self._poll_index += 500
        return [HealthcareSignal(e) for e in batch]

    def collect_all(self) -> List[HealthcareSignal]:
        return [HealthcareSignal(e) for e in self._events]
