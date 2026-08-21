# Model Benchmarks

> Historical results below are point-in-time artifacts. They must not be read as current zero-false-negative or TCO guarantees; validate the raw false-negative fields and model/hardware coverage for the workload being deployed.

## Classification Accuracy (20-signal AAP test, Xeon 6 CPU)

All models tested with the same 20 AAP signals covering task failures, warnings, config changes, job events, and playbook stats. Temperature=0, deterministic output confirmed across 3 runs.

### Results Summary

| Model | Params | Score | Avg Latency | Dangerous | Safe Over | Platform |
|-------|--------|-------|-------------|-----------|-----------|----------|
| granite-3-2-8b-instruct-cpu | 8.8B | **14/20** | 860ms | **0** | 6 | racmaas CPU |
| phi4-mini | 3.8B | **14/20** | 734ms | **0** | 6 | Oberon CPU |
| granite-4.1-3b | 3.4B | 14/20 | 888ms | 3 | 3 | Oberon CPU |
| granite-2b-cpu | 2B | 13/20 | 677ms | 1 | 6 | racmaas CPU |
| gemma3-4b | 4B | 8/10* | 1,338ms | 0 | 2 | Oberon CPU |
| llama32-1b | 1B | 5/10* | 689ms | 5 | 0 | Oberon CPU |

*Tested on 10-signal subset only

### Failure Analysis

**Dangerous failures** — signal dismissed when it should have been escalated:

| Model | Signal | Expected | Got |
|-------|--------|----------|-----|
| granite-4.1-3b | task_runner_on_failed (provision) | real_incident | needs_attention |
| granite-4.1-3b | task_warning (disk 89%) | needs_attention | routine_noise |
| granite-4.1-3b | task_runner_on_failed (net policy) | real_incident | needs_attention |
| granite-2b-cpu | task_runner_on_failed (provision) | real_incident | needs_attention |
| llama32-1b | All 5 failures | real_incident/needs_attention | routine_noise |

**Safe failures** — over-escalated (let through when it didn't need to):

All models over-escalate `config_delete_credential` and `job_failed` to `real_incident` instead of `needs_attention`/`known_pattern`. This is the safe failure mode — the signal gets more attention than needed but is never dismissed.

### Key Findings

1. **phi4-mini and granite-8b-instruct both have 0 dangerous misses.** Every error is over-escalation. For a signal compression system, this is the correct bias.

2. **granite-4.1-3b has the most balanced accuracy** (fewer over-escalations) but at the cost of 3 dangerous misses. Not suitable as the sole micro-tier model.

3. **llama32-1b is not viable.** Classifies everything as `routine_noise`. Too small for classification tasks.

4. **known_pattern is universally hard.** No model can identify recurring failures from a single signal. This is where the cascade's promotion engine adds value — it learns patterns over time that the LLM can't see in one classification.

5. **Terse prompt outperforms context-rich prompt.** phi4-mini scored 14/20 with terse, 13/20 with detailed context. More instructions confused the model.

6. **All models are 100% deterministic at temp=0.** Three consecutive runs produced identical output for every signal on every model.

### Prompt Comparison

| Prompt | phi4-mini | granite-3b |
|--------|-----------|-----------|
| Terse ("one word only") | 14/20 | 14/20 |
| Context-rich (with examples) | 13/20 | 14/20 |

Terse prompt:
```
You are classifying Ansible Automation Platform (AAP) signals.
Classify as exactly one of: routine_noise, known_pattern,
needs_attention, real_incident. Answer with one word only.
```

## Throughput Benchmarks (Oberon, Xeon 6767P 128c)

From expanded shootout (`benchmarks/results/expanded-shootout-20260804.json`):

| Model | Quality | Avg Latency | P95 Latency |
|-------|---------|-------------|-------------|
| phi4-mini | 93.5% | 705ms | 931ms |
| gemma3-4b | 80% (10-signal) | 1,303ms | — |
| smollm2-360m | 35% (limited) | 54.7 tok/s | 676ms |
| granite-2b | 15.2 tok/s | 4,240ms | — |

## Prompt Tuning

### K8s Granite Prompt Tuning

Tuned prompt for granite-3-2-8b-instruct-cpu on K8s signals. The original terse prompt produced a 0.9% noise rate (LLM classified almost nothing as noise). After tuning with domain-specific guidance, the noise rate increased to 37.3% while maintaining 0 false negatives.

| Metric | Before Tuning | After Tuning |
|--------|--------------|--------------|
| Noise rate | 0.9% | 37.3% |
| False negatives | 0 | 0 |
| Compression | 70-74% | 72.9% |

The tuned prompt tells the LLM what "routine" looks like in Kubernetes context (normal pod cycling, expected CronJob completions, info-severity events on healthy nodes). Without this, the model conservatively escalates everything.

## Precision Metric

Independent quality check across all domains. Tests whether signals classified as "important" are genuinely important.

```
Result:  100% (30/30 "important" signals confirmed)
Method:  Sample important-classified signals, verify against ground truth
```

This is distinct from the per-domain FN rate. The precision metric checks the other direction: are we wasting LLM time on signals that aren't actually important?

## Live Cascade Performance

### K8s cascade (production cluster, granite-3-2-8b-instruct-cpu, tuned prompt)

**Peak sustained run:**
```
Signals:     68.7M
Compression: 99.5%
Agents:      23 activated, 37 total (all green on rubric)
FN:          0
```

**Current sustained run (30+ hours, multi-cluster):**
```
Decisions:   176K+ written to immutable ledger
Latency:     581ms avg, no degradation over 30+ hours
Agents:      self-tuning, continue to activate and stabilize
FN:          0
```

**Earlier run (3.3M signals, tuned prompt validation):**
```
Signals:     3,370,405
Compression: 72.9%
Classified:  77,080 (noise: 28,751 = 37.3%, important: 48,329)
Activated:   6 agents (self-discovered)
FN:          0
Latency:     ~595ms per classification
```

### AAP cascade (production cluster, granite-3-2-8b-instruct-cpu)

```
Signals:     1.0M+
Compression: 96.0%
Activated:   5 agents (self-discovered)
FN:          0
Latency:     ~610ms per classification
```

## Throughput Capacity

Single granite-8b replica (8 CPU, 8 GB, --parallel 4):
- ~400 classifications/min
- At 98% compression: 20,000 signals/min = 28.8M/day

On one Xeon 6 server (128 cores):

| Config | Replicas | Daily Capacity |
|--------|----------|---------------|
| granite-8b | 8 | 46M-230M signals/day |
| granite-2b | 25 | 144M-720M signals/day |

Reference enterprise volumes:
- Small bank: ~1M signals/day
- Large telco: ~10M signals/day
- Hyperscaler: ~100M signals/day
