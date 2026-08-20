# Build governed memory for AI agents that never forgets

_Self-curating institutional memory for agentic systems, with zero tolerance for forgetting critical context._

## Table of Contents

- [Overview](#overview)
- [Detailed description](#detailed-description)
  - [Architecture diagrams](#architecture-diagrams)
  - [How memory forms](#how-memory-forms)
  - [Defense in depth](#defense-in-depth)
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

AI agents lose critical context. They overflow their context windows, can't explain what they remember or why, and forget lessons learned between interactions. Enterprise agents need something better: governed memory that curates itself, retains what matters, forgets what doesn't, and proves to auditors exactly why every memory decision was made.

The agentic memory cascade solves this by applying the same architecture biological memory uses. It ingests millions of signals per day, compresses 85-99% of noise deterministically, and retains only the survivors as institutional memory. Each memory is validated empirically (200+ samples, zero false negatives), decays naturally (72-hour TTL), and is continuously re-verified by independent audit. The result: agents get bounded, relevant context without fabricating consensus or overwhelming their context windows.

## Detailed description

The cascade maps directly to how memory works. Raw signals arrive as sensory input. The nano tier (working memory) filters and pattern-matches, discarding most input at sub-millisecond latency. The micro tier (episodic memory) classifies notable events using a small CPU model. Macro survivors become semantic memory: a compressed, curated record of things that actually mattered.

The critical difference from a data lake: a data lake remembers everything with no comprehension. It cannot tell you what mattered. The cascade can, because it learned what the LLM considers noise and encoded that knowledge as executable deterministic rules. The LLM only processes what the cascade cannot resolve (typically 0.007% of signals).

### Architecture diagrams

![Agentic memory cascade architecture](docs/cascade-architecture.png)

```
Signals -> [Encoding] -> [Working Memory] -> [Episodic Memory] -> [Semantic Memory]
           Nano tier     Pattern match       CPU model classify    Survivor archive
           (99% filtered) (sub-ms)           (~800ms)             (permanent)
                ^                                    |
                +---- Shadow validation (5%) --------+
                ^                                    |
                +---- GCL audit (1%) ---------------+
                ^                                    |
                +---- 72h decay + re-qualify -------+
```

### How memory forms

| Memory stage | Cascade mechanism | What happens |
|---|---|---|
| **Encoding** | Promotion engine | Corpus analyzer detects a recurring pattern and proposes a draft agent |
| **Consolidation** | 5-tier promotion ladder | Draft -> candidate -> nano -> micro -> macro. Each tier requires more evidence. |
| **Recall** | Nano agent pattern matching | Every agent that fires is performing recall: "I have seen this before and know what it means" |
| **Forgetting** | 72h TTL + demotion | Patterns that don't recur expire. Wrong memories are actively unlearned via shadow validation. |
| **Priming** | Threshold modulation | After a significant event, attention thresholds lower for related signal types |

### Defense in depth

Five layers, none trusting each other:

| Layer | What it does |
|---|---|
| **Zero-FN gate** | 200+ samples with 0% false negatives before a memory forms |
| **Shadow validation** | 5% of suppressed signals re-checked by LLM |
| **GCL audit loop** | Independent system samples 1%, writes verdicts to immutable ledger |
| **72h TTL** | Memories expire and must re-qualify against current data |
| **Human gate** | Optional approval before memories form (for regulated environments) |

One false negative from any source and the memory is instantly deactivated, samples zeroed, evidence chain written to the immutable ledger.

## Industry domain packs

Each domain is a collector, a one-paragraph prompt, and historical data. The memory framework stays untouched.

| Domain | Data source | Compression | Safety |
|---|---|---|---|
| Kubernetes | Production replay (142.4M signals) | 99.1% | 0 false negatives |
| Ansible (AAP) | Live + replay (553K signals) | 98.1% | 63 shadow demotions |
| Financial services | Synthetic | 61.1% | 92.7% fraud, 100% compliance |
| Healthcare | Synthetic | 91.0% | 96.6% critical, 99.0% compliance |
| Insurance | Synthetic | 81.2% | 100% fraud, 99.8% compliance |
| Retail | Synthetic | 88.3% | 100% shrinkage, 100% compliance |
| Telecom | Synthetic | 94.3% | 92.1% incidents |
| Org Knowledge | Live (Jira/Git/Confluence) | 83% | Runbook decay, decision churn |

## CPU model leaderboard (Xeon 6)

| Model | Score | Latency | Dangerous misses |
|---|---|---|---|
| granite-3-2-8b-instruct | 14/20 | 860ms | **0** |
| phi4-mini | 14/20 | 734ms | **0** |

Both models: every error is over-escalation (safe failure), never dismissal.

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
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...
```

**Option B: Run locally**

```bash
git clone https://github.com/jkershawrh/agentic-memory-cascade.git
cd agentic-memory-cascade
pip install -e ".[dev]"
cascade-run --domain kubernetes --llm-url https://your-llm/v1 --llm-key sk-...
```

**Option C: Start the dashboard**

```bash
python3 -m uvicorn cascade_compression.service:app --port 8090
```

### Validating the deployment

```bash
make test-all
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
│   ├── domains/                  # 10 domain packs
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
