# Shared Knowledge Corpus

Auto-extracted from 63 projects, 2477 claims across 3 source types.
Institutional topics: 22 (appearing in 3+ projects).

## Openshift Deploy (37 projects, 154 unique claims)

**Rules:**
- The system is still being validated. User is building toward auto-remediation but is not there yet. Read-only diagnostics (oc get/describe/logs) are fine. Any mutation (oc rollout restart, oc delete, 
- Requires namespace-specific claims naming the sandbox and failure cause
- Show trust boundaries (TDX enclave, namespace isolation, policy gates)
- Before any deploy to infra01:
1. Verify the deployment has an oauth-proxy sidecar container
2. Verify the route targets the oauth-proxy port (8080/8443/4180), not the backend directly
3. Test with `cu
- 1. **No work during European hours** — cluster is shared with European teams. Only use outside EU business hours.
2. **Always verify nothing is running before starting** — check `oc get pods --all-nam

**Key facts:**
- granite-2b-cpu → `granite-2b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- phi3-mini-cpu → `phi3-mini-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- qwen25-3b-cpu → `qwen25-3b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- deepseek-r1-distill-qwen-14b → `deepseek-...-direct-llm-hosting.apps.ocp-rac-maas...`
- microsoft-phi-4 → `microsoft-phi-4-direct-llm-hosting.apps.ocp-rac-maas...`

---

## Maas Inference (36 projects, 158 unique claims)

**Rules:**
- phi-4 via LiteLLM (maas-rhdp) is the right backend for Gate 2 classification. The zero-shot encoder (distilbert-mnli) guesses at 30-46% confidence. phi-4 classifies decisively at 270-314ms with the ri
- LiteLLM endpoint: `LITELLM_API_BASE=https://maas-rhdp.apps.maas.redhatworkshops.io`
- Replace the encoder-service HTTP calls in `_run_gate2()` with LiteLLM calls to phi-4. The domain pack YAML provides the system prompt context. The encoder pod can be scaled down or repurposed.

[[proj
- In all showroom labs, demo frontends, READMEs, and presentation materials: use "Powered by Intel" or "Intel Xeon" / "Intel Xeon 6". The MaaS model map may reference `gpt-oss-120b-gaudi` as a technical
- Explain the WHY before the HOW (why vLLM on CPU, why this architecture pattern)

**Key facts:**
- Focus is CPU-only (Xeon 6) for now. Gaudi 3 GPU support is future scope. Changes to the planner are data-driven (gpu_catalog.json, slo_templates.json, benchmark data) rather than code changes. A stand
- Hardware detection method: LiteLLM `/v1/model/info` shows backend `api_base` URLs.
- `-external-` route + `-cpu` suffix = Xeon 6 (OpenVINO runtime)
- `-direct-` route (no `-cpu`) = Gaudi 3 (vLLM runti
- granite-2b-cpu → `granite-2b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- phi3-mini-cpu → `phi3-mini-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- qwen25-3b-cpu → `qwen25-3b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`

**Decisions:**
- The entire "one model, one silicon" narrative assumed granite-3-2-8b ran on Xeon 6. It doesn't. Need to either get it onto rac-maas Xeon CPU or pivot to existing CPU models (granite-2b-cpu is the clos
- redhat-et/semantic_router (archived Aug 2025) → migrated to vllm-project/semantic-router

---

## Granite Xeon (35 projects, 152 unique claims)

**Rules:**
- All work should reinforce "one model, one silicon" — granite-3-2-8b-instruct on Xeon 6 only. No Gaudi references in new work. Branding must be Intel + Red Hat co-branded.

Related: [[reference-repos]]
- Use "Powered by Intel" as the brand message. Do NOT reference "Intel Gaudi" in branding. "Intel Xeon 6" is acceptable.
- In all showroom labs, demo frontends, READMEs, and presentation materials: use "Powered by Intel" or "Intel Xeon" / "Intel Xeon 6". The MaaS model map may reference `gpt-oss-120b-gaudi` as a technical
- Intel Hardware section on the index page (component → Intel Xeon table)
- Don't fold RACM infrastructure optimization into the porting work. The two tracks are: (1) port quickstarts to showroom + benchmark MaaS models (our pipeline), (2) optimize how RACM serves models on X

**Key facts:**
- Focus is CPU-only (Xeon 6) for now. Gaudi 3 GPU support is future scope. Changes to the planner are data-driven (gpu_catalog.json, slo_templates.json, benchmark data) rather than code changes. A stand
- Hardware detection method: LiteLLM `/v1/model/info` shows backend `api_base` URLs.
- `-external-` route + `-cpu` suffix = Xeon 6 (OpenVINO runtime)
- `-direct-` route (no `-cpu`) = Gaudi 3 (vLLM runti
- granite-2b-cpu → `granite-2b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- **Deepfield** (namespace: deepfield) — real-time signal processing
- 5 clusters configured via CLUSTER_N_* env vars (infra/AWS clusters removed 2026-08-04)
- Signal funnel: ~200K raw/hr → reasoning ta
- Multi-agent orchestration system to be deployed on OpenShift AI (Red Hat AI 3.4+). Inference on Intel Xeon 6 and Gaudi accelerators. Will use Red Hat's AgentOps stack: OpenTelemetry distributed tracin

**Decisions:**
- Red Hat–Intel AI inference partnership. Strategic direction pivoted on 2026-06-15 from "Xeon-first dual-path" to **"One model, one silicon, every lab"** — consolidating ALL Summit Connect labs to gran
- The entire "one model, one silicon" narrative assumed granite-3-2-8b ran on Xeon 6. It doesn't. Need to either get it onto rac-maas Xeon CPU or pivot to existing CPU models (granite-2b-cpu is the clos
- Consolidating all Summit Connect labs to granite-3-2-8b-instruct on Intel Xeon 6. Validated across 25 repos, 135 quality probes, all pass.

---

## Methodology (34 projects, 72 unique claims)

**Rules:**
- Follow the CDD then TDD then BDD then EDD methodology with red/green rubric grids for all milestones.
- Every milestone must have contracts defined before logic, tests written before implementation (red first), BDD Given/When/Then scenarios, and an EDD rubric grid. verify.sh must exit 0 before a milesto
- Don't just write code and move on — follow a TDD/EDD red/green cycle. Run the verification tests first to see what's RED, then work to make each check GREEN.
- For each document phase:
1. Write the tests/verification first (or use the ones already specified in the docs)
2. Run them — see the red/green matrix
3. Fix what's red, one check at a time
4. Report t
- Always include a multi-stage gated validation matrix (CDD → TDD → EDD → BDD → CBT → Publication) with claim registries for provenance tracking.

**Key facts:**
- All work for the multimodal agent pack targets the `deepfield-multimodal` repo. The original `deepfield` repo is read-only reference for architectural patterns. The spec lives at `/Users/jkershaw/Docu
- **Deepfield EDD rubrics still show some FAILING** — routing_quality and fleet_health need the inference router subsystem (not installed) or more sophisticated fallback
- The red/green verification matrix has "Local" (Mac, simulated) and "Target" (Folsome, real) columns
- `analysis/evaluator.py` — `score_routing()` added as 6th EDD rubric dimension (workload_detection, strategy_grade, model_quality/latency/throughput, corpora_coverage, fallback_rate)
- /Users/jkershaw/.claude/plans/how-does-that-apply-imperative-donut.md has the full design with Options 1/2/3, rubric additions, and phased implementation.

---

## Demo Platform (31 projects, 192 unique claims)

**Rules:**
- Don't create the agnosticv config manually. Run the liftoff onboard pipeline first to right-size the deployment — it scans the repo, classifies it, generates the agnosticv config, and validates readin
- The liftoff pipeline (at /Users/jkershaw/Documents/liftoff) is the standard path from repo → RHDPS-ready. It runs NovaScan (capacity), DarkScope (security), and LLM classification to generate the corr
- 1. Finish the Antora showroom content (content/ directory with lab pages)
2. Run `liftoff onboard` against the sovereign-ai-lab repo
3. Review the generated agnosticv config
4. Adjust if needed, then 
- phi-4 via LiteLLM (maas-rhdp) is the right backend for Gate 2 classification. The zero-shot encoder (distilbert-mnli) guesses at 30-46% confidence. phi-4 classifies decisively at 270-314ms with the ri
- LiteLLM endpoint: `LITELLM_API_BASE=https://maas-rhdp.apps.maas.redhatworkshops.io`

**Key facts:**
- llm-d-planner (cloned to InferencePlan/llm-d-planner/) is being adapted as a CPU inference capacity planner for the Intel Red Hat partner demo platform.
- The demo platform has no capacity planning — replicas, concurrency, and rate limits are all hardcoded. The planner provides SLO-driven sizing backed by real benchmark data.
- **StarGate** (namespace: stargate) — batch readiness scanner + remediation pipeline
- API: 2 replicas + oauth-proxy, Frontend: nginx + oauth-proxy, Scanner: scheduler + babylon-worker, Postgres
- Scan
- LLM: llama-scout-17b via LiteLLM at maas-rhdp.apps.maas.redhatworkshops.io
- Works at Red Hat on the demo platform. Building a multi-agent orchestration system on OpenShift AI. Has access to Intel Xeon 6 and Gaudi accelerator servers for inference. May be able to provision Ope

---

## Testing Coverage (22 projects, 39 unique claims)

**Rules:**
- Save for next phase. Requires careful extraction to avoid breaking deepfield's 231 tests. The nanoagents need a standalone signal model (dict-based, no Pydantic deepfield imports) similar to how the b

**Key facts:**
- All work for the multimodal agent pack targets the `deepfield-multimodal` repo. The original `deepfield` repo is read-only reference for architectural patterns. The spec lives at `/Users/jkershaw/Docu
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 
- **intel-inference-router**: cascade/, 248 tests, Helm chart
- Agent promotion engine: draft→candidate→nano→micro→macro with empirical validation (24 tests)
- 248 tests in intel-inference-router, 231 in deepfield

---

## Self Serve Quickstart (21 projects, 68 unique claims)

**Rules:**
- Don't create the agnosticv config manually. Run the liftoff onboard pipeline first to right-size the deployment — it scans the repo, classifies it, generates the agnosticv config, and validates readin
- The liftoff pipeline (at /Users/jkershaw/Documents/liftoff) is the standard path from repo → RHDPS-ready. It runs NovaScan (capacity), DarkScope (security), and LLM classification to generate the corr
- 1. Finish the Antora showroom content (content/ directory with lab pages)
2. Run `liftoff onboard` against the sovereign-ai-lab repo
3. Review the generated agnosticv config
4. Adjust if needed, then 
- When building a new project or quickstart, create `tests/validation_matrix.yaml`, `tests/claim_registry.yaml`, and `tests/benchmark_rubric.yaml` following the pattern in edge-ai-cpu-inference. Structu
- Explicit direction from the user regarding Intel partner branding requirements for RHDP showrooms and quickstart materials.

**Key facts:**
- Focus is CPU-only (Xeon 6) for now. Gaudi 3 GPU support is future scope. Changes to the planner are data-driven (gpu_catalog.json, slo_templates.json, benchmark data) rather than code changes. A stand
- Application repo: `rh-ai-quickstart/multi-agent-loan-origination` (Python agents, LangGraph)
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 
- Created the EVY platform (srex-dev/EVY) and the edge-ai-cpu-inference quickstart (jkershawrh/edge-ai-cpu-inference, published to quay.io/rh-ai-quickstart/).
- Works with Red Hat + Intel partnerships. Familiar with OpenShift, UBI9, Helm charts, BitNet models, llama.cpp, and the rh-ai-quickstart GitHub org. Uses a CDD→TDD→EDD→BDD validation framework with cla

---

## Governance (18 projects, 67 unique claims)

**Rules:**
- Executives and government stakeholders need to see how the pieces connect visually. A schematic shows trust boundaries, data flow direction, which components talk to which, and where governance checkp
- Lead with sovereignty, not governance mechanics
- The ecosystem whitepaper must be strategically framed: fleet-llm-d is the Red Hat product being showcased. deepfield-fleet, governed-cognitive-loop, and are-immutable-ledger are Kersh's creations that
- Lead every section with what fleet-llm-d does. Frame the other systems as "what fleet-llm-d's architecture enables" rather than equal peers. The narrative is: fleet-llm-d provides the fleet orchestrat
- All work should reinforce "one model, one silicon" — granite-3-2-8b-instruct on Xeon 6 only. No Gaudi references in new work. Branding must be Intel + Red Hat co-branded.

Related: [[reference-repos]]

**Key facts:**
- The user decided to fully open-source the ledger (no commercial holdback) to maximize AAIF credibility. ARE Foundation passports, scope, policy, and the full governance stack remain as-is.
- `srex-dev/are-foundation` — the full ARE Foundation (S0/S1 governance)
- **GeoLux** (namespace: geolux) — governance brain
- Receives evidence from StarGate (HTTP + Kafka), generates hypotheses, classifies, runs MPC
- Hypothesis dedup: in-memory cache (1h TTL) + DB check p
- GeoLux is the governance brain connecting Deepfield (detection) and StarGate (execution). Deployed on infra01 namespace `geolux`.
- Multi-agent orchestration system to be deployed on OpenShift AI (Red Hat AI 3.4+). Inference on Intel Xeon 6 and Gaudi accelerators. Will use Red Hat's AgentOps stack: OpenTelemetry distributed tracin

---

## Infra01 Only (14 projects, 52 unique claims)

**Rules:**
- Every application deployed to infra01 (ocpv-infra01) MUST have Red Hat OAuth proxy (ose-oauth-proxy-rhel9 sidecar) on all externally-routed frontend and API deployments.
- Before any deploy to infra01:
1. Verify the deployment has an oauth-proxy sidecar container
2. Verify the route targets the oauth-proxy port (8080/8443/4180), not the backend directly
3. Test with `cu
- When rebasing agnosticv PR branches, the context keeps switching back to infra01. Always `oc config use-context` explicitly before running oc commands on integration or prod clusters.
- This project deploys to infra01 ONLY. Before any oc command, verify context:
- Multiple sessions have accidentally run commands against Oberon (default/api-REDACTED-CLUSTER-example-com:6443/kube:admin) instead of infra01, causing "namespace not found" errors, stale data reads, and gat

**Key facts:**
- Three systems deployed on infra01 (ocpv-infra01.dal12.infra.demo.redhat.com), all scoped to 5 sandbox clusters: ocpv05, ocpv06, ocpv07, ocpv08, ocpv09.
- GeoLux is the governance brain connecting Deepfield (detection) and StarGate (execution). Deployed on infra01 namespace `geolux`.
- Scanner clusters reduced to ocpv05-09 only (removed infra01, infra02, ocp-us-east-1)
- Intel Lab OpenShift cluster. This IS Folsome Lab (same cluster). DEV environment in the pipeline: Oberon → Infra01 → Integration → Demo. Related to .
- ## Live Metrics (infra01 demo platform, 48K+ signals)

---

## Branding (14 projects, 27 unique claims)

**Rules:**
- Red Hat logo SVGs loaded via `<img>` tags MUST use inline `fill` attributes, not `<style>` CSS classes — browsers ignore internal stylesheets in SVGs loaded as images.
- Download official SVGs from `static.redhat.com/libs/redhat/brand-assets/2/corp/`
- `logo.svg` — standard (black wordmark, for light backgrounds)
- On dark mastheads: use white pill container (`background: white, padding: 6px 14px, borderRadius: 6px`) with the standard (black text) logo inside — per brand guidelines
- All work should reinforce "one model, one silicon" — granite-3-2-8b-instruct on Xeon 6 only. No Gaudi references in new work. Branding must be Intel + Red Hat co-branded.

Related: [[reference-repos]]

**Key facts:**
- Multi-agent orchestration system to be deployed on OpenShift AI (Red Hat AI 3.4+). Inference on Intel Xeon 6 and Gaudi accelerators. Will use Red Hat's AgentOps stack: OpenTelemetry distributed tracin
- Works at Red Hat on the demo platform. Building a multi-agent orchestration system on OpenShift AI. Has access to Intel Xeon 6 and Gaudi accelerator servers for inference. May be able to provision Ope
- Critical correction from Ashok Jammula on 2026-06-16. The demo narrative assumed granite-3-2-8b-instruct runs on Intel Xeon 6 — it does NOT.
- Do NOT claim Intel Xeon 6 inferencing for granite-3-2-8b-instruct until confirmed on rac-maas. The 426-532ms latency numbers from LiftOff trial runs were hitting NVIDIA L40S, not Xeon.

Related: [[pro
- TCO comparison tool for Financial Services sales conversations, comparing AI inference on Intel Xeon 6 CPUs vs NVIDIA H100 GPUs vs cloud API pricing.

**Decisions:**
- Red Hat–Intel AI inference partnership. Strategic direction pivoted on 2026-06-15 from "Xeon-first dual-path" to **"One model, one silicon, every lab"** — consolidating ALL Summit Connect labs to gran
- Consolidating all Summit Connect labs to granite-3-2-8b-instruct on Intel Xeon 6. Validated across 25 repos, 135 quality probes, all pass.

---

## Summit Connect (14 projects, 57 unique claims)

**Rules:**
- When building a new project or quickstart, create `tests/validation_matrix.yaml`, `tests/claim_registry.yaml`, and `tests/benchmark_rubric.yaml` following the pattern in edge-ai-cpu-inference. Structu
- Explicit direction from the user regarding Intel partner branding requirements for RHDP showrooms and quickstart materials.
- Kelkhund (org gatekeeper) explicitly stated this when granting org access. The quickstart catalog was originally ranked by Intel tech story + originality, but the org cares most about the business pro
- Showroom labs are NOT "deploy the quickstart and explore the UI." They teach the user how to BUILD what the quickstart builds, step by step, so they understand the technology contextually.
- Package Launchpad as AgnosticV config + AgnosticD roles. Don't change core Launchpad architecture (namespace-per-demo). The RHDP integration is about deployment packaging, not internal restructuring.


**Key facts:**
- Application repo: `rh-ai-quickstart/multi-agent-loan-origination` (Python agents, LangGraph)
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 
- Demonstrate that even with 2G connectivity and minimal hardware (no GPU), you can deploy useful AI inference at edge sites — disaster zones, warzones, underserved communities. Summit Connect conferenc
- Created the EVY platform (srex-dev/EVY) and the edge-ai-cpu-inference quickstart (jkershawrh/edge-ai-cpu-inference, published to quay.io/rh-ai-quickstart/).
- Works with Red Hat + Intel partnerships. Familiar with OpenShift, UBI9, Helm charts, BitNet models, llama.cpp, and the rh-ai-quickstart GitHub org. Uses a CDD→TDD→EDD→BDD validation framework with cla

**Decisions:**
- Red Hat–Intel AI inference partnership. Strategic direction pivoted on 2026-06-15 from "Xeon-first dual-path" to **"One model, one silicon, every lab"** — consolidating ALL Summit Connect labs to gran
- Consolidating all Summit Connect labs to granite-3-2-8b-instruct on Intel Xeon 6. Validated across 25 repos, 135 quality probes, all pass.

---

## Immutable Ledger (13 projects, 45 unique claims)

**Rules:**
- The sovereign AI lab demo story is about **national/organizational AI sovereignty** — the ability to possess, control, adapt, govern, and prove your AI infrastructure is truly yours. The immutable led
- Show that sovereign AI means more than local deployment — it requires provable control recorded in a tamper-evident immutable ledger (are-immutable-ledger).
- The ecosystem whitepaper must be strategically framed: fleet-llm-d is the Red Hat product being showcased. deepfield-fleet, governed-cognitive-loop, and are-immutable-ledger are Kersh's creations that
- DeepField owns observations, findings, and forecasts. GCL owns signed and falsified proposals. fleet-llm-d owns admission, authorization, operation state, desired/observed state, and actuation. The st
- **are-immutable-ledger**: independent evidence infrastructure with its own database and compute. The ledger-owned gRPC service is canonical. `pkg/ledger/` currently supports memory/disabled modes and 

**Key facts:**
- ARE Foundation's immutable ledger has been extracted into a standalone repo at jkershawrh/are-immutable-ledger (GitHub). Positioned as neutral infrastructure for cross-system agentic proof chains.
- `jkershawrh/are-immutable-ledger` — the standalone ledger + demo
- Key changes deployed:
- GCL prompt governance adapter replaced regex semantic router (evidence→classify→falsify→sign→commit)
- OPA decisions now bridge to the immutable ledger via demo-api
- Periodic 
- Ledger API: update doc code to match actual are-immutable-ledger API (no adapter shim)
- 1. **Identity** — SPIFFE vs Passports. Compare KAGENTI's SPIFFE identity model with ARE's passport model. Document gaps.
2. **Enforcement** — OPA vs Coprocessor. Review KAGENTI's OPA setup. Identify w

---

## Deterministic (13 projects, 20 unique claims)

**Rules:**
- The LLM never computes the committed action. A deterministic controller owns optimization.
- Every LLM call site must have a deterministic fallback.

**Key facts:**
- **Constraint Classification** — 32 constraints, deterministic assertions
- Extract the 19 deterministic nanoagents from deepfield into intel-inference-router so any consumer gets cascade + routing + scaling in one pip install.
- 100% deterministic FAILS (severity check flags medium+ drops) — LLM adversary probe not yet wired into standalone sampler
- ```
Cascade drops signal → decision.record → Ledger
                                           ↓
GCL sampler polls (from_ts watermark) → audits 5% sample → deterministic checks
                       
- All 100% deterministic at temp=0

---

## Agent Architecture (10 projects, 16 unique claims)

**Key facts:**
- Multi-agent orchestration system to be deployed on OpenShift AI (Red Hat AI 3.4+). Inference on Intel Xeon 6 and Gaudi accelerators. Will use Red Hat's AgentOps stack: OpenTelemetry distributed tracin
- Works at Red Hat on the demo platform. Building a multi-agent orchestration system on OpenShift AI. Has access to Intel Xeon 6 and Gaudi accelerator servers for inference. May be able to provision Ope
- Application repo: `rh-ai-quickstart/multi-agent-loan-origination` (Python agents, LangGraph)
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 
- 1. Intel Inference Platform — Xeon 6 inferencing, semantic routing, RAG chat, 1,437 tests, live on RHDP integration
2. Triforce — multi-agent on Xeon 6 only (zero GPU), 246 tests, PR #26789 to agnosti

---

## Oauth Security (9 projects, 15 unique claims)

**Rules:**
- Every application deployed to infra01 (ocpv-infra01) MUST have Red Hat OAuth proxy (ose-oauth-proxy-rhel9 sidecar) on all externally-routed frontend and API deployments.
- The user has flagged this repeatedly — OAuth keeps getting dropped during redeploys. This is a security compliance requirement, not optional.
- Before any deploy to infra01:
1. Verify the deployment has an oauth-proxy sidecar container
2. Verify the route targets the oauth-proxy port (8080/8443/4180), not the backend directly
3. Test with `cu
- ### Troshkad (Host Agent Daemon)
- Single-file Python daemon at `src/troshkad/troshkad.py` — stdlib only, no pip
- Backend client: `src/backend/app/services/troshkad_client.py` — urllib3 connection po

**Key facts:**
- **StarGate** (namespace: stargate) — batch readiness scanner + remediation pipeline
- API: 2 replicas + oauth-proxy, Frontend: nginx + oauth-proxy, Scanner: scheduler + babylon-worker, Postgres
- Scan
- API: 2 replicas, oauth-proxy sidecar
- Frontend: nginx reverse proxy, oauth-proxy
- Backend: 1 replica + oauth-proxy, Postgres
- 1 replica + oauth-proxy

---

## Claim Registry (9 projects, 15 unique claims)

**Rules:**
- Always include a multi-stage gated validation matrix (CDD → TDD → EDD → BDD → CBT → Publication) with claim registries for provenance tracking.
- This is a core quality practice — every factual claim (model size, memory footprint, latency, architecture specs) must be tracked with source and verification status. The user designed this framework 
- When building a new project or quickstart, create `tests/validation_matrix.yaml`, `tests/claim_registry.yaml`, and `tests/benchmark_rubric.yaml` following the pattern in edge-ai-cpu-inference. Structu
- Once Ashok deploys optimized models, re-run the validation matrix and compare against these targets. Update Act 06 punchline with real before/after. Don't show projected numbers as measured — only sho

**Key facts:**
- Validation matrix expanded to stages 8-10 (modules, benchmarks, workflows)
- Whitepaper with benchmark proof points
- 72 tests, 9 stages. Validation matrix at `tests/validation_matrix.yaml`.
- 1. Create `src/darkscope/analyzers/new_analyzer.py`
2. Implement `def scan(files: list) -> list[Finding]`
3. Wire into `scanner.py` orchestrator
4. Add tests in `tests/test_new_analyzer.py`
5. Add sta
- `tests/` | Gated validation matrix (contracts, unit, integration, benchmarks, publication)

---

## Pipeline Routing (8 projects, 28 unique claims)

**Rules:**
- Three-tier cascade compression engine for CPU inference at scale.
Consolidates the cascade pipeline, routing engine, TCO calculator, and benchmark harness
into a single repo. Built for Intel FSI engag

**Key facts:**
- intel-inference-router has routing/corpora/bootstrapper/scaler/fleet_manager
- Any new consumer gets the full 85% compression + five-lane routing in one package
- ```
deepfield (monolith)
  nanoagents/ (19 agents) → FilterDecision
  routing/signal_router.py → imports intel-inference-router
  
intel-inference-router (package)
  corpora, lanes, bootstrapper, scal
- ```
intel-inference-router (complete CPU inference stack)
  cascade/           ← extracted from deepfield
    pipeline.py      ← runs 19 nanoagents in sequence
    agents/          ← dedupe, transient
- cascade/router.py — post-cascade lane routing

---

## Intel Partnership (7 projects, 12 unique claims)

**Rules:**
- Explicit direction from the user regarding Intel partner branding requirements for RHDP showrooms and quickstart materials.
- The cascade framework (`cascade_compression/`) must never be modified for domain-specific work. Domain packs are the adapter layer. Related: [[people-ron-haberman]], [[project-tco-calculator]]
- Ron Haberman (AI Incubation, Red Hat) needs dollar-per-business-process TCO numbers for customer conversations with Amex. The story: cascade compression means most signals don't need a model, so cheap
- Three-tier cascade compression engine for CPU inference at scale.
Consolidates the cascade pipeline, routing engine, TCO calculator, and benchmark harness
into a single repo. Built for Intel FSI engag
- The key insight for FSI sales: most signals do NOT need a model.

**Key facts:**
- **Corpora compiled from real benchmarks**: 216 entries from 16 benchmark files → 19 routing entries across 6 industries, 21 quality gaps. Key picks: granite-350m for classification, smollm2-360m for b
- Every feature/fix should reinforce the cascade compression narrative. Throughput numbers are placeholders until mtahhan delivers RHAIIS 3.5 benchmarks.

Key people: [[people-ron-haberman]], [[people-s
- The Intel partner demo needs to be packaged as a Catalog Item (CI) using agnosticd/agnosticv — the standard Red Hat demo delivery pipeline. Workflow: devel branch → integration environment → prod bran
- # Lookup best model
task = resolve_benchmark_task("classify_signal", industry="fsi")
entry = corpora.lookup("fsi", task, tier="micro", strategy=strategy)
```
- Intel vs GPU vs Cloud API Total Cost of Ownership calculator for Financial Services sales conversations.
Compares running AI inference workloads on Intel Xeon 6 CPUs vs NVIDIA H100 GPUs vs cloud API t

---

## Self Contained (6 projects, 8 unique claims)

**Rules:**
- Keep everything internal to the repo for research work — no external applications.
- Pure Python, no external services needed. Read-only — never modifies the scanned repo.

**Key facts:**
- Everything stays internal to the repo — no Docker, no separate frontend, no external services (except Claude API for inferencing)
- "A token is just OCP access" — no external API key provisioning needed
- Unit tests use mocks (no external services). Conftest provides mock BitNet server (httpx.MockTransport), mock Redis, mock ChromaDB.
- **Self-contained**: Everything runs from this repo. No Docker, no external services (except Claude API for inferencing).
- ### Azure Provider Setup
- Provider type `azure` — creates nested-virt RHEL VMs on Azure
- **Driver**: `src/backend/app/services/providers/azure.py` (~880 lines, self-contained)
- **Prerequisites**: c

---

## Story First (3 projects, 8 unique claims)

**Rules:**
- Don't build dashboards with tabs/panels. Build guided story experiences using the act-based pattern from Triforce and DeepField Multimodal.
- Jonathan's demos are presented on large projection screens to executive audiences. A dashboard with tiny text and tabs doesn't tell a story. The hero's journey arc (ordinary world → call → ordeal → re
- Projection-scale fonts: 56-80px headlines, 20-28px body, min 14px for anything
- Progressive disclosure: one thought per click, never a wall of text
- Reference: Triforce App.tsx (act engine), DeepField App.tsx (hero's journey mapping)

**Key facts:**
- **Navigation:** Act-based linear progression with progress dots and keyboard nav.
- Hero's Journey stays but the punchline changes: "80% CPU at $0, 20% GPU at $/token, system routes for you"

---

## Cascade Framework (3 projects, 12 unique claims)

**Rules:**
- System prompt must include domain context from the domain pack
- Replace the encoder-service HTTP calls in `_run_gate2()` with LiteLLM calls to phi-4. The domain pack YAML provides the system prompt context. The encoder pod can be scaled down or repurposed.

[[proj

**Key facts:**
- AAP cascade validated 2026-08-06. Domain-agnostic proven with zero code changes to cascade framework.
- 1. **AAP signal source** — prove domain-agnostic on Ansible job signals (infra01 has AAP namespace)
2. **Historical replay** — feed 140M Postgres signals through cascade offline
3. **Action layer** — 
- 1. Historical replay — feed 140M Postgres signals through cascade offline
2. Lower Gate 2 threshold or let replay boost compression
3. Action layer — what happens to signals that pass through (tickets
- Wire AAP signal source as second domain to prove domain-agnostic.

[[project-cascade-final-state]]
- deepfield-multimodal becomes the engine. deepfield's K8s agents become a domain pack. Repos align to tiers.

---

## Production Gates (3 projects, 3 unique claims)

**Key facts:**
- `make test-platform` is the full green light gate (all 11 stages)
- # Tests by stage (11 stages total)
make test-contracts              # Stage 0: schema validation (120 tests)
make test-infra                  # Stage 1: containers + health
make test-unit             

---

## Statistics

- **Total Claims:** 2477
- **Projects Scanned:** 63
- **Institutional Topics:** 22
- **Institutional Claims:** 851
- **Rules:** 270
- **Facts:** 2147
- **Preferences:** 31
- **Decisions:** 7
- **Caveats:** 22
