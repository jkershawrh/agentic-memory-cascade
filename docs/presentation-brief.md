# Presentation brief: the agent remembers the handoff

## The point

Agent context windows are temporary. This slice demonstrates a separate memory mechanism:
Cascade filters routine session exhaust, retains consequential facts, and makes them
available to a later agent session.

## Five-minute demonstration

1. Start with a synthetic incident and a first agent session.
2. Submit 12 routine acknowledgements plus the cause, constraint, and resolution.
3. Show that Cascade compresses the 12 routine observations and retains three memories.
4. Create a new session and ask three incident-handoff questions with memory disabled.
5. Repeat with memory enabled and show the deterministic score move from 0/3 to 3/3.
6. Show memory identifiers and the zero unsupported-claim count in the JSON evidence.

## Architecture message

Cascade Compression remains the reusable curation engine. Agentic Memory Cascade is the
thin agent-facing seam: session identity, observation admission, bounded recall, and an
evidence harness. No engine code is forked.

## What we can say

- Consequential synthetic facts remained available to a distinct agent session.
- Routine synthetic chatter was compressed before memory formation.
- Recalled facts improved a deterministic task score from 0/3 to 3/3.
- The result is repeatable without an LLM or external service.
- Consequential observations are explicitly severity-marked in this first slice.

## What we cannot say yet

- The memory survives a process restart, outage, or software upgrade.
- The system is production-ready or validated at organizational scale.
- A generative model will always use recalled context correctly.
- The proof includes authorization, governance, or an immutable audit trail.
- The cascade autonomously learned which incident facts were consequential.
