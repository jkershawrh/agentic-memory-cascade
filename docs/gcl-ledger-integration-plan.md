# GCL + Immutable Ledger Integration (Implemented)

**Status: LIVE on infra01.** All three systems deployed and running autonomously. 500K+ entries, 10% LLM-validated disagreement rate.

## Methodology: CDD → TDD → EDD → BDD

Three independent repos. No cross-contamination. Each repo only knows the contract, not the other repo's internals.

```
cascade-compression          fleet-llm-d/upstream-are-ledger          governed-cognitive-loop
      │                              │                                      │
      │  POST /api/receipts          │                                      │
      │  entry_type:                 │                                      │
      │  "cascade.decision"          │                                      │
      ├─────────────────────────────►│                                      │
      │                              │  GET /api/entries                    │
      │                              │  ?entry_type=cascade.decision        │
      │                              │◄─────────────────────────────────────┤
      │                              │                                      │
      │                              │  POST /api/receipts                  │
      │                              │  entry_type:                         │
      │                              │  "cascade.audit.verdict"             │
      │                              │◄─────────────────────────────────────┤
      │                              │                                      │
```

## Contracts (CDD)

### Contract 1: cascade.decision (cascade-compression → ledger)

```json
{
  "entry_type": "cascade.decision",
  "agent_id": "cascade-{domain}",
  "content_type": "application/json",
  "source_id": "cascade-compression",
  "correlation_id": "batch-{uuid}",
  "idempotency_key": "sha256({entry_type}\\0{correlation_id})",
  "input_hash": "sha256({content})",
  "content": "[{decisions}]"
}
```

Decision content schema:
```json
{
  "signal_id": "string",
  "signal_type": "string",
  "severity": "info|low|medium|high|critical",
  "namespace": "string",
  "outcome": "keep|drop|suppress|dedupe|escalate|classify",
  "agent_name": "string",
  "confidence": "float 0-1",
  "tier": "nano",
  "domain": "string"
}
```

Written by: `cascade_compression/integrations/ledger.py`
Endpoint: `POST /api/receipts`
Auth: `Authorization: Bearer {token}`

### Contract 2: cascade.audit.verdict (GCL → ledger)

```json
{
  "entry_type": "cascade.audit.verdict",
  "agent_id": "governed-cognitive-loop",
  "content_type": "application/json",
  "source_id": "gcl-cascade-audit",
  "correlation_id": "{same as original cascade.decision}",
  "idempotency_key": "sha256({entry_type}\\0{correlation_id}\\0{signal_id})",
  "input_hash": "sha256({content})",
  "content": "{verdict}"
}
```

Verdict content schema:
```json
{
  "signal_id": "string",
  "signal_type": "string",
  "original_outcome": "drop|suppress|dedupe",
  "original_agent": "string",
  "original_confidence": "float",
  "domain": "string",
  "verdict": "SURVIVES|FAILS",
  "checks_passed": ["severity", "confidence", "freshness"],
  "checks_failed": [],
  "llm_probe_result": "string|null",
  "reason": "string"
}
```

Written by: `gcl/loop/cascade_audit_sampler.py`
Endpoint: `POST /api/receipts`

### Contract 3: Query interface (GCL → ledger)

GCL reads cascade decisions via:
- `GET /api/entries?entry_type=cascade.decision&page_size=100`
- Uses chain_position watermark to paginate incrementally

No changes to the ledger API — uses existing endpoints.

---

## Repo Boundaries — What Changes Where

### cascade-compression (382 tests currently)

Changes:
- `contracts/schemas/cascade-decision.json` — JSON Schema for decision content (NEW)
- `contracts/schemas/cascade-audit-verdict.json` — JSON Schema for verdict content (NEW)
- `cascade_compression/integrations/ledger.py` — already exists, needs contract compliance tests
- `tests/test_ledger_contract.py` — contract tests against schemas (NEW)

Does NOT change:
- cascade/ (pipeline, agents, promotion, protocol)
- routing/, infra/, tco/
- bridge.py (ledger write is already wired, just needs contract validation)
- Any collector or domain pack

### fleet-llm-d/upstream-are-ledger

Changes:
- None. The ledger is a generic append-only store. It doesn't know about cascade or GCL entry types. It just stores and retrieves entries.
- Deploy to infra01 (operational, not code)

### governed-cognitive-loop (823 tests currently)

