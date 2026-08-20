# Architecture

## Executive Summary

Cascade compression is a three-tier signal processing framework. Most enterprise AI signals are routine noise — the cascade eliminates them with deterministic rules before anything touches an LLM. The 1-15% that survive get classified by a small CPU-hosted model. No GPU required.

> **Historical document:** The benchmark figures below describe preserved experiments, not current release guarantees. Raw artifacts contain non-zero false-negative results. Validate safety and measured model/hardware throughput before using compression or TCO figures operationally.

---

## How It Works (Non-Technical)

```
Raw signals (millions/day)
    │
    ▼
┌─────────────────────────────┐
│  RULES (85-99% handled)     │  Deduplication, severity filtering,
│  Zero inference cost        │  pattern matching, learned rules
│  Sub-millisecond            │  No model involved
└──────────────┬──────────────┘
               │ 1-15% survive
               ▼
┌─────────────────────────────┐
│  SMALL MODEL ON CPU         │  IBM Granite 8B or phi4-mini
│  ~600ms per classification  │  4 buckets: noise, known, attention, incident
│  Runs on standard servers   │  No GPU hardware needed
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  SELF-TUNING                │  Watches what the model classifies as noise
│  Discovers new rules        │  Promotes them automatically
│  Cascade gets smarter       │  Zero human intervention
└─────────────────────────────┘
```

**The cost story:** If 90% of signals never reach a model, you don't need GPU infrastructure to handle them. A single Intel Xeon 6 server runs the entire system at 10% utilization.

---

## Full System

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CASCADE COMPRESSION SYSTEM                           │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         DOMAIN PACKS                                  │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────┐    │  │
│  │  │   K8s   │ │   AAP   │ │ Finance │ │Healthcare│ │  + 4 more    │    │  │
│  │  │Collector│ │Collector│ │Collector│ │Collector │ │  (insurance, │    │  │
│  │  │+ prompt │ │+ prompt │ │+ prompt │ │+ prompt  │ │  retail,     │    │  │
│  │  │         │ │         │ │         │ │          │ │  telecom,    │    │  │
│  │  │  Live   │ │  Live   │ │Synthetic│ │Synthetic │ │  memory)     │    │  │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘ └──────┬───────┘    │  │
│  └───────┼────────-──┼─────────-─┼─────────-─┼──────────────┼───────────-┘  │
│          └───────-───┴──────-────┴─────-─────┴──────────────┘               │
│                                    │                                        │
│                              Signal protocol                                │
│                     {type, severity, source, content}                       │
│                                    │                                        │
│  ┌─────────────────────────────────▼─────────────────────────────────────┐  │
│  │                      CASCADE ENGINE (unchanged)                       │  │
│  │                                                                       │  │
│  │   CascadeBridge                                                       │  │
│  │   ├── CascadePipeline (nano tier)                                     │  │
│  │   │   ├── Stage 1: Dedup → Transient Suppressor → Severity Gate       │  │
│  │   │   ├── Stage 2: Pattern Classifier → Threshold Classifier          │  │
│  │   │   └── Stage 3: [Learned Agents — self-discovered]                 │  │
│  │   │                                                                   │  │
│  │   ├── LLM Classification (micro tier)                                 │  │
│  │   │   granite-8b / phi4-mini on CPU                                   │  │
│  │   │   routine_noise | known_pattern | needs_attention | real_incident │  │
│  │   │                                                                   │  │
│  │   └── Self-Tuning Loop                                                │  │
│  │       CorpusAnalyzer → PromotionEngine → Agent activation/demotion    │  │
│  └───────────────────────┬───────────────────────────────────────────────┘  │
│                          │                                                  │
│              ┌───────────┴───────────┐                                      │
│              ▼                       ▼                                      │
│  ┌───────────────────┐  ┌────────────────────────────────┐                  │
│  │  TCO CALCULATOR   │  │  GOVERNANCE (optional)         │                  │
│  │                   │  │                                │                  │
│  │  Xeon vs GPU vs   │  │  Immutable Ledger              │                  │
│  │  Cloud API cost   │  │  (hash-chained decisions)      │                  │
│  │  comparison       │  │         │                      │                  │
│  │                   │  │  GCL Audit Loop                │                  │
│  │  FastAPI :8090    │  │  (LLM adversary probe on       │                  │
│  │                   │  │   sampled drops)               │                  │
│  └───────────────────┘  └────────────────────────────────┘                  │
│                                                                             │
│  Runs on one Xeon 6 server at 10% utilization. No GPU.                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Technical Architecture

