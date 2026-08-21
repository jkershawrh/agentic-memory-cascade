# Agent Promotion Guidelines

> Historical run examples in this document are not release guarantees. The current runtime reports false-negative telemetry as unmeasured until ground-truth feedback is supplied and requires zero important classifications in its promotion sample.

## Overview

The cascade discovers and promotes agents automatically. No human writes rules. The system watches signal patterns, proposes agents, validates them against real data, and promotes or demotes them based on performance.

## The Promotion Ladder

```
                    ┌─────────┐
                    │  MACRO  │  1000+ samples, 85% accuracy, 5% FP, 0% FN
                    │ terminal│  Human reviewed.
                    └────▲────┘
                         │ promote (human review required)
                    ┌────┴────┐
                    │  MICRO  │  500+ samples, 85% accuracy, 10% FP, 0% FN
                    │         │  Human reviewed.
                    └────▲────┘
                         │ promote (automated)
                    ┌────┴────┐
                    │  NANO   │  200+ samples, 75% accuracy, 15% FP, 0% FN
                    │         │  Agent is ACTIVATED. Zero-FN enforced.
                    └────▲────┘
                         │ promote (automated, or human gate if enabled)
               ┌─────────┴──────────┐
               │ PENDING_APPROVAL   │  (optional, human_gate_enabled only)
               │                    │  Meets nano thresholds, awaiting human sign-off.
               └─────────▲──────────┘
                         │ promote (automated)
                    ┌────┴────┐
                    │CANDIDATE│  50+ samples, 60% accuracy, 30% FP, 20% FN
                    │         │  Under observation. FN tolerated.
                    └────▲────┘
                         │ promote (automated)
                    ┌────┴────┐
                    │  DRAFT  │  Proposed by CorpusAnalyzer.
                    │         │  Awaiting LLM validation.
                    └─────────┘
```

## Tier Requirements

| Tier | Min Samples | Min Accuracy | Max FP Rate | Max FN Rate | Human Gate | Status |
|------|------------|-------------|-------------|-------------|------------|--------|
| Draft | 0 | none | none | none | No | Proposed, not active |
| Candidate | 50 | 60% | 30% | 20% | No | Under observation |
| Pending Approval | — | — | — | — | Yes (optional) | Awaiting human sign-off |
| Nano | 200 | 75% | 15% | **0%** | No | **ACTIVATED** — processing signals |
| Micro | 500 | 85% | 10% | **0%** | Yes | Active, human-validated |
| Macro | 1000 | 85% | 5% | **0%** | Yes | Terminal |

## How Agents Are Discovered

The `CorpusAnalyzer` watches the signal stream and detects three patterns:

### 1. Repeat Floods

```
Detection: Same signal_type appears N+ times within a time window
Threshold: min_repeat_count=10, repeat_window_seconds=300

Example:
  "event_deprecatedannotation" appeared 6,074 times in 5 minutes
  → Proposes: RepeatFloodSuppressor for "event_deprecatedannotation"
  → Rule: signal_type == "event_deprecatedannotation" → SUPPRESS
```

### 2. Dominant Types

```
Detection: One signal_type dominates the stream (>N% of total volume)
Threshold: min_frequency=0.05 (5% of all signals)

Example:
  "job_succeeded" is 92% of all AAP signals
  → Proposes: DominantNoiseSuppressor for "job_succeeded"
  → Rule: signal_type == "job_succeeded" → DROP
```

### 3. Mono-Severity Patterns

```
Detection: A signal_type always appears at the same severity level
Threshold: min_frequency=0.05

Example:
  "job_error" always appears at severity "high" (100% of instances)
  → Proposes: SeverityGate for "job_error" at "high"
  → Rule: signal_type == "job_error" AND severity == "high" → CLASSIFY
```

## Validation Flow

When the CorpusAnalyzer proposes a draft agent, the validation process:

```
Step 1: DRAFT proposed
        CorpusAnalyzer sees pattern → creates RuleAgent
        Agent is registered but NOT processing signals
        Status: "awaiting_phi4_validation"

Step 2: LLM validation
        The LLM classifies signals matching the proposed rule
        If LLM consistently says "routine_noise" for this pattern:
          → Confidence increases
        If LLM says "needs_attention" or "real_incident":
          → Agent stays in draft or is discarded

Step 3: Sample accumulation
        Agent tracks metrics:
          - true_positives: correctly suppressed noise
          - false_positives: suppressed something important
          - accuracy: TP / (TP + FP)
        At 50 samples with 60%+ accuracy → promote to CANDIDATE

Step 4: CANDIDATE observation
        Agent continues tracking metrics
        At 200 samples with 75%+ accuracy, <15% FP → promote to NANO
        If accuracy drops below threshold → demote to DRAFT

Step 5: NANO activation
        Agent is now ACTIVATED
        Processes signals in Stage 3 of the pipeline
        Signals matching this agent's rule never reach the LLM
        Compression ratio increases
```

