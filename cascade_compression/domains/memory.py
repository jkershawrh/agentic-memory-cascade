"""Agent memory curation domain pack."""

from cascade_compression.collectors.memory import MemoryCollector

DOMAIN = "memory"
COLLECTOR_CLASS = MemoryCollector

SYSTEM_PROMPT = """You are building a shared knowledge corpus from agent memory.
Each signal is a single claim extracted from a project memory or session file.

Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident.

- routine_noise: session-specific state, expired config, commit hashes, project-local ephemera
- known_pattern: fact or rule already established in the knowledge corpus
- needs_attention: novel insight, cross-project pattern, or reusable lesson worth adding to the corpus
- real_incident: correction of existing knowledge, contradiction between sources, or rule that must be enforced globally

Answer with one word only."""
