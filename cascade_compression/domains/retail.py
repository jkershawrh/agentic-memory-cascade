"""Retail domain pack."""

from cascade_compression.collectors.retail import RetailCollector

DOMAIN = "retail"
COLLECTOR_CLASS = RetailCollector

SYSTEM_PROMPT = """You are classifying retail operations signals (POS transactions, inventory, returns, compliance). Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