### Signal Protocol

Everything flows through a generic `Signal` object:

```python
Signal(
    signal_id,      # UUID
    signal_type,    # domain-specific (e.g. "pod_crashloop", "wire_transfer")
    severity,       # info | low | medium | high | critical
    source,         # resource that generated the signal
    content,        # dict — arbitrary evidence payload
    labels,         # dict — metadata for routing
    namespace,      # grouping identifier
    cluster,        # source system identifier
)
```

The framework doesn't know what domain produced the signal. Domain-specific knowledge lives in three pluggable components: collector, prompt, and signal mapping.

### Cascade Engine

```
                    1,000 signals/batch
                           │
    ═══════════════════════╪═══════════════════════════════════════
    ║  STAGE 1: NOISE ELIMINATION                                 ║
    ║                                                             ║
    ║  ┌──────────────┐     ┌───────────────┐   ┌────────────┐    ║
    ║  │ Deduplicator │     │   Transient   │   │  Severity  │    ║
    ║  │              │     │  Suppressor   │   │    Gate    │    ║
    ║  │ SHA256 hash  │     │               │   │            │    ║
    ║  │ of content   │     │ Knows which   │   │ Drops info │    ║
    ║  │ 60s window   │     │ types are     │   │ severity   │    ║
    ║  │              │     │ transient     │   │ unless     │    ║
    ║  │ Same signal  │     │ at low sev    │   │ escalation │    ║
    ║  │ within 60s?  │     │               │   │ keywords   │    ║
    ║  │ → DEDUPE     │     │ Fail-open:    │   │ match      │    ║
    ║  │              │     │ oomkill,      │   │            │    ║
    ║  │ ~150 removed │     │ panic,        │   │ ~450       │    ║
    ║  │              │     │ security      │   │ removed    │    ║
    ║  │              │     │ always pass   │   │            │    ║
    ║  │              │     │               │   │            │    ║
    ║  │              │     │ ~200 removed  │   │            │    ║
    ║  └──────┬───────┘     └──────┬──────-─┘   └─────┬──────┘    ║
    ║         └────────────────────┴─────────────────┘            ║
    ║                          │                                  ║
    ║                    200 signals remain                       ║
    ═══════════════════════════╪═══════════════════════════════════
    ║  STAGE 2: PATTERN CLASSIFICATION                           ║
    ║                                                            ║
    ║  ┌────────────────────────┐  ┌─────────────────────────┐   ║
    ║  │  Pattern Classifier    │  │  Threshold Classifier   │   ║
    ║  │                        │  │                         │   ║
    ║  │  7 regex patterns:     │  │  Numeric extraction:    │   ║
    ║  │  • OOM / memory        │  │  • CPU > 80%            │   ║
    ║  │  • Disk pressure       │  │  • Memory > 95%         │   ║
    ║  │  • CPU saturation      │  │  • Disk > 90%           │   ║
    ║  │  • Network errors      │  │                         │   ║
    ║  │  • Crash / restart     │  │  Tags signals,          │   ║
    ║  │  • Auth failures       │  │  never drops            │   ║
    ║  │  • Scaling events      │  │                         │   ║
    ║  │                        │  │                         │   ║
    ║  │  Tags signals,         │  │                         │   ║
    ║  │  never drops           │  │                         │   ║
    ║  └────────────┬───────────┘  └────────────┬────────────┘   ║
    ║               └──────────────────────────-┘                ║
    ║                          │                                 ║
    ║                   200 signals remain (tagged)              ║
    ═══════════════════════════╪═══════════════════════════════════
    ║  STAGE 3: LEARNED AGENTS (empty at startup)                ║
    ║                                                            ║
    ║  These agents don't exist on day 1. The cascade discovers  ║
    ║  them from the LLM feedback loop (see below).              ║
    ║                                                            ║
    ║  ┌────────────────────────┐  ┌─────────────────────────┐   ║
    ║  │  Repeat Flood          │  │  Dominant Noise         │   ║
    ║  │  Suppressor            │  │  Suppressor             │   ║
    ║  │                        │  │                         │   ║
    ║  │  Same signal_type      │  │  Signal type identified │   ║
    ║  │  N+ times within       │  │  as consistent noise    │   ║
    ║  │  a time window         │  │  by LLM feedback        │   ║
    ║  │  → SUPPRESS            │  │  → SUPPRESS             │   ║
    ║  │                        │  │                         │   ║
    ║  │  After activation:     │  │  After activation:      │   ║
    ║  │  ~50 removed           │  │  ~50 removed            │   ║
    ║  └────────────┬───────────┘  └────────────┬────────────┘   ║
    ║               └──────────────────────────-┘                ║
    ║                          │                                 ║
    ║                   100 signals survive                      ║
    ═══════════════════════════╪═══════════════════════════════════
                               │
                        NANO TIER RESULT
                     900 handled (90%)
                     100 survivors → LLM
                               │
    ═══════════════════════════╪═══════════════════════════════════
    ║  MICRO TIER: LLM CLASSIFICATION                            ║
    ║                                                            ║
    ║  granite-8b / phi4-mini on CPU (~600ms per signal)         ║
    ║                                                            ║
    ║  System prompt (domain-specific, one paragraph):           ║
    ║  "Classify as: routine_noise | known_pattern |             ║
    ║   needs_attention | real_incident. One word only."         ║
    ║                                                            ║
    ║            100 signals classified                          ║
    ║            ┌──────────────────────────────────────┐        ║
    ║            │  routine_noise    42  ─── noise ───┐ │        ║
    ║            │  known_pattern    31  ─── noise ───┤ │        ║
    ║            │  needs_attention  22  ─── keep ────┤ │        ║
    ║            │  real_incident     5  ─── alert ───┘ │        ║
    ║            └──────────────────────────────────────┘        ║
    ═══════════════════════════╪═══════════════════════════════════
                               │
                        LLM says "noise"
                        for 73 signals
                               │
    ═══════════════════════════╪═══════════════════════════════════
    ║  SELF-TUNING FEEDBACK LOOP                                 ║
    ║                                                            ║
    ║  ┌──────────────────────────────────────────────────────┐  ║
    ║  │  Corpus Analyzer                                     │  ║
    ║  │  Watches signal stream (10K buffer)                  │  ║
    ║  │                                                      │  ║
    ║  │  Detects:                                            │  ║
    ║  │  • Repeat floods (same type N+ times in window)      │  ║
    ║  │  • Dominant types (>5% of traffic)                   │  ║
    ║  │  • Mono-severity (>80% at one severity)              │  ║
    ║  │                                                      │  ║
    ║  │  "event_deprecatedannotation appeared 6,074 times    │  ║
    ║  │   and LLM classified it as noise every time"         │  ║
    ║  │                                                      │  ║
    ║  │  → Proposes draft agent                              │  ║
    ║  └──────────────────────┬───────────────────────────────┘  ║
    ║                         │                                  ║
    ║  ┌──────────────────────▼───────────────────────────────┐  ║
    ║  │  Promotion Engine                                    │  ║
    ║  │                                                      │  ║
    ║  │  draft ──→ candidate ──→ nano ──→ micro ──→ macro    │  ║
    ║  │         50 samples    200 samples  500      1000     │  ║
    ║  │         60% accuracy  75% acc.     85%      85%      │  ║
    ║  │                                                      │  ║
    ║  │  At nano tier: ACTIVATED                             │  ║
    ║  │  Agent runs in Stage 3 from now on                   │  ║
    ║  │  LLM never sees matching signals again               │  ║
    ║  │                                                      │  ║
    ║  │  Accuracy drops? → automatic demotion                │  ║
    ║  │  False negative? → immediate demotion                │  ║
    ║  └──────────────────────────────────────────────────────┘  ║
    ║                                                            ║
    ║  Cycle repeats every 30s. After ~1 hour:                   ║
    ║  • 5-23 agents self-discovered                             ║
    ║  • Compression rises from 60% → 96-99%                     ║
    ║  • LLM calls drop proportionally                           ║
    ║  • No human intervention at any point                      ║
    ═════════════════════════════════════════════════════════════
```

