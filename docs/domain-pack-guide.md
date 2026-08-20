# Domain Pack Guide

## What Is a Domain Pack?

A domain pack connects a data source to the cascade framework. It consists of three things:

1. **Collector** — reads from the data source, maps records to Signal protocol
2. **Prompt** — one paragraph telling the LLM what the classification buckets mean
3. **Data** — historical signals for replay bootstrapping

The cascade framework handles everything else: pipeline, agents, promotion, routing, scaling, TCO.

## Building a Domain Pack

### Step 1: Write the Collector

Extend `BaseCollector` from `collectors/base.py`. It requires three methods: `connect()`, `collect()` (new signals since last poll), and `collect_all()` (full replay).

Required fields per signal:
```python
signal_id       # unique identifier (int, UUID, or string)
cluster_id      # source system identifier
namespace       # grouping key (account, team, region, etc.)
resource_kind   # what generated the signal (e.g. "ansible_job", "transaction")
resource_name   # specific resource (e.g. job name, account number)
signal_type     # classification key (e.g. "job_failed", "wire_transfer")
severity        # info, low, medium, high, critical
evidence        # dict with message and domain-specific fields
labels          # dict with metadata (e.g. {"domain": "finance"})
```

Example — transaction collector:
```python
class TransactionCollector:
    def __init__(self, db_url, poll_interval=30):
        self._last_id = 0
        self._db_url = db_url

    def collect(self):
        """Fetch new transactions since last poll."""
        rows = query("""
            SELECT id, account_id, amount, merchant, location,
                   transaction_type, timestamp
            FROM transactions WHERE id > %s
            ORDER BY id LIMIT 500
        """, self._last_id)

        signals = []
        for row in rows:
            signals.append(TransactionSignal(row))
            self._last_id = max(self._last_id, row.id)
        return signals

    def collect_all(self):
        """Replay all historical transactions."""
        rows = query("SELECT ... FROM transactions ORDER BY id")
        return [TransactionSignal(row) for row in rows]
```

Signal mapping:
```python
class TransactionSignal:
    def __init__(self, txn):
        self.signal_id = txn.id
        self.cluster_id = "bank-prod"
        self.namespace = f"acct-{txn.account_id}"
        self.resource_kind = "transaction"
        self.resource_name = f"{txn.merchant}"
        self.signal_type = self._map_type(txn)
        self.severity = self._map_severity(txn)
        self.evidence = {
            "message": f"${txn.amount} at {txn.merchant}",
            "amount": txn.amount,
            "merchant": txn.merchant,
            "location": txn.location,
            "type": txn.transaction_type,
        }
        self.labels = {"domain": "finance"}

    def _map_type(self, txn):
        # Map to signal types the cascade can learn from
        if txn.transaction_type == "wire":
            return "wire_transfer"
        if txn.amount > 10000:
            return "large_transaction"
        return "standard_transaction"

    def _map_severity(self, txn):
        if txn.amount > 50000:
            return "high"
        if txn.transaction_type == "wire":
            return "medium"
        return "info"
```

### Step 2: Write the Prompt

The prompt tells the LLM what to do with signals that survive the cascade. Keep it short — the terse version outperforms the detailed version.

```python
PROMPT = """You are classifying financial transaction signals.
Classify as exactly one of: routine_noise, known_pattern,
needs_attention, real_incident. Answer with one word only."""
```

That's it. The domain knowledge is in the signal's evidence field, not in the prompt. When the LLM sees `"$50,000 wire transfer | first_time: true | location: offshore"`, the signal speaks for itself.

**Do not:**
- List every possible scenario in the prompt
- Add examples (the model already knows what fraud looks like)
- Explain the domain in detail
- Use more than one paragraph

**Why terse works:** By the time a signal reaches the LLM, the cascade has already eliminated 85-99% of noise. The remaining signals are ambiguous edge cases where the LLM's general knowledge is more useful than domain-specific instructions.

### Step 3: Provide Historical Data

The cascade bootstraps itself from historical replay. Point the collector at historical data and let it run.

```python
# On first deployment:
collector = TransactionCollector(db_url="postgresql://...")
cascade = CascadeBridge()

# Replay 3 years of transactions
all_signals = collector.collect_all()  # e.g. 50M transactions
for batch in chunks(all_signals, 500):
    cascade.process(batch)

# After replay:
# - Cascade has discovered recurring merchant patterns
# - Velocity agents have been proposed and promoted
# - Compression ratio is established
# - Switch to live polling
```

The more historical data, the smarter the cascade on day one.

## Wiring Into the Cascade Bridge

The cascade bridge (`bridge.py`) connects your collector to the framework. Two options:

### Option 1: CLI (recommended)

Create a domain config file in `domains/your_domain.py`:

```python
DOMAIN = "your_domain"
SYSTEM_PROMPT = """You are classifying your-domain signals.
Classify as exactly one of: routine_noise, known_pattern,
needs_attention, real_incident. Answer with one word only."""
LLM_MODEL = "granite-3-2-8b-instruct-cpu"
COLLECTOR_CLASS = YourCollector
```

