"""Telecom domain pack."""

from cascade_compression.collectors.telecom import TelecomCollector

DOMAIN = "telecom"
COLLECTOR_CLASS = TelecomCollector

SYSTEM_PROMPT = """You are classifying telecom network and customer signals (CDR records, alarms, SLA breaches, complaints). Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
