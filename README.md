# Build governed memory for AI agents that never forgets

_Self-curating institutional memory for agentic systems, with zero tolerance for forgetting critical context._

## Table of Contents

- [Overview](#overview)
- [Who is this for](#who-is-this-for)
- [Example: Financial services compliance agent](#example-financial-services-compliance-agent)
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

A compliance officer at a bank asks the AI agent: "What patterns has the system learned about wire transfers from high-risk jurisdictions this quarter?" The agent should answer from institutional memory, not re-analyze every transaction. It should know what it learned, when it learned it, why it still believes it, and what it chose to forget.

Today's AI agents can't do this. They lose context between interactions, overflow their context windows, and can't explain what they remember or why. Enterprise agents need governed memory: self-curating, auditable, with zero tolerance for forgetting what matters.

The agentic memory cascade solves this. It ingests millions of signals per day from any operational domain, compresses 85-99% of noise deterministically, and retains only the survivors as institutional memory. Each memory is validated empirically (200+ samples, zero false negatives), decays naturally (72 hours), and is continuously re-verified by independent audit.

## Who is this for

- **Financial services compliance teams** building AI agents that must remember every fraud pattern, explain every decision to regulators, and never forget a compliance signal
- **Healthcare platform engineers** building clinical AI that must retain critical patient safety context across interactions without overwhelming the context window
- **SRE and AIOps teams** building observability agents that learn from incidents and remember what signals preceded every outage, not just the most recent one
- **Insurance claims investigators** building agents that remember historical fraud patterns and surface them when similar claims appear months later

## Example: Financial services compliance agent

Here's what governed memory looks like for a compliance monitoring agent processing 500,000 transaction signals per day:

**Day 1: Cold start.** The cascade has no learned memory. Every signal goes to the LLM for classification. The LLM processes all 500K signals and classifies 92.7% of them as routine (not fraud, not compliance-relevant). Cost: high. Latency: slow.

**Day 3: Memory forms.** The cascade's corpus analyzer notices that wire transfers under $500 between domestic accounts with established history are always classified as routine. It proposes a nano agent (a deterministic rule) to handle this pattern. The agent is tested against 200+ samples with zero false negatives and promoted to active. Now those signals are filtered in sub-millisecond, never touching the LLM.

**Day 7: Memory curates.** 14 nano agents are active, compressing 61% of signals. The LLM only processes the 39% that the cascade can't resolve. A shadow validation check catches one agent that started missing a new pattern of structuring (splitting a large transfer into smaller ones). That agent is instantly deactivated, its samples zeroed, and the evidence chain written to the immutable ledger. The cascade learns from the correction.

**Day 30: Institutional knowledge.** The cascade has compressed 61% of routine noise. The survivor archive contains a curated record of every signal that actually mattered: confirmed fraud patterns, compliance violations, unusual jurisdiction activity, structuring attempts. When the compliance officer asks "what has the system learned about high-risk jurisdictions?", the answer comes from the survivor archive, not a log search. Every memory has a provenance chain: how it was learned, how many times it was validated, when it was last re-verified, and whether the GCL audit loop confirmed or challenged it.

**What the auditor sees:** An immutable ledger entry for every memory decision. Which agents are active, when they were promoted, their false-negative rate (always zero), their shadow validation history, and their GCL audit verdicts. Not "the AI said so." A proof chain.

## Detailed description

The cascade maps directly to how memory works. Raw signals arrive as sensory input. The nano tier (working memory) filters and pattern-matches, discarding most input at sub-millisecond latency. The micro tier (episodic memory) classifies notable events using a small CPU model. Macro survivors become semantic memory: a compressed, curated record of things that actually mattered.

The critical difference from a data lake: a data lake remembers everything with no comprehension. It cannot tell you what mattered. The cascade can, because it learned what the LLM considers noise and encoded that knowledge as executable deterministic rules. The LLM only processes what the cascade cannot resolve (typically 0.007% of signals).

Each industry vertical is a "domain pack" -- a collector, a one-paragraph prompt, and historical data. The memory framework stays untouched. You choose your industry at deploy time.

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

| Memory stage | Cascade mechanism | FSI example |
|---|---|---|
| **Encoding** | Corpus analyzer proposes a draft agent | "Domestic transfers under $500 with established history are always routine" |
| **Consolidation** | 5-tier promotion (draft -> candidate -> nano) | Agent tested against 200+ real transactions with 0% false negatives |
| **Recall** | Nano agent fires on matching signal | New $300 domestic transfer instantly classified as routine, no LLM call |
| **Forgetting** | 72h TTL + shadow demotion | Agent that missed a structuring pattern is deactivated in < 5 minutes |
| **Priming** | Threshold modulation after significant event | After a confirmed fraud, related signal types get lower suppression thresholds |

### Defense in depth

Five layers, none trusting each other:

| Layer | What it does | FSI relevance |
|---|---|---|
| **Zero-FN gate** | 200+ samples with 0% false negatives before a memory forms | No fraud signal is ever suppressed without proof it's safe |
| **Shadow validation** | 5% of suppressed signals re-checked by LLM | Catches drift in transaction patterns |
| **GCL audit loop** | Independent system samples 1%, writes verdicts to immutable ledger | Regulatory audit trail for every memory decision |
| **72h TTL** | Memories expire and must re-qualify against current data | Patterns that worked last week may not work this week |
| **Human gate** | Optional approval before memories form | Required for regulated environments (FINRA, PCI-DSS) |

One false negative from any source and the memory is instantly deactivated, samples zeroed, evidence chain written to the immutable ledger.

## See it in action

Deploy the cascade with the financial services domain pack and replay historical transaction data:

```bash
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
| **Financial services** | Transaction monitoring, fraud detection, compliance | 61.1% | 92.7% fraud recall, 100% compliance |
| **Healthcare** | Clinical signals, patient safety, diagnostic alerts | 91.0% | 96.6% critical recall, 99.0% compliance |
| **Insurance** | Claims monitoring, fraud patterns, risk signals | 81.2% | 100% fraud recall, 99.8% compliance |
| **Retail** | Inventory signals, shrinkage detection, demand patterns | 88.3% | 100% shrinkage recall, 100% compliance |
| **Telecom** | Network events, incident detection, capacity signals | 94.3% | 92.1% incident recall |
| **Kubernetes** | Production operations (validated on 142.4M signals) | 99.1% | 0 false negatives |
| **Org Knowledge** | Jira/Git/Confluence -- runbook decay, decision churn | 83% | Knowledge gap detection |

## CPU model leaderboard (Intel Xeon 6)

| Model | Score | Latency | Dangerous misses |
|---|---|---|---|
| granite-3-2-8b-instruct | 14/20 | 860ms | **0** |
| phi4-mini | 14/20 | 734ms | **0** |

Both models: every error is over-escalation (safe failure), never dismissal. Runs entirely on CPU, no GPU required.

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

**Option A: Deploy on Red Hat OpenShift (financial services)**

```bash
oc new-app https://github.com/jkershawrh/agentic-memory-cascade \
  -e CASCADE_DOMAIN=finance \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...
```

**Option B: Deploy with a different domain**

```bash
# Healthcare
oc new-app https://github.com/jkershawrh/agentic-memory-cascade \
  -e CASCADE_DOMAIN=healthcare \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...

# Kubernetes operations
oc new-app https://github.com/jkershawrh/agentic-memory-cascade \
  -e CASCADE_DOMAIN=kubernetes \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...
```

**Option C: Run locally with historical data**

```bash
git clone https://github.com/jkershawrh/agentic-memory-cascade.git
cd agentic-memory-cascade
pip install -e ".[dev]"

# Replay financial services transactions
cascade-replay --domain finance --data transactions.csv --llm-url https://your-llm/v1

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
- **Industry:** Banking and securities
- **Product:** Red Hat OpenShift AI
- **Use case:** AI inference
- **Partner:** Intel
- **Contributor org:** Red Hat