Then run:
```bash
cascade-run --domain your_domain --llm-url https://maas/v1 --llm-key sk-...
cascade-replay --domain your_domain --data historical.csv --llm-url https://maas/v1
```

### Option 2: Python API

```python
from cascade_compression.bridge import CascadeBridge

bridge = CascadeBridge(
    llm_url="https://maas/v1",
    llm_key="sk-...",
    llm_model="granite-3-2-8b-instruct-cpu",
    system_prompt=YOUR_PROMPT,
    domain="your_domain",
)

collector = YourCollector(poll_interval=30)
while running:
    signals = collector.collect()
    if signals:
        bridge.process(signals)
    time.sleep(30)
```

## Testing Your Domain Pack

### Signal mapping test

Verify your collector produces valid signals:

```python
def test_collector_produces_valid_signals():
    collector = YourCollector(db_url=TEST_DB)
    signals = collector.collect()
    for sig in signals:
        assert sig.signal_type  # not empty
        assert sig.severity in ("info", "low", "medium", "high", "critical")
        assert sig.evidence.get("message")  # has a message
        assert sig.labels.get("domain")  # tagged with domain
```

### Cascade integration test

Verify signals flow through the pipeline:

```python
def test_cascade_processes_signals():
    pipeline = CascadePipeline(default_agents())
    signals = [your_signal_factory() for _ in range(100)]
    result = pipeline.run(signals)
    assert result.compression_ratio > 0  # some signals were filtered
    assert len(result.survivors) < len(signals)  # not everything passed
```

### LLM classification test

Verify the prompt produces valid classifications:

```python
EXPECTED = {
    "routine_signal": "routine_noise",
    "obvious_incident": "real_incident",
    "ambiguous_signal": ("needs_attention", "known_pattern"),
}

def test_llm_classifies_correctly():
    for signal_desc, expected in EXPECTED.items():
        result = classify(signal_desc, system_prompt=YOUR_PROMPT)
        if isinstance(expected, tuple):
            assert result in expected
        else:
            assert result == expected
```

## Existing Domain Packs

| Domain | Collector | Signal Types | Compression | Key Metric | Source |
|--------|-----------|-------------|-------------|------------|--------|
| Kubernetes | `collectors/kubernetes.py` | pods, events, nodes | 72.9% | 37.3% noise rate, 0 FN | Live |
| AAP | `collectors/aap.py` | jobs, task events, activity stream | 96.0% | 0 FN | Live |
| Finance | `collectors/finance.py` | transactions, fraud, compliance | 61.1% | 92.7% fraud survival, 100% compliance | Synthetic |
| Healthcare | `collectors/healthcare.py` | patient alerts, clinical, compliance | 91.0% | 96.6% critical, 99.0% compliance | Synthetic |
| Insurance | `collectors/insurance.py` | claims, fraud indicators, policy | 81.2% | 100% fraud, 99.8% compliance | Synthetic |
| Retail | `collectors/retail.py` | POS, shrinkage, inventory | 88.3% | 100% shrinkage, 100% compliance | Synthetic |
| Telecom | `collectors/telecom.py` | network events, incidents, SLA | 94.3% | 92.1% incidents, 80.7% compliance | Synthetic |

## Synthetic Generators

For domains without live data sources, synthetic generators in `benchmarks/` produce realistic signal streams for benchmarking and bootstrapping:

| Generator | File | Signals |
|-----------|------|---------|
| Finance | `benchmarks/synthetic_finance.py` | Transactions, wire transfers, fraud patterns, compliance events |
| Healthcare | `benchmarks/synthetic_healthcare.py` | Patient vitals, lab results, medication alerts, HIPAA events |
| Insurance | `benchmarks/synthetic_insurance.py` | Claims, policy changes, fraud indicators, NAIC compliance |
| Retail | `benchmarks/synthetic_retail.py` | POS transactions, returns, shrinkage patterns, inventory |
| Telecom | `benchmarks/synthetic_telecom.py` | Network faults, traffic anomalies, SLA breaches, incidents |

Use via replay:
```bash
cascade-replay --domain finance --data synthetic --llm-url https://maas/v1
```

## Domain Pack Checklist

- [ ] Collector extends `BaseCollector` (in `collectors/base.py`)
- [ ] `connect()` initializes connection to data source
- [ ] `collect()` returns new signals since last poll
- [ ] `collect_all()` returns all historical signals for replay
- [ ] Signals have valid `signal_type`, `severity`, `evidence`
- [ ] Domain config in `domains/your_domain.py` with `DOMAIN`, `SYSTEM_PROMPT`, `COLLECTOR_CLASS`
- [ ] Prompt is one paragraph, four classification buckets
- [ ] Historical data or synthetic generator available for replay
- [ ] Integration test passes with cascade pipeline
- [ ] LLM classification test passes with at least 70% accuracy
- [ ] No changes to cascade framework code
