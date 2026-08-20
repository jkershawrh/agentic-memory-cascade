# CLAUDE.md — Cascade Compression

## What This Is

Three-tier cascade compression engine for CPU inference at scale.
Consolidates the cascade pipeline, routing engine, TCO calculator, and benchmark harness
into a single repo. Built for Intel FSI engagements — proves that most AI signals
don't need a GPU.

## Architecture

**Cascade Pipeline** (`cascade_compression/cascade/`)
- Nano tier (85-99%): Deterministic agents — dedup, transient suppression, severity gate, pattern/threshold classifiers
- Micro tier (1-15%): Small CPU models (360M-3B), classification/extraction lanes
- Macro tier (<1%): Larger CPU models (3.8B-8B), generation/reasoning lanes
- Signal protocol is domain-agnostic — K8s events, AAP job runs, or any structured signal
- Hardened promotion engine: zero-FN gate, instant demotion, cooling-off, PromotionEvent provenance
- Shadow validation: 5% of suppressed signals re-checked by LLM, disagreement triggers demotion
- Time-bounded activation: 72h TTL, agents expire and must re-qualify
- GCL verdict polling: FAILS verdicts trigger automatic demotion
- Optional human gate: pending_approval state for regulated environments

**Routing Engine** (`cascade_compression/routing/`)
- Benchmark-graded model selection via corpora (19 models, 6 industries, 5 lanes)
- Strategy router with 10 optimization profiles
- Workload bootstrapper (cosine similarity classification)
- Task mapping (14 deepfield types → 7 benchmark shapes)

**Memory Archive** (`cascade_compression/cascade/memory.py`)
- Survivors become institutional memory — strength-weighted, content-hash deduped
- Capacity-bounded (CASCADE_MEMORY_MAX, default 10K), evicts weakest when full
- Severity-weighted initial strength (info=0.1 → critical=1.0), exponential decay, asymptotic reinforcement
- MemoryEvent audit trail (formed/recalled/consolidated/evicted/federated)
- JSON Schema contracts in contracts/schemas/memory-record.json, memory-event.json
- API: GET /memories/stats, POST /memories/query, POST /recall

**Recall Engine** (`cascade_compression/cascade/recall.py`)
- Composite similarity: type match (0.4), label Jaccard (0.2), content feature cosine (0.2), text trigram (0.2)
- Score weighted by memory strength — stronger memories surface first
- Reinforcement on recall: strength increases, recall_count increments
- Performance: <50ms for 1,000 memories

**Consolidation** — periodic re-cascade of old memories through current pipeline
- Suppressed memories lose strength (−0.3), survivors gain consolidation_count (+0.05 boost)
- Below eviction threshold (0.05) → evicted. Noise decays, core memories persist.
- API: POST /consolidate

**Priming / Attention** — macro survivors temporarily escalate related signal types
- PrimingWindow: linear decay over configurable duration (default 4h)
- PrimingEscalator agent (stage 0): ONLY escalates, NEVER suppresses (fail-open safety)
- Window cap: max 10 concurrent, oldest evicted

**Federation** — cross-instance memory sharing
- Export/import: GET /memories/export, POST /memories/import
- Cross-source correlation: same content_hash from 2+ instances → strength boost
- Source provenance preserved via source_instance field
- 3 federated sources: cascade-k8s, cascade-aap, cascade-knowledge → cascade-memory aggregator

**Organizational Knowledge** (`cascade_compression/collectors/jira.py`, `git.py`, `confluence.py`)
- Jira: Atlassian Cloud REST API v3 (POST /search/jql), multi-project JQL
- Git: GitHub REST API with org-level repo discovery (GIT_ORG → auto-discovers all repos)
- Confluence: Confluence REST API v2, page revision analysis
- Knowledge domain pack (`cascade_compression/domains/knowledge.py`): causal rules for expertise_departure, documentation_gap, incident_repeat, runbook_decay, decision_revisited
- Signals: hotfix_pattern, decision_revisited, documentation_gap, runbook_decay, incident_learning, onboarding_question, code_review_repeat

