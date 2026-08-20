"""Telecom signal collector — maps network/customer events to cascade Signal protocol."""

import json
import logging
from typing import List
from .base import BaseCollector

log = logging.getLogger(__name__)


class TelecomSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "telecom"
        self.namespace = evt.get("region", "general")
        self.resource_kind = "telecom_event"
        self.resource_name = evt.get("network_element", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        ne = evt.get("network_element", "")
        self.evidence = {
            "message": f"{evt.get('description', '')} [{ne} {evt.get('region', '')}]",
            "event_type": evt.get("event_type", ""),
            "metric_name": evt.get("metric_name", ""),
            "metric_value": evt.get("metric_value", 0),
            "threshold": evt.get("threshold", 0),
            "affected_subscribers": evt.get("affected_subscribers", 0),
            "technology": evt.get("technology", ""),
            "region": evt.get("region", ""),
        }
        self.labels = {"domain": "telecom", "technology": evt.get("technology", "")}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        subs = evt.get("affected_subscribers", 0)
        if etype == "outage" and subs > 10000:
            return "major_outage"
        if etype == "outage":
            return "minor_outage"
        return etype

    def _map_severity(self, evt):
        sev = evt.get("severity_level", "normal")
        subs = evt.get("affected_subscribers", 0)
        if sev == "critical" or subs > 10000:
            return "critical"
        if sev == "major" or sev == "compliance":
            return "high"
        if sev == "customer":
            return "medium"
        metric_val = evt.get("metric_value", 0)
        threshold = evt.get("threshold", 0)
        if threshold > 0 and metric_val > threshold * 0.8:
            return "low"
        return "info"


class TelecomCollector(BaseCollector):
    name = "telecom"

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
            from cascade_compression.benchmarks.synthetic_telecom import generate, asdict
            self._events = [asdict(e) for e in generate(self._synthetic_count)]
            return True
        return False

    def collect(self) -> List[TelecomSignal]:
        if self._poll_index >= len(self._events):
            return []
        batch = self._events[self._poll_index:self._poll_index + 500]
        self._poll_index += 500
        return [TelecomSignal(e) for e in batch]

    def collect_all(self) -> List[TelecomSignal]:
        return [TelecomSignal(e) for e in self._events]