**Numbers above are representative.** Actual compression varies by domain:
K8s peaks at 99.5% (68.7M signals), AAP at 96%, finance cold-start at 61%.
The cascade improves continuously — cold-start numbers are the floor.

### CascadeBridge — The Orchestrator

`CascadeBridge` is the main entry point. It wires everything together:

1. Receives signals from a collector
2. Converts them to the `Signal` protocol
3. Runs them through `CascadePipeline` (nano tier)
4. Buffers survivors and classifies them via LLM (micro tier)
5. Feeds LLM results back to `CorpusAnalyzer` (self-tuning)
6. Runs `PromotionEngine` periodically to promote/demote agents
7. Optionally writes decisions to the immutable ledger

State (activated agents, metrics) persists to a JSON file and restores on restart.

### Self-Tuning: How Agents Are Discovered

The cascade discovers its own rules. No human writes agents.

1. **CorpusAnalyzer** watches the signal stream (10K-signal buffer) and detects three pattern types:
   - **Repeat floods** — same signal type appears N+ times within a time window
   - **Dominant types** — one signal type is >5% of total volume
   - **Mono-severity** — a signal type always appears at the same severity

2. For each pattern, it proposes a draft agent (a `RuleAgent` with configurable conditions).

3. The **PromotionEngine** validates the agent against incoming signals using LLM classifications as ground truth. Agents progress through five tiers:

   ```
   draft → candidate (50+ samples, 60% accuracy)
         → nano (200+ samples, 75% accuracy) ← ACTIVATED
         → micro (500+ samples, human reviewed)
         → macro (1000+ samples, terminal)
   ```

