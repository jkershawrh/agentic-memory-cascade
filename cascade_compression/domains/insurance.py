"""Insurance domain pack."""

from cascade_compression.collectors.insurance import InsuranceCollector

DOMAIN = "insurance"
COLLECTOR_CLASS = InsuranceCollector

SYSTEM_PROMPT = """You are classifying insurance signals (claims, policy changes, underwriting events). Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
