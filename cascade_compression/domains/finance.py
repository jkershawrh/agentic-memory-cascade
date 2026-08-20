"""Financial services domain pack."""

from cascade_compression.collectors.finance import FinanceCollector

DOMAIN = "finance"
COLLECTOR_CLASS = FinanceCollector

SYSTEM_PROMPT = """You are classifying financial transaction signals. Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