4. Once at nano tier, the agent processes signals in Stage 3. The LLM never sees matching signals again. Compression ratio increases.

5. If accuracy degrades, the agent is automatically demoted. Safety invariant: over-escalate (safe) rather than under-escalate (dangerous).

### Domain Packs

Adding a new domain requires three things, zero framework changes:

| Component | What | Where |
|-----------|------|-------|
| **Collector** | Reads data source, maps to Signal | `collectors/your_domain.py` |
| **Prompt** | One paragraph telling the LLM what the buckets mean | `domains/your_domain.py` |
| **Data** | Historical signals for replay bootstrapping | CSV, DB, or synthetic generator |

Seven domains validated:

| Domain | Source | Peak Compression | Critical Survival |
|--------|--------|----------------:|------------------:|
| Kubernetes | Live (68.7M signals) | 99.5% | 0 FN |
| AAP (Ansible) | Live (1M+ signals) | 96.0% | 0 FN |
| Financial Services | Synthetic | 61.1% | 92.7% fraud, 100% compliance |
| Healthcare | Synthetic | 91.0% | 96.6% critical, 99.0% compliance |
| Insurance | Synthetic | 81.2% | 100% fraud, 99.8% compliance |
| Retail | Synthetic | 88.3% | 100% shrinkage, 100% compliance |
| Telecom | Synthetic | 94.3% | 92.1% incidents |

### Resource Footprint

| Component | CPU | RAM | Role |
|-----------|-----|-----|------|
| Cascade engine | 4 | 4 GB | Pipeline, agents, promotion, LLM client |
| Postgres | 1 | 1 GB | Signal store, agent state |
| Granite-8b (micro) | 8 | 8 GB | LLM classification |
| **Total** | **13** | **13 GB** | |

