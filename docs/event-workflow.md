# Event Workflow

## Signal Lifecycle

A signal enters the system and passes through four phases: ingestion, compression, classification, and feedback. Each phase is documented here with the exact decisions made at each step.

### Phase 1: Ingestion

The collector reads from a domain-specific data source and maps each record to the `Signal` protocol.

```
Data Source                          Signal
──────────                          ──────
K8s pod status        →   Signal(type="pod_crashloop", severity="high",
                                  source="nginx-abc123", namespace="production",
                                  content={"restarts": 47, "reason": "OOMKilled"})

AAP job event         →   Signal(type="task_runner_on_failed", severity="high",
                                  source="provision-cluster", namespace="dev-zr9r7",
                                  content={"task": "Create OpenShift cluster",
                                           "error": "connection refused"})

Bank transaction      →   Signal(type="wire_transfer", severity="medium",
                                  source="acct-12345", namespace="international",
                                  content={"amount": 50000, "destination": "offshore",
                                           "first_time": true})
```

The `Signal` dataclass fields:
- `signal_id` — UUID, unique per signal
- `signal_type` — string, domain-specific (e.g. "pod_crashloop", "task_runner_on_failed")
- `severity` — one of: info, low, medium, high, critical
- `source` — the resource that generated the signal
- `content` — dict, arbitrary evidence payload
- `labels` — dict, metadata for routing (e.g. {"domain": "aap"})
- `namespace` — grouping identifier
- `cluster` — source cluster/system identifier

### Phase 2: Compression (Nano Tier)

The `CascadePipeline` runs agents in stage order. Each agent processes the full batch and returns `CascadeDecision` objects for signals it can handle. Signals without a decision pass through.

```
Input: 1,000 signals
         │
    Stage 1: DeduplicateAgent
         │   Content hash + 60s window
         │   "Same pod crash event 3 seconds ago" → DEDUPE
         │   Result: 850 signals remain
         │
    Stage 1: TransientSuppressor
         │   Signal type + severity check
         │   "pod_restart at info severity" → SUPPRESS
         │   SAFETY: "pod_restart + OOMKilled" → KEEP (fail-open keyword match)
         │   Result: 400 signals remain
         │
    Stage 1: SeverityGate
         │   Drops info unless escalation pattern
         │   "info event, no keywords" → DROP
         │   "info event + 'panic' in content" → KEEP
         │   Result: 200 signals remain
         │
    Stage 2: PatternClassifier
         │   7 regex patterns (OOM, disk, CPU, network, crash, auth, scaling)
         │   Tags matching signals with classification
         │   Result: 200 signals remain (tagged, not dropped)
         │
    Stage 2: ThresholdClassifier
         │   Numeric extraction (CPU >80%, memory >95%, disk >90%)
         │   Tags signals that cross thresholds
         │   Result: 200 signals remain (tagged)
         │
    Stage 3: RepeatFloodSuppressor (if activated)
         │   "signal_type=event_deprecatedannotation seen 6,074 times" → SUPPRESS
         │   Result: 150 signals remain
         │
    Stage 3: DominantNoiseSuppressor (if activated)
         │   "job_succeeded is 92% of all signals" → SUPPRESS
         │   Result: 100 signals remain
         │
Output: CascadeResult
         │   survivors: 100 signals (10% of input)
         │   decisions: 1,000 CascadeDecision records
         │   compression_ratio: 0.90
```

### Phase 3: Classification (Micro Tier)

Surviving signals are forwarded to the LLM for classification. The LLM receives:

```
System: You are classifying [domain] signals. Classify as exactly
        one of: routine_noise, known_pattern, needs_attention,
        real_incident. Answer with one word only.

User:   Signal: task_runner_on_failed | Task: Create OpenShift
        cluster using Assisted Installer | Host: localhost |
        Job: RHPDS agd-v2.osac-cnv.dev-zr9r7-provision |
        Status: failed | Severity: high

LLM:    real_incident
```

Classification outcomes:
- `routine_noise` — safe to suppress in future. Feeds back to corpus analyzer.
- `known_pattern` — recurring issue, low priority. Logged but not escalated.
- `needs_attention` — investigate. Routed to monitoring dashboard.
- `real_incident` — immediate attention. Triggers alerts.

### Phase 4: Feedback Loop

LLM classifications feed back into the cascade to make it smarter:

```
Step 1: LLM classifies signal_type "event_deprecatedannotation" as routine_noise
        (this happens repeatedly — 50+ times)

Step 2: CorpusAnalyzer detects the pattern
        - "event_deprecatedannotation has been classified as noise 50 times"
        - "Frequency: 12% of all signals"
        - Proposes a draft RuleAgent:
          name: "classify_event_deprecatedannotation"
          rule: signal_type == "event_deprecatedannotation" → DROP

Step 3: PromotionEngine evaluates the draft
        - Checks: does suppressing this type miss real incidents?
        - Compares against baseline: 0 false negatives in 50 samples
        - Promotes to CANDIDATE tier

Step 4: After 200+ samples at 75%+ accuracy
        - Promotes to NANO tier
        - Agent is now ACTIVATED — runs in Stage 3
        - "event_deprecatedannotation" never reaches the LLM again

Result: Compression ratio increases. LLM handles fewer signals per cycle.
```

## Signal Routing

After classification, signals are routed by tier and lane:

```
                        CascadeRouter
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         MICRO TIER     MACRO TIER     ESCALATE
      (medium/low sev) (high/critical)  (to human)
              │              │
    ┌────┬────┼────┬────┐    │
    ▼    ▼    ▼    ▼    ▼    ▼
  Class Extr  Gen  Reas Emb  Larger
  Lane  Lane  Lane Lane Lane Model
```

Five inference lanes, each with optimized model selection:

| Lane | Task Types | Primary Models | Latency Target |
|------|-----------|----------------|----------------|
| Classification | fraud-scoring, dispute-classification, ticket-routing | gemma3-4b, phi4-mini | <3s |
| Extraction | document-extraction, policy-extraction, NER | phi4-mini, gemma3-4b | <4s |
| Generation | summarize, compliance-screening | phi4-mini, mistral-7b | <5s |
| Reasoning | underwriting-risk, churn-prediction, QA | phi4-mini, mistral-7b | <8s |
| Embedding | encode-text, similarity-search | nomic-embed-text, bge-small | <10ms |

## Batch Processing

The cascade processes signals in batches, not individually:

```
Poll interval: 30 seconds
Batch size: up to 500 signals per collect() call

Timeline:
  T+0s    Collector.collect() → 500 signals
  T+0.01s CascadePipeline.run(500 signals) → 25 survivors
  T+0.02s CascadeRouter.route(25 survivors) → 25 LLM requests
  T+15s   LLM finishes classifying (25 × 600ms)
  T+15s   CorpusAnalyzer.analyze() — check for new patterns
  T+15s   PromotionEngine.evaluate() — promote/demote agents
  T+30s   Next poll
```

## Historical Replay

On first deployment, the collector replays all historical data through the cascade:

```
  collector.collect_all()
       │
       ▼
  17,985 historical AAP jobs
  850,000 historical task events
  14,000 historical config changes
       │
       ▼ (batches of 500)
  CascadePipeline processes each batch
  CorpusAnalyzer discovers patterns
  PromotionEngine promotes agents
       │
       ▼
  After replay:
  - 7+ nano agents activated
  - Compression ratio established
  - Cascade is "warm" before first live signal
       │
       ▼
  Switch to live polling (30s intervals)
  Cascade is already smart on day one
```

## Governance Flow (Optional)

When a ledger URL is configured, cascade decisions flow through the governance pipeline:

```
CascadeBridge.process()
    │
    ├── Pipeline runs, decisions made
    │
    ├── LedgerClient.write_decisions()
    │   POST /api/receipts → decision.record entry
    │   (fire-and-forget — failures never block the pipeline)
    │
    └── Decision enters immutable ledger (hash-chained, append-only)

                    ┌─────────────────────────────────────┐
                    │  GCL Decision Sampler (independent)  │
                    │  Polls ledger every 60s              │
                    │  Samples 1% of drop decisions        │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │  Deterministic checks:               │
                    │  - High+ severity dropped? → FAILS   │
                    │  - Low confidence? → FAILS            │
                    │  - Dedup always passes (hash match)   │
                    └──────────────┬──────────────────────┘
                                   │ if FAILS
                    ┌──────────────▼──────────────────────┐
                    │  LLM adversary probe (granite-8b):   │
                    │  "Was this drop correct despite the  │
                    │   severity flag?"                     │
                    │  If LLM agrees → override to SURVIVES│
                    │  If LLM disagrees → stays FAILS      │
                    └──────────────┬──────────────────────┘
                                   │ only FAILS written
                    ┌──────────────▼──────────────────────┐
                    │  audit.verdict → Ledger              │
                    │  (SURVIVES are ephemeral, not stored) │
                    └─────────────────────────────────────┘
```

Three independent systems, none grading itself:
- Cascade decides (writes `decision.record`)
- Ledger records (append-only, hash-chained)
- GCL audits (writes `audit.verdict` for FAILS only)