## Demotion (Hardened)

Any activated agent (nano, micro, macro) with **ANY** false negative in a
validation batch is **instantly demoted to draft** and deactivated:

```
Triggers (any one of these fires demotion):
  - ANY false negative (fn > 0) in a validation batch → instant demote
  - Shadow validation: LLM disagrees with a suppressed signal → instant demote
  - GCL FAILS verdict: independent audit finds a false negative → instant demote
  - TTL expiry: agent has been active for 72h+ without re-qualification → demote + reactivate

What happens:
  1. Agent tier → draft
  2. Agent deactivated (stops processing signals immediately)
  3. samples_tested → 0 (cooling-off: must re-accumulate from scratch)
  4. human_approved → false (must re-earn if human gate is enabled)
  5. Demotion event → immutable ledger (full evidence chain)
  6. Agent must be explicitly reactivated before it can climb again

Demotion path (all activated tiers):
  NANO/MICRO/MACRO → DRAFT (instant, no intermediate stops)

There is no gradual demotion. One false negative = back to draft.
```

## Agent Types

### Built-in Agents (always active, not promotable)

| Agent | Stage | Action | Safety |
|-------|-------|--------|--------|
| DeduplicateAgent | 1 | Content hash, 60s window | Only deduplicates exact matches |
| TransientSuppressor | 1 | Type+severity filter | Fail-open: oomkill, segfault, panic, security, data loss always pass |
| SeverityGate | 1 | Drops info severity | Escalation patterns override: oomkill, segfault, panic, security |
| PatternClassifier | 2 | 7 regex patterns | Tags only, never drops |
| ThresholdClassifier | 2 | Numeric thresholds | Tags only, never drops |

### Discovered Agents (runtime, promotable)

| Agent | Stage | Discovery | Rule |
|-------|-------|-----------|------|
| RepeatFloodSuppressor | 3 | Repeat flood pattern | signal_type == X → SUPPRESS |
| DominantNoiseSuppressor | 3 | Dominant type pattern | signal_type == X → DROP |
| RuleAgent | 3 | Mono-severity or custom | field operator value → outcome |

## Metrics Tracked Per Agent

```python
AgentMetrics:
    samples: int           # total signals evaluated
    true_positives: int    # correctly handled (noise correctly suppressed)
    false_positives: int   # incorrectly handled (important signal suppressed)
    accuracy: float        # TP / (TP + FP)
    fp_rate: float         # FP / samples
    last_evaluated: str    # ISO timestamp
```

## Safety Invariants

1. **Built-in agents never drop high/critical severity** — TransientSuppressor and SeverityGate have hardcoded keyword lists that always pass through (oomkill, segfault, panic, security, data loss).

2. **Discovered agents start inactive** — a proposed agent cannot process signals until it reaches NANO tier (200+ validated samples).

3. **The LLM is always the backstop** — if no agent handles a signal, it passes through to the LLM. The cascade can only reduce LLM load, never increase it.

4. **Demotion is automatic** — if an agent's accuracy drops, it is demoted without human intervention. Human review is only required for MICRO/MACRO promotion, not demotion.

5. **Zero-FN invariant** — activated agents (nano+) that miss ANY real incident are instantly demoted to draft, deactivated, and must re-accumulate from scratch. The immutable ledger records the full demotion evidence chain. Three layers enforce this: cascade prevents (zero-FN promotion gate), ledger records (provenance), GCL verifies (independent audit).

## Live Examples

### K8s cascade (observed in production)

**Peak run (68.7M signals):**
```
23 nano agents activated, 37 total — all green on rubric
99.5% compression
0 false negatives
```

**Earlier run (3.3M signals, initial tuning):**
```
Activated agents (self-discovered):
  event_deprecatedannotation  — 6,074 noise confirmations
  event_pending               — 584 noise confirmations
  event_completed             — 481 noise confirmations
  pod_pending                 — 21 noise confirmations
  task_runner_on_skipped      — 12 noise confirmations
  task_warning                — 6 noise confirmations

Result: 70-74% compression
```

### AAP cascade (observed in production)

```
Signals:   1.0M+
Activated agents (self-discovered):
  task_runner_on_skipped      — 1,496 noise confirmations
  task_runner_on_ok           — 10 noise confirmations
  task_verbose                — 8 noise confirmations

Result: 96% compression, 0 false negatives
```

These agents were not written by a human. The cascade discovered them from watching the signal stream and validating against the LLM.
