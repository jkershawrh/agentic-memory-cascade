"""Healthcare domain pack."""

from cascade_compression.collectors.healthcare import HealthcareCollector

DOMAIN = "healthcare"
COLLECTOR_CLASS = HealthcareCollector

SYSTEM_PROMPT = """You are classifying clinical healthcare signals (HL7 FHIR events, labs, vitals, medication alerts). Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