**Infrastructure** (`cascade_compression/infra/`)
- Pressure-aware scaler (Linux PSI + cgroup v2, green/yellow/red rubric)
- Fleet manager (deployment planning, replica allocation, memory budgeting)

**TCO Calculator** (`cascade_compression/tco/`)
- Xeon 6 vs H100 vs Cloud API cost comparison
- Cascade compression math — proves 15-20x effective volume reduction
- FastAPI on port 8090 with frontend dashboard

**Benchmark Harness** (`cascade_compression/benchmarks/`)
- 9 optimization levers tested on Oberon (Xeon 6767P, 128c)
- Model shootout (13+ models), soak tests, fleet patterns
- Industry-standard prompts (ISO 20022, TMF621, ACORD, GS1, HL7 FHIR)

## Running Tests

```bash
make test-memory       # Memory archive + contracts
make test-cascade      # Cascade pipeline + safety + promotion
make test-routing      # Corpora, strategies, bootstrapper, routing
make test-infra        # Scaler, fleet manager
make test-tco          # TCO contracts, calculations, scenarios, API
make test-all          # Everything
```

## Running the App

```bash
make up                # FastAPI on port 8090
```

## Key Data

- `config/` — Strategies, verticals, workload profiles, scaler thresholds, lane prompts
- `data/` — Hardware profiles, workload profiles, model profiles, benchmark matrix
- `benchmarks/results/` — Raw benchmark JSON from Oberon cluster runs

## Methodology

CDD → TDD → EDD → BDD (Contract → Test → Event → Behavior Driven)

**Standalone Service** (`cascade_compression/service.py`)
- Single-container deployment via Containerfile
- FastAPI with /health, /stats, /cascade, /agents, /memories/*, /recall, /consolidate endpoints
- Real-time dashboard at / (frontend/index.html)
- OpenShift manifests: deploy/openshift.yaml (single), deploy/openshift-federated.yaml (K8s + AAP + Knowledge + aggregator)
- Collector manifests: deploy/collectors.yaml (15 collectors including Jira/Git/Confluence)
- Per-tier model routing: CASCADE_MICRO_MODEL (medium/low), CASCADE_MACRO_MODEL (critical/high)

## Running the Service

```bash
# Standalone service with dashboard
python3 -m uvicorn cascade_compression.service:app --port 8090

# With per-tier models
CASCADE_MICRO_MODEL=granite-2b-cpu CASCADE_MACRO_MODEL=granite-3-2-8b-instruct \
  python3 -m uvicorn cascade_compression.service:app --port 8090

# Federated deployment on OpenShift (K8s + AAP + memory aggregator)
oc apply -f deploy/openshift-federated.yaml
oc create secret generic cascade-llm \
  --from-literal=url=https://your-llm/v1 --from-literal=key=sk-... \
  -n cascade-compression
```

## Collectors

20 collectors registered in `cascade_compression/collector_sidecar.py`:

```bash
# Operational collectors → cascade-k8s / cascade-aap
python3 -m cascade_compression.collector_sidecar --mode=k8s --target=http://cascade-k8s:8090
python3 -m cascade_compression.collector_sidecar --mode=aap --target=http://cascade-aap:8090

# Knowledge collectors → cascade-knowledge
JIRA_PROJECT=GPTEINFRA,RHDP,CPEX \
  python3 -m cascade_compression.collector_sidecar --mode=jira --target=http://cascade-knowledge:8090 --interval=60
GIT_ORG=rhpds \
  python3 -m cascade_compression.collector_sidecar --mode=git --target=http://cascade-knowledge:8090 --interval=3600
python3 -m cascade_compression.collector_sidecar --mode=confluence --target=http://cascade-knowledge:8090 --interval=7200
```

Env vars: `ATLASSIAN_BASE_URL`, `ATLASSIAN_EMAIL`, `ATLASSIAN_API_TOKEN` (shared by Jira+Confluence), `GITHUB_TOKEN`, `GIT_ORG`.

## Next Steps

- Customer pilot (Amex via Ron) with hardened engine
- OCP Operator packaging (single Helm chart for cascade + governance)
- Edge deployment validation (robotics/IoT domain pack)
- Knowledge domain soak: causal graph linking Git hotfixes → Jira tickets → Confluence runbook gaps