Changes:
- `gcl/adapter/cascade_audit_adapter.py` — converts cascade.decision to Evidence (NEW)
- `gcl/falsification/cascade_audit.py` — deterministic checks + LLM probe (NEW)
- `gcl/loop/cascade_audit_sampler.py` — polls ledger, samples drops, runs audit (NEW)
- `gcl/config.py` — add cascade audit settings (MODIFY)
- `tests/test_cascade_audit_adapter.py` — adapter contract tests (NEW)
- `tests/test_cascade_audit_falsifier.py` — falsification tests (NEW)
- `tests/test_cascade_audit_sampler.py` — sampler integration tests (NEW)

Does NOT change:
- gcl/falsification/gate.py (existing falsification stays untouched)
- gcl/loop/driver.py (existing loop stays untouched)
- gcl/loop/ledger.py (existing ledger client reused as-is)
- Any existing adapter, classifier, or controller

---

## Validation Matrix

### Stage 0: Contract Validation (CDD)

| Test | Repo | What |
|------|------|------|
| cascade.decision schema validates | cascade-compression | JSON Schema for decision content |
| cascade.audit.verdict schema validates | cascade-compression | JSON Schema for verdict content |
| ledger.py produces valid cascade.decision | cascade-compression | Output matches schema |
| cascade_audit_adapter.py consumes valid cascade.decision | GCL | Input matches schema |
| cascade_audit.py produces valid cascade.audit.verdict | GCL | Output matches schema |

### Stage 1: Unit Tests (TDD)

| Test | Repo | What |
|------|------|------|
| write_decisions serializes correctly | cascade-compression | JSON structure, idempotency key, input hash |
| write_decisions handles empty results | cascade-compression | No crash on 0 decisions |
| write_decisions handles no ledger URL | cascade-compression | Silent no-op |
| cascade_drop_to_evidence converts correctly | GCL | Decision → Evidence mapping |
| audit_drop runs severity check | GCL | medium+ flagged |
| audit_drop runs confidence check | GCL | <0.8 flagged |
| audit_drop runs freshness check | GCL | <1hr agent flagged |
| audit_drop returns SURVIVES for correct drop | GCL | Low severity, high confidence |
| audit_drop returns FAILS for missed incident | GCL | High severity dropped |
| sampler respects sample rate | GCL | 5% of drops sampled |
| sampler always audits new agents | GCL | <1hr agents always sampled |
| sampler updates watermark | GCL | chain_position advances |

### Stage 2: Integration Tests (EDD)

| Test | Repo | What |
|------|------|------|
| cascade bridge writes to ledger endpoint | cascade-compression | HTTP POST succeeds (mock server) |
| sampler reads from ledger endpoint | GCL | HTTP GET returns entries (mock server) |
| sampler writes verdict back to ledger | GCL | HTTP POST with correct schema |
| full cycle: decision → read → audit → verdict | GCL | End-to-end with mock ledger |

### Stage 3: Behavior Tests (BDD)

| Test | Repo | What |
|------|------|------|
| cascade processes signals and records decisions | cascade-compression | bridge.process() → ledger entry |
| GCL detects a false negative | GCL | high-severity drop → FAILS verdict |
| GCL confirms a correct drop | GCL | info-severity drop → SURVIVES verdict |
| correlation_id links decision to verdict | GCL | Same correlation_id in both entries |

---

## Red/Green Matrix Rubric

| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Contract compliance | 100% schemas pass | 95%+ pass | <95% |
| Unit test coverage | All pass | 1-2 failing | 3+ failing |
| Integration tests | All pass | Mock failures only | Real endpoint failures |
| Behavior tests | Full cycle works | Partial cycle | Broken cycle |
| cascade-compression tests | 382+ pass | Any regression | New test fails |
| GCL tests | 823+ pass | Any regression | New test fails |
| Ledger tests | All pass | Any regression | New test fails |
| Cross-repo contamination | Zero imports | Shared types via schema | Direct imports |

**Gate rule**: No implementation code until all contracts are green. No deployment until all tests are green. No merge until rubric is all-green.

---

## Execution Order

1. **CDD**: Write JSON schemas for cascade.decision and cascade.audit.verdict in cascade-compression/contracts/schemas/
2. **TDD cascade-compression**: Write test_ledger_contract.py, run red, implement to green
3. **TDD GCL**: Write test_cascade_audit_*.py, run red, implement to green
4. **EDD**: Integration tests with mock ledger server
5. **BDD**: Full behavior tests
6. **Deploy ledger to infra01**: Operational, no code changes to ledger
7. **Deploy GCL to infra01**: With cascade audit config enabled
8. **Verify end-to-end**: Live cascade → live ledger → live GCL audit
9. **Rubric check**: All green across all three repos