On a single Xeon 6 server (128 cores, 512 GB): **10% utilization.**

### Governance (If Asked)

For regulated industries, the cascade optionally integrates with an immutable ledger (hash-chained decision log) and an independent audit loop that samples drops and challenges them with an LLM probe. Details in [gcl-ledger-integration-plan.md](gcl-ledger-integration-plan.md). Adds 3.5 CPU / 2.8 GB.

### Component Map

```
cascade_compression/
├── bridge.py             Orchestrator — collector → pipeline → LLM → feedback
├── cli.py                cascade-run (live), cascade-replay (historical)
├── cascade/              NANO TIER
│   ├── protocol.py       Signal, CascadeDecision, Outcome, CascadeAgent
│   ├── pipeline.py       CascadePipeline — runs agents in stage order
│   ├── agents.py         5 built-in agents (dedup, transient, severity, pattern, threshold)
│   ├── dynamic_agents.py RepeatFloodSuppressor, DominantNoiseSuppressor
│   ├── promotion.py      5-tier promotion engine with rubric matrix
│   ├── corpus_analyzer.py Pattern discovery (floods, dominant types, mono-severity)
│   ├── router.py         Routes survivors by tier and lane
│   └── service.py        FastAPI cascade service
├── collectors/           7 domain collectors (k8s, aap, finance, healthcare, insurance, retail, telecom)
├── domains/              Domain configs (prompt, model, collector class per domain)
├── routing/              Benchmark-graded model selection (19 models, 5 lanes, 6 industries)
├── infra/                Pressure-aware scaler, fleet manager
├── tco/                  TCO calculator, FastAPI API, FSI scenarios
├── integrations/         Immutable ledger client
├── metrics/              Precision metric (LLM-vs-LLM audit)
└── benchmarks/           Harness, shootouts, synthetic generators
```

### Data Flow (Technical)

```
Collector.collect()
    │ returns List[Signal]
    ▼
CascadeBridge.process(signals)
    │
    ├── CascadePipeline.run(signals)
    │   ├── Stage 1: DeduplicateAgent → TransientSuppressor → SeverityGate
    │   ├── Stage 2: PatternClassifier → ThresholdClassifier
    │   └── Stage 3: [RepeatFloodSuppressor, DominantNoiseSuppressor] (if activated)
    │   └── Returns CascadeResult(survivors, decisions, compression_ratio)
    │
    ├── LLM classification (survivors only)
    │   POST /v1/chat/completions → routine_noise | known_pattern | needs_attention | real_incident
    │
    ├── CorpusAnalyzer.analyze() → discovers patterns → proposes draft agents
    │
    ├── PromotionEngine.evaluate() → promotes/demotes agents based on LLM feedback
    │
    └── LedgerClient.write_decisions() → POST /api/receipts (optional, fire-and-forget)
```

### Key Design Decisions

- **Agents are stateless per-batch.** Each `process()` call gets the full signal list. Agent state (dedup windows, flood counts) lives in the agent instance, not the pipeline.
- **LLM is the backstop, not the primary.** If no agent handles a signal, it passes through to the LLM. The cascade can only reduce LLM load, never increase it.
- **Fire-and-forget ledger writes.** Ledger failures are logged but never block the pipeline. The cascade's job is signal processing, not governance.
- **Terse prompts outperform detailed ones.** By the time signals reach the LLM, the cascade has eliminated 85-99% of noise. The remaining signals are ambiguous edge cases where general knowledge beats domain instructions.
- **Temperature=0, always.** All models are 100% deterministic. Three consecutive runs produce identical output.

---

## Memory Domain Pack — Institutional Knowledge Extraction

