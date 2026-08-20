# Build governed memory for AI agents that never forgets

_Self-curating institutional memory for agentic systems, with zero tolerance for forgetting critical context._

## Table of Contents

- [Overview](#overview)
- [Who is this for](#who-is-this-for)
- [Example: AdTech campaign optimization agent](#example-adtech-campaign-optimization-agent)
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

An ad operations team asks their AI agent: "Which bid strategies worked for automotive campaigns in the Southeast last quarter, and which placements should we avoid?" The agent should answer from institutional memory, not re-analyze billions of bid events. It should know what strategies won auctions, what click patterns turned out to be fraud, and what placements triggered brand safety incidents, with a proof chain for every conclusion.

Today's AI agents can't do this. They lose context between interactions, overflow their context windows with raw bid logs, and can't explain what they remember or why. AdTech moves at millions of events per second with sub-100ms latency requirements. You can't call an LLM for every bid request.

The agentic memory cascade solves this. It ingests millions of signals per day, compresses 85-99% of noise deterministically, and retains only the survivors as institutional memory. Each memory is validated empirically (200+ samples, zero false negatives), decays naturally (72 hours, because yesterday's high-performing audience segment may not convert today), and is continuously re-verified by independent audit.

## Who is this for

- **AdTech platform engineers** building campaign optimization agents that must remember what bid strategies win auctions across millions of daily events without calling an LLM for every bid
- **Ad fraud investigators** building detection agents that must learn and remember click fraud patterns, bot traffic signatures, and impression fraud markers, and never forget a confirmed fraud pattern
- **Brand safety teams** building monitoring agents that must ensure an ad never appears next to harmful content, with an auditable memory of every placement decision
- **Media buying analysts** building programmatic agents that need bounded, relevant context about audience performance across campaigns without overwhelming a 128K context window with raw bid logs

## Example: AdTech campaign optimization agent

Here's what governed memory looks like for a programmatic advertising agent processing 2 million bid request signals per day:

**Day 1: Cold start.** The cascade has no learned memory. Every bid event goes to the LLM for classification: is this signal worth remembering (winning bid strategy, unusual click pattern, brand safety flag) or is it noise (routine no-bid, below-floor auction, standard impression)? The LLM processes all 2M signals. Slow and expensive.

**Day 3: Memory forms.** The corpus analyzer notices that bid requests from known low-viewability placements in the IAB "run of network" category are always classified as noise. It proposes a nano agent (a deterministic rule): "Suppress bid events from placements with viewability below 30% in run-of-network inventory." The agent is tested against 200+ real bid events with zero false negatives (it never suppressed a signal that turned out to be a winning strategy or fraud indicator) and promoted to active. Now those signals are filtered in sub-millisecond, never touching the LLM.

**Day 7: Memory curates.** 18 nano agents are active, compressing 88% of bid noise. The LLM only processes the 12% the cascade can't resolve: borderline placements, unusual click-to-conversion ratios, new publisher domains. A shadow validation check catches one agent that started missing a new click fraud pattern (bot traffic mimicking human scroll behavior on a previously clean publisher). That agent is instantly deactivated. The cascade learns from the correction.

**Day 30: Institutional knowledge.** The survivor archive contains a curated record of everything that mattered: winning bid strategies by audience segment, confirmed click fraud patterns, brand safety incidents by publisher, seasonal audience behavior shifts. When the media buyer asks "what worked for automotive in the Southeast?", the answer comes from the survivor archive. Every memory has a provenance chain: how it was learned, how many times it was validated, and whether the GCL audit loop confirmed or challenged it.

**What the brand safety auditor sees:** An immutable ledger entry for every memory decision. Which patterns are actively being suppressed, when each was promoted, their false-negative rate (always zero), whether any brand safety signal was ever incorrectly suppressed (never, or it would have been caught by shadow validation). Not "the AI decided." A proof chain that holds up in an advertiser review.

## Detailed description

The cascade maps directly to how memory works. Raw signals arrive as sensory input (bid events, click streams, impression logs). The nano tier (working memory) filters and pattern-matches, discarding most input at sub-millisecond latency, fast enough for real-time bidding. The micro tier (episodic memory) classifies notable events using a small CPU model. Macro survivors become semantic memory: a compressed, curated record of things that actually mattered.

The critical difference from a data warehouse: a data warehouse remembers every impression with no comprehension. It cannot tell you what mattered. The cascade can, because it learned what the LLM considers noise and encoded that knowledge as executable deterministic rules. The LLM only processes what the cascade cannot resolve (typically 0.007% of signals).

Each industry vertical is a "domain pack" -- a collector, a one-paragraph prompt, and historical data. The memory framework stays untouched. You choose your industry at deploy time.

### Architecture diagrams

![Agentic memory cascade architecture](docs/cascade-architecture.png)

```
Bid events -> [Encoding]  -> [Working Memory] -> [Episodic Memory] -> [Semantic Memory]
              Nano tier      Pattern match       CPU model classify    Survivor archive
              (88% filtered) (sub-ms, RTB-safe)  (~800ms)             (query by campaign)
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
- **Industry:** Broadcasting and cable
- **Product:** Red Hat OpenShift AI
- **Use case:** AI inference
- **Partner:** Intel
- **Contributor org:** Red Hat
