# Build governed memory for AI agents that never forgets

_Self-curating institutional memory for agentic systems, with zero tolerance for forgetting critical context._

## Table of Contents

- [Overview](#overview)
- [Who is this for](#who-is-this-for)
- [What can you point it at](#what-can-you-point-it-at)
- [Validated on production data](#validated-on-production-data)
- [Example: Watch memory form](#example-watch-memory-form)
- [Detailed description](#detailed-description)
  - [Architecture diagrams](#architecture-diagrams)
  - [How memory forms](#how-memory-forms)
  - [Defense in depth](#defense-in-depth)
- [See it in action](#see-it-in-action)
- [Requirements](#requirements)
  - [Minimum hardware requirements](#minimum-hardware-requirements)
  - [Minimum software requirements](#minimum-software-requirements)
  - [Required user permissions](#required-user-permissions)
- [Deploy](#deploy)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Validating the deployment](#validating-the-deployment)
  - [Delete](#delete)
- [Repository structure](#repository-structure)
- [References](#references)
- [Tags](#tags)

## Overview

Your AI agent processes millions of signals a day. It classifies transactions, monitors infrastructure, triages alerts, screens content. Ask it tomorrow what it learned today and it has no answer. Its context window overflowed hours ago. The lessons it extracted are gone.

AI agents need institutional memory: a system that decides what matters, retains it, forgets what doesn't, and proves every decision to auditors. Not a data lake that remembers everything with no comprehension. A memory that curates itself.

The agentic memory cascade solves this. It ingests millions of signals per day, compresses 85-99% of noise deterministically, and retains only the survivors as institutional memory. Each memory is validated empirically (200+ samples, zero false negatives), decays naturally (72 hours), and is continuously re-verified by independent audit. The LLM only processes what the cascade cannot resolve -- typically 0.007% of signals.

## Who is this for

- **Platform engineers** building AI agents that must remember what happened across millions of daily events without calling an LLM for every signal
- **Compliance and risk teams** needing auditable proof of what an AI system learned, forgot, and why -- with an immutable ledger trail
- **SRE and AIOps teams** building observability agents that learn from incidents and carry that knowledge forward, not just the most recent alert
- **Anyone drowning in signal noise** -- if your agents process more data than fits in a context window, the cascade gives them bounded, relevant, governed memory

## What can you point it at

Anything that produces a stream of signals. The cascade doesn't care what the signals are. It learns what matters and what doesn't.

| Signal source | What the cascade learns | What memory looks like |
|---|---|---|
| **Factory floor sensors** | Which temperature, vibration, and pressure readings are normal operating range | "Vibration on Line 3 between 2.1-2.8mm/s is always normal" -- agent filters 94% of readings |
| **Robotic arm telemetry** | Which motion patterns indicate routine operation vs calibration drift | "Joint 4 torque variance under 0.3Nm during pick-place is normal" -- surfaces only anomalies |
| **Agent output / decision logs** | Which decisions the agent makes repeatedly vs which are novel | "Routing to tier-1 support for password resets is always the right call" -- auto-routes 80% |
| **Financial transactions** | Which transaction patterns are routine vs suspicious | "Domestic transfers under $500 with established history are always routine" -- flags only anomalies |
| **Kubernetes events** | Which pod restarts, OOM kills, and scaling events are normal churn | "CrashLoopBackOff on init containers in ci-runners namespace is always transient" -- 99.1% compressed |
| **Healthcare alerts** | Which clinical signals are routine vs require attention | "Heart rate 60-100 bpm with stable trend is normal" -- surfaces only deviations |
| **IoT / edge devices** | Which readings are within operating parameters vs indicate failure | "Humidity sensor reads between 40-60% RH during business hours" -- alerts only on drift |

The domain pack is three files: a collector (how to read the signals), a one-paragraph prompt (what "matters" means in this domain), and seed data. The cascade framework stays untouched.

## Validated on production data

This isn't theoretical. The cascade has been validated against real production signal streams:

| Domain | Source | Signals processed | Compression | What it proved |
|---|---|---|---|---|
| **Kubernetes** | Production cluster replay | **142.4 million** | **99.1%** | 3 agents activated. 0 false negatives. LLM classified 9,685 of 142M signals (0.007%). |
| **Ansible (AAP)** | Live platform + replay | **553,000** | **98.1%** | 63 shadow demotions (the system caught its own mistakes and corrected). |
| **Org Knowledge** | Live Jira, GitHub (819 repos), Confluence | ongoing | **83%** | Surfaced runbook decay, decision churn (16+ comment tickets), hotfix/revert patterns, expertise concentration. |

The hardened engine ran with all five safety layers active: zero-FN gate, 5% shadow validation, GCL audit loop, 72h TTL, and human gate. The Kubernetes run processed 142 million signals and the LLM only needed to see 9,685 of them. Everything else was handled by three deterministic agents that the cascade discovered, validated, and promoted on its own.

The organizational knowledge domain applied the same cascade to non-operational signals: Jira tickets, GitHub commits and PRs across 819 repositories, and Confluence pages. It compressed 83% of routine activity (status updates, regular commits, standard ticket flow) and surfaced the signals that indicate knowledge gaps, process decay, and expertise concentration.

## Example: Watch memory form

Run the demo and watch the dashboard. Here's what happens:

**Minute 0-1: Cold start.** The cascade has no learned memory. Every signal goes to the LLM for classification. Compression is 0%. Everything is new.

**Minute 1-2: First pattern emerges.** The corpus analyzer notices a recurring signal type that the LLM always classifies as noise. It proposes a draft agent -- a deterministic rule to handle this pattern. The agent appears on the Memory Map tab.

**Minute 2-3: Memory forms.** The draft agent hits 200 samples with zero false negatives. It's promoted to active. Compression jumps. On the dashboard, the compression gauge climbs and the agent card shows "nano" tier.

**Minute 3-4: More agents activate.** The cascade discovers more patterns. Each agent handles one type of noise. Compression climbs to 60-85%. The LLM is only processing what the cascade genuinely can't resolve.

**Minute 4-5: Self-correction.** Shadow validation catches an agent that started missing a new pattern. The agent is instantly deactivated -- you see it strikethrough on the Memory Map with the reason. The cascade learns from the correction and tightens its rules.

**Minute 5+: Institutional knowledge.** The survivor archive now contains a curated record of everything that mattered. Query it: "What has the system learned?" The answer is a compressed narrative of significant events, not a log search.

```bash
./demo.sh              # See it live — no LLM needed, runs on a laptop
```

## Detailed description

The cascade maps directly to how memory works. Raw signals arrive as sensory input. The nano tier (working memory) filters and pattern-matches, discarding most input at sub-millisecond latency. The micro tier (episodic memory) classifies notable events using a small CPU model. Macro survivors become semantic memory: a compressed, curated record of things that actually mattered.

The critical difference from a data lake: a data lake remembers everything with no comprehension. It cannot tell you what mattered. The cascade can, because it learned what the LLM considers noise and encoded that knowledge as executable deterministic rules. The LLM only processes what the cascade cannot resolve (typically 0.007% of signals).

Each vertical is a "domain pack" -- a collector, a one-paragraph prompt, and historical data. The memory framework stays untouched. You choose your domain at deploy time, or write your own in three files.

### Architecture diagrams

![Agentic memory cascade architecture](docs/cascade-architecture.png)

```
Any signal -> [Encoding]  -> [Working Memory] -> [Episodic Memory] -> [Semantic Memory]
              Nano tier      Pattern match       CPU model classify    Survivor archive
              (85-99%)       (sub-ms)            (~800ms)             (query anytime)
                   ^                                    |
                   +---- Shadow validation (5%) --------+
                   ^                                    |
                   +---- GCL audit (1%) ---------------+
                   ^                                    |
                   +---- 72h decay + re-qualify -------+
```

### How memory forms

| Memory stage | Cascade mechanism | AdTech example |
|---|---|---|
| **Encoding** | Corpus analyzer proposes a draft agent | "Run-of-network bids with viewability < 30% are always noise" |
| **Consolidation** | 5-tier promotion (draft -> candidate -> nano) | Agent tested against 200+ real bid events with 0% false negatives |
| **Recall** | Nano agent fires on matching signal | New low-viewability bid instantly suppressed, no LLM call needed |
| **Forgetting** | 72h TTL + shadow demotion | Audience behaviors shift fast -- yesterday's pattern may not hold |
| **Priming** | Threshold modulation after significant event | After confirmed click fraud, related publisher patterns get lower suppression thresholds |

### Defense in depth

Five layers, none trusting each other:

| Layer | What it does | AdTech relevance |
|---|---|---|
| **Zero-FN gate** | 200+ samples with 0% false negatives before a memory forms | No fraud signal or brand safety event is ever suppressed without proof |
| **Shadow validation** | 5% of suppressed signals re-checked by LLM | Catches evolving bot traffic patterns and new fraud techniques |
| **GCL audit loop** | Independent system samples 1%, writes verdicts to immutable ledger | Audit trail for brand safety reviews and advertiser disputes |
| **72h TTL** | Memories expire and must re-qualify against current data | AdTech patterns shift daily -- stale rules cost money |
| **Human gate** | Optional approval before memories form | Required for high-value campaigns or sensitive brand categories |

One false negative from any source and the memory is instantly deactivated, samples zeroed, evidence chain written to the immutable ledger.

## See it in action

Deploy the cascade with any domain pack and replay historical data:

```bash
# AdTech signals (use retail domain pack for e-commerce advertising)
cascade-replay --domain retail --data campaign_events.csv --llm-url https://your-llm/v1

# Or financial services, healthcare, telecom, kubernetes...
cascade-replay --domain finance --data transactions.csv --llm-url https://your-llm/v1
```

The real-time dashboard at `http://localhost:8090` shows:
- Active memory agents and their promotion history
- Compression rate over time (watch it climb as agents activate)
- Survivor archive with classification metadata
- Shadow validation results and demotion events
- LLM usage rate (should drop toward 0.007% as memory forms)

## Industry domain packs

Choose your industry at deploy time. Each domain is a collector, a one-paragraph prompt, and historical data.

| Domain | Scenario | Compression | Safety guarantee |
|---|---|---|---|
| **Retail** | Campaign signals, ad performance, audience behavior | 88.3% | 100% shrinkage recall, 100% compliance |
| **Financial services** | Transaction monitoring, fraud, compliance | 61.1% | 92.7% fraud recall, 100% compliance |
| **Healthcare** | Clinical signals, patient safety, diagnostic alerts | 91.0% | 96.6% critical recall, 99.0% compliance |
| **Insurance** | Claims monitoring, fraud patterns, risk signals | 81.2% | 100% fraud recall, 99.8% compliance |
| **Telecom** | Network events, incident detection, capacity signals | 94.3% | 92.1% incident recall |
| **Kubernetes** | Production operations (validated on 142.4M signals) | 99.1% | 0 false negatives |
| **Org Knowledge** | Jira/Git/Confluence -- runbook decay, decision churn | 83% | Knowledge gap detection |

> **Building a custom domain pack?** See the [Domain Pack Guide](docs/domain-pack-guide.md). A domain pack is three files: a collector, a one-paragraph prompt, and seed data. The cascade framework stays untouched.

## CPU model leaderboard (Intel Xeon 6)

| Model | Score | Latency | Dangerous misses |
|---|---|---|---|
| granite-3-2-8b-instruct | 14/20 | 860ms | **0** |
| phi4-mini | 14/20 | 734ms | **0** |

Both models: every error is over-escalation (safe failure), never dismissal. Runs entirely on CPU, no GPU required. Nano tier is sub-millisecond -- fast enough for real-time bidding pipelines.

> **Powered by Intel** -- This quickstart runs on Intel Xeon processors with CPU-optimized inference.

## Requirements

### Minimum hardware requirements

| Component | Minimum |
|---|---|
| CPU cores | 4 (Intel Xeon recommended) |
| Memory | 8 GiB |
| Storage | 10 GiB |

### Minimum software requirements

| Requirement | Version |
|---|---|
| Python | 3.9+ |
| Red Hat OpenShift | 4.14 or later |
| LLM endpoint | Any OpenAI-compatible API |

### Required user permissions

This quickstart can be deployed by a regular user with namespace-level permissions.

## Deploy

### Prerequisites

- An OpenAI-compatible LLM endpoint (vLLM, Ollama, or cloud API)
- An API key for the LLM endpoint

### Installation

**Option A: Deploy on Red Hat OpenShift**

```bash
oc new-app https://github.com/jkershawrh/agentic-memory-cascade \
  -e CASCADE_DOMAIN=retail \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...
```

**Option B: Deploy with a different domain**

```bash
# Financial services
oc new-app https://github.com/jkershawrh/agentic-memory-cascade \
  -e CASCADE_DOMAIN=finance \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...

# Healthcare
oc new-app https://github.com/jkershawrh/agentic-memory-cascade \
  -e CASCADE_DOMAIN=healthcare \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...
```

**Option C: Run locally with historical data**

```bash
git clone https://github.com/jkershawrh/agentic-memory-cascade.git
cd agentic-memory-cascade
pip install -e ".[dev]"

# Replay ad campaign data
cascade-replay --domain retail --data campaign_events.csv --llm-url https://your-llm/v1

# Or run live with Kubernetes signals
cascade-run --domain kubernetes --llm-url https://your-llm/v1 --llm-key sk-...
```

**Start the real-time dashboard:**

```bash
python3 -m uvicorn cascade_compression.service:app --port 8090
# Open http://localhost:8090
```

### Validating the deployment

```bash
# Run the full test suite (776 tests)
make test-all

# Check cascade status
curl -s http://localhost:8090/stats | python3 -m json.tool
```

### Delete

```bash
oc delete all -l app=agentic-memory-cascade
```

## Repository structure

```
.
├── cascade_compression/          # Core engine
│   ├── cascade/                  # Pipeline, agents, promotion, memory, recall
│   ├── collectors/               # 20 collectors (k8s, aap, jira, git, confluence, ...)
│   ├── domains/                  # 10 domain packs (choose at deploy time)
│   ├── routing/                  # Benchmark-graded model selection
│   ├── tco/                      # TCO calculator
│   ├── infra/                    # Pressure-aware scaler, fleet manager
│   ├── integrations/             # Immutable ledger client
│   ├── service.py                # FastAPI service with dashboard
│   └── cli.py                    # cascade-run, cascade-replay entrypoints
├── benchmarks/                   # Harness, configs, corpora, results
├── contracts/                    # OpenAPI specs + JSON schemas (memory-event, memory-record)
├── docs/                         # Architecture, whitepaper, memory formation model
├── tests/                        # 776 tests
├── Containerfile                 # UBI9 Python 3.11
├── Makefile
├── LICENSE
└── README.md
```

## References

- [Cascade as Memory](docs/cascade-as-memory.md) -- how the cascade forms institutional knowledge
- [Memory Whitepaper](docs/cascade-as-memory-whitepaper.md) -- full thesis with biological analogs
- [Architecture](docs/architecture.md) -- technical deep-dive
- [Model Benchmarks](docs/model-benchmarks.md) -- 6-model comparison on Xeon 6
- [Domain Pack Guide](docs/domain-pack-guide.md) -- add a new domain in three files
- [Promotion Guidelines](docs/promotion-guidelines.md) -- how memories form and decay

## Tags

- **Title:** Build governed memory for AI agents that never forgets
- **Description:** Self-curating institutional memory for agentic systems, with zero tolerance for forgetting critical context
- **Industry:** Media and IT services
- **Product:** Red Hat OpenShift AI
- **Use case:** AI inference
- **Partner:** Intel
- **Contributor org:** Red Hat
