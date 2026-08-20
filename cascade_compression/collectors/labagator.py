"""Labagator collector — lab schedule, session status, ops health.

READ-ONLY. API reads only. Never modify labs or sessions.

Polls the Labagator REST API for lab status, room sessions, and ops
dashboard metrics. Tracks lab capacity and session health.

API endpoints adapted from stargate-platform/collectors/labagator/collect_labagator.py.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class LabagatorSignal:
    _counter = 0

    def __init__(self, record: dict):
        LabagatorSignal._counter += 1
        self.signal_id = LabagatorSignal._counter
        self.cluster_id = "labagator"
        self.namespace = record.get("namespace", "labagator")
        self.resource_kind = record.get("kind", "lab")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "labagator"}


class LabagatorCollector(BaseCollector):
    name = "labagator"

    def __init__(self):
        self._api_url = ""
        self._connected = False

    def connect(self, config: dict) -> bool:
        self._api_url = (
            config.get("api_url", "")
            or os.getenv("LABAGATOR_URL", "")
        ).rstrip("/")
        if not self._api_url:
            log.warning("No LABAGATOR_URL configured")
            return False
        data = self._get("/events/")
        self._connected = data is not None
        if self._connected:
            log.info("Labagator connected: %s", self._api_url)
        return self._connected

    def collect(self) -> list:
        signals = []
        signals.extend(self._collect_labs())
        signals.extend(self._collect_sessions())
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _collect_labs(self) -> List[LabagatorSignal]:
        signals = []
        data = self._get("/labs/?limit=200")
        if not data or not isinstance(data, list):
            return signals

        for lab in data:
            code = lab.get("lab_code", "")
            status = lab.get("status", "")
            capacity = lab.get("capacity", 0)
            enrolled = lab.get("enrolled", 0)

            if not code:
                continue

            if status == "degraded":
                signals.append(LabagatorSignal({
                    "signal_type": "lab_degraded",
                    "severity": "high",
                    "kind": "lab", "name": code,
                    "message": f"Lab {code} degraded: {enrolled}/{capacity} enrolled",
                    "status": status, "capacity": capacity, "enrolled": enrolled,
                }))
            elif capacity > 0 and enrolled >= capacity:
                signals.append(LabagatorSignal({
                    "signal_type": "lab_at_capacity",
                    "severity": "medium",
                    "kind": "lab", "name": code,
                    "message": f"Lab {code} at capacity: {enrolled}/{capacity}",
                    "status": status, "capacity": capacity, "enrolled": enrolled,
                }))
            else:
                signals.append(LabagatorSignal({
                    "signal_type": "lab_healthy",
                    "severity": "info",
                    "kind": "lab", "name": code,
                    "message": f"Lab {code}: {enrolled}/{capacity} enrolled",
                    "status": status, "capacity": capacity, "enrolled": enrolled,
                }))

        return signals

    def _collect_sessions(self) -> List[LabagatorSignal]:
        signals = []
        data = self._get("/room-sessions/?limit=100")
        if not data or not isinstance(data, list):
            return signals

        for session in data:
            lab_code = session.get("lab_code", "")
            status = session.get("status", "")
            room = session.get("room_name", "")

            if status == "failed" or status == "error":
                signals.append(LabagatorSignal({
                    "signal_type": "session_failed",
                    "severity": "high",
                    "kind": "session", "name": f"{lab_code}-{room}",
                    "namespace": lab_code,
                    "message": f"Session failed: {lab_code} in {room}",
                    "lab_code": lab_code, "room": room,
                }))
            elif status == "timeout":
                signals.append(LabagatorSignal({
                    "signal_type": "session_timeout",
                    "severity": "medium",
                    "kind": "session", "name": f"{lab_code}-{room}",
                    "namespace": lab_code,
                    "message": f"Session timeout: {lab_code} in {room}",
                    "lab_code": lab_code, "room": room,
                }))

        return signals

    def _get(self, path: str) -> Optional:
        url = f"{self._api_url}{path}"
        try:
            req = Request(url)
            ctx = ssl.create_default_context()
            if os.getenv("LABAGATOR_SSL_VERIFY", "true").lower() == "false":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Labagator GET %s: %s", path, str(e)[:100])
            return None