The cascade framework processes any signal type without modification. The memory domain pack applies this to agent memory — extracting institutional knowledge from what agents observe across projects.

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     INSTITUTIONAL KNOWLEDGE ENGINE                      │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                     SOURCE LAYER (pluggable)                      │  │
│  │                                                                   │  │
│  │  Dev Sources                        Workload Sources              │  │
│  │  ┌─────────────────┐               ┌──────────────────────┐       │  │
│  │  │ Claude Code     │               │ Immutable Ledger     │       │  │
│  │  │ memories        │               │ (decision records)   │       │  │
│  │  ├─────────────────┤               ├──────────────────────┤       │  │
│  │  │ CLAUDE.md /     │               │ JSONL agent logs     │       │  │
│  │  │ AGENTS.md       │               │ (any format)         │       │  │
│  │  ├─────────────────┤               └──────────────────────┘       │  │
│  │  │ Session files   │                                              │  │
│  │  │ (handoff/park)  │               Adding a source =              │  │
│  │  ├─────────────────┤               one parser function            │  │
│  │  │ Cursor rules    │                                              │  │
│  │  │ (.cursorrules)  │                                              │  │
│  │  ├─────────────────┤                                              │  │
│  │  │ Any markdown    │                                              │  │
│  │  │ knowledge base  │                                              │  │
│  │  └─────────────────┘                                              │  │
│  └────────────────┬──────────────────────────────┬───────────────────┘  │
│                   │                              │                      │
│                   ▼                              ▼                      │
│  ┌────────────────────────────┐  ┌───────────────────────────────────┐  │
│  │    CLAIM EXTRACTOR         │  │    WORKLOAD DISTILLER             │  │
│  │                            │  │                                   │  │
│  │  Parse markdown:           │  │  Aggregate decisions:             │  │
│  │  ├─ opening paragraph      │  │  ├─ agent effectiveness           │  │
│  │  ├─ **Why:** section       │  │  ├─ signal landscape              │  │
│  │  ├─ **How to apply:**      │  │  ├─ multi-agent routing           │  │
│  │  ├─ bullet points          │  │  ├─ confidence distribution       │  │
│  │  └─ table rows             │  │  ├─ safety invariants             │  │
│  │                            │  │  ├─ namespace hotspots            │  │
│  │  Classify each claim:      │  │  └─ compression achieved          │  │
│  │  ├─ rule (imperative)      │  │                                   │  │
│  │  ├─ fact (measurements)    │  │  Each aggregate pattern           │  │
│  │  ├─ preference (user)      │  │  becomes one claim                │  │
│  │  ├─ decision (choice)      │  │                                   │  │
│  │  └─ caveat (temporal)      │  │                                   │  │
│  │                            │  │                                   │  │
│  │  Dedup: SHA256 + trigrams  │  │                                   │  │
│  │  Filter: skip secrets      │  │                                   │  │
│  └────────────┬───────────────┘  └───────────────┬───────────────────┘  │
│               │                                  │                      │
│               ▼                                  ▼                      │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    TOPIC FREQUENCY ANALYSIS                      │   │
│  │                                                                  │   │
│  │  20 regex topic patterns scan every claim:                       │   │
│  │  methodology, granite_xeon, openshift_deploy, governance, ...    │   │
│  │                                                                  │   │
│  │  Topics appearing in 3+ projects = institutional knowledge       │   │
│  │                                                                  │   │
│  │  ┌──────────────────────────────────────────────────┐            │   │
│  │  │  "granite_xeon"      → 35 projects               │            │   │
│  │  │  "methodology"       → 34 projects               │            │   │
│  │  │  "openshift_deploy"  → 37 projects               │            │   │
│  │  │  "oauth_security"    →  9 projects               │            │   │
│  │  │  ...22 topics total                              │            │   │
│  │  └──────────────────────────────────────────────────┘            │   │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │               LLM ENRICHMENT (optional, granite-8b)              │   │
│  │                                                                  │   │
│  │  Untagged rules/preferences → "shared or specific?"              │   │
│  │  92% of untagged rules classified as shared knowledge            │   │
│  │  Catches single-source-but-universal claims:                     │   │
│  │    "Keep Jira lean" (1 project, applies everywhere)              │   │
│  │    "No auto-remediation" (1 project, universal principle)        │   │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      OUTPUT LAYER                                │   │
│  │                                                                  │   │
│  │  ┌──────────────────────┐  ┌─────────────────────────────────┐   │   │
│  │  │  Dev Corpus          │  │  Workload Corpus                │   │   │
│  │  │                      │  │                                 │   │   │
│  │  │  Rules (174)         │  │  Agent effectiveness            │   │   │
│  │  │  Facts (1,353)       │  │  Signal landscape               │   │   │
│  │  │  Preferences (28)    │  │  Safety invariants              │   │   │
│  │  │  Decisions (7)       │  │  Compression metrics            │   │   │
│  │  │  Caveats (17)        │  │  Confidence patterns            │   │   │
│  │  │                      │  │                                 │   │   │
│  │  │  22 topics           │  │  Per-source analysis            │   │   │
│  │  │  1,011 institutional │  │                                 │   │   │
│  │  │  claims              │  │                                 │   │   │
│  │  │                      │  │                                 │   │   │
│  │  │  Audience:           │  │  Audience:                      │   │   │
│  │  │  developer / agent   │  │  ops / architecture             │   │   │
│  │  │  starting a session  │  │  capacity planning              │   │   │
│  │  └──────────────────────┘  └─────────────────────────────────┘   │   │
│  │                                                                  │   │
│  │  Formats: JSON (programmatic) + Markdown (human review)          │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Zero cascade framework changes. Same domain pack pattern as            │
│  K8s, AAP, finance, healthcare, insurance, retail, telecom.             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Source files (201 memories + CLAUDE.md + sessions + Cursor + ledger)
       │
       ▼
   memory_parsers.py / memory_parsers_workload.py
       │ each parser yields {path, name, memory_type, project, source_system, body}
       ▼
   ClaimExtractor.extract(record)
       │ splits body into individual claims
       │ classifies: rule / fact / preference / decision / caveat
       │ dedup: SHA256 exact + trigram near-match
       │ filters: skip secrets (sk-*, sha256~*, Bearer tokens)
       │ topics: regex match against 20 institutional patterns
       ▼
   MemorySignal (one per claim)
       │ maps to cascade Signal protocol
       │ signal_type = claim_type, severity = claim_severity
       ▼
   CascadePipeline.run() [optional — nano tier]
       │ severity gate drops info-level session noise
       │ dedup catches exact-match restated facts
       ▼
   LLM classification [optional — micro tier]
       │ "shared or specific?" for untagged claims
       ▼
   Corpus output
       ├── shared-knowledge-corpus.json (structured, for agents)
       └── shared-knowledge-corpus.md (readable, for humans)
```

### Adding a New Agent Framework

One function in `memory_parsers.py`:

```python
def scan_your_framework(data_dir: str, since: float = 0) -> Iterator[dict]:
    """Parse your agent's memory/config files."""
    for file in Path(data_dir).rglob("*.your_ext"):
        if since and file.stat().st_mtime < since:
            continue
        yield {
            "path": str(file),
            "name": file.stem,
            "description": "",
            "memory_type": "your_type",
            "project": file.parent.name,
            "source_system": "your-framework",
            "body": file.read_text(),
        }
```

Then add to collector config:
```python
collector.connect({"your_framework_dir": "/path/to/data"})
```

The ClaimExtractor, topic analysis, dedup, and LLM enrichment all work automatically on the new source. No framework changes needed.

### Proven Results

| Metric | Value |
|--------|------:|
| Claims extracted | 2,485 |
| Source types | 5 (Claude Code, CLAUDE.md, sessions, Cursor, ledger) |
| Projects scanned | 63 |
| Institutional topics | 22 |
| Institutional claims | 1,011 (40%) |
| Project-specific claims | 1,466 (60%) |
| LLM enrichment rate | 92% of untagged rules classified as shared |
| Workload claims | 8 (from 595K+ agent decisions) |
