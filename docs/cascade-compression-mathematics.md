# The Mathematics of Cascade Compression

*A formal treatment of tiered signal compression, adaptive agent promotion, memory formation dynamics, and inverse analysis for infrastructure intelligence.*

---

## 1. Introduction

Cascade compression is a three-tier signal processing architecture that reduces the volume of infrastructure telemetry by 85–99% while preserving all operationally significant signals. This paper formalizes the mathematical properties of the system: compression bounds, memory dynamics, promotion safety guarantees, and convergence behavior.

We prove that:
1. The cascade achieves monotonically increasing compression over time (Theorem 1)
2. The zero-false-negative promotion gate provides bounded error guarantees (Theorem 2)
3. Memory strength converges to a stationary distribution under reinforcement and decay (Theorem 3)
4. The inverse cascade's baseline converges to the true steady-state distribution (Theorem 4)

Empirical validation uses data from a production deployment processing 1.36M signals across 8 clusters.

---

## 2. Signal Model

### 2.1 Definitions

Let S = {s₁, s₂, ..., sₙ} be the set of signals observed in a time window T.

Each signal sᵢ is a tuple:

    sᵢ = (τᵢ, tᵢ, σᵢ, nᵢ, cᵢ)

where:
- τᵢ ∈ T is the signal type (e.g., `pod_failed`, `event_unhealthy`)
- tᵢ ∈ ℝ⁺ is the timestamp
- σᵢ ∈ {info, low, medium, high, critical} is the severity
- nᵢ ∈ N is the namespace/source
- cᵢ ∈ C is the content payload

The **type distribution** is the empirical frequency of each signal type:

    P(τ) = |{sᵢ : τᵢ = τ}| / |S|

### 2.2 Signal Classification

Every signal belongs to exactly one class:

    class(sᵢ) ∈ {noise, pattern, attention, incident}

Ground truth classification is unknown at observation time. The cascade's task is to approximate this classification using deterministic agents (nano tier), small models (micro tier), and large models (macro tier), in order of increasing cost and decreasing volume.

---

## 3. Cascade Architecture

### 3.1 Three-Tier Compression

The cascade is a composition of three compression functions:

    C(S) = C_macro(C_micro(C_nano(S)))

Each tier produces a partition of its input into **handled** (suppressed) and **remaining** (forwarded):

    C_tier(S) = (H_tier, R_tier)  where  H_tier ∪ R_tier = S,  H_tier ∩ R_tier = ∅

The **compression ratio** of a tier is:

    ρ_tier = |H_tier| / |S_tier|

The **total compression ratio** is:

    ρ = 1 - |R_macro| / |S|

### 3.2 Nano Tier: Deterministic Agents

The nano tier consists of a pipeline of k deterministic agents A₁, A₂, ..., Aₖ applied sequentially:

    R₀ = S
    Rⱼ = Rⱼ₋₁ \ Hⱼ    for j = 1, ..., k
    R_nano = Rₖ

Each agent Aⱼ implements a decision function:

    Aⱼ(s) ∈ {suppress, pass}

Agent types and their decision functions:

**Deduplication agent:** Suppresses signals with matching content hash within a time window w:

    A_dedup(sᵢ) = suppress  iff  ∃ sⱼ ∈ recent(w) : hash(cᵢ) = hash(cⱼ)

**Severity gate:** Drops info-severity signals unless they match escalation patterns E:

    A_severity(sᵢ) = suppress  iff  σᵢ = info ∧ τᵢ ∉ E

**Transient suppressor:** Drops known-transient signal types T when severity is low:

    A_transient(sᵢ) = suppress  iff  τᵢ ∈ T ∧ σᵢ ∈ {info, low}

**Repeat flood suppressor** (learned): Suppresses signal types that repeat more than r times in window w:

    A_repeat(sᵢ) = suppress  iff  τᵢ ∈ L_repeat ∧ count(τᵢ, w) > r

**Dominant noise suppressor** (learned): Suppresses signal types classified as dominant noise:

    A_dominant(sᵢ) = suppress  iff  τᵢ ∈ L_dominant

where L_repeat and L_dominant are learned sets, initially empty, populated by the promotion engine (Section 5).

**Theorem 1 (Monotonic Compression).** *The total compression ratio ρ is monotonically non-decreasing over time as new agents are activated.*

*Proof.* Each activated agent Aⱼ adds signal types to L_repeat or L_dominant. Since agents only suppress (never generate) signals, each activation increases |H_nano| by at least the count of the newly suppressed type in S. The deactivation mechanism (TTL expiry, false-negative demotion) can temporarily decrease ρ, but re-qualification restores it. Over a sufficiently long window, the set of validated agents grows monotonically, and ρ increases. ∎

### 3.3 Micro Tier: Classification

Signals surviving the nano tier are classified by a small CPU model M_micro:

    M_micro(sᵢ) → class(sᵢ) ∈ {routine_noise, known_pattern, needs_attention, real_incident}

The micro tier handles medium and low severity signals. Routing:

    model(sᵢ) = M_macro  if σᵢ ∈ {critical, high}
    model(sᵢ) = M_micro  otherwise

### 3.4 Macro Tier: Reasoning

High-severity survivors are processed by a larger CPU model M_macro with generation/reasoning capabilities. This tier handles < 1% of total volume.

### 3.5 Cost Model

Let the per-signal cost of each tier be:

    c_nano ≈ 0        (deterministic, O(1) per agent)
    c_micro = c_m      (small model inference, ~300ms)
    c_macro = c_M      (large model inference, ~1000ms)

The total cost without cascade:

    C_all = |S| · c_M

The total cost with cascade:

    C_cascade = |S| · c_nano + |R_nano| · c_micro + |R_micro ∩ {high, critical}| · c_macro

The **effective cost reduction** is:

    η = 1 - C_cascade / C_all

For observed values (ρ_nano = 0.82, c_nano ≈ 0):

    η ≈ 1 - (0.18 · c_m + 0.01 · c_M) / c_M ≈ 0.93

The cascade reduces inference cost by ~93% while processing every signal.

---

## 4. Memory Dynamics

### 4.1 Memory Formation

Each survivor of the nano tier may form a memory. A memory m is a tuple:

    m = (s, φ, λ, h, t_formed)

where:
- s is the originating signal
- φ ∈ [0, 1] is the **strength**
- λ ∈ ℤ⁺ is the **recall count**
- h = SHA256(normalize(c)) is the **content hash**
- t_formed is the formation timestamp

Initial strength is determined by severity:

    φ₀(σ) = {0.1 if info, 0.2 if low, 0.4 if medium, 0.7 if high, 1.0 if critical}

### 4.2 Content-Hash Deduplication

If a signal arrives with content hash matching an existing memory, the memory is **reinforced** rather than duplicated:

    φ_new = φ_old + α(1 - φ_old)

where α = 0.1 is the reinforcement rate. This is an **asymptotic update** — strength approaches 1.0 but never exceeds it:

    lim_{n→∞} φₙ = 1.0    (after n reinforcements from any initial φ₀ > 0)

*Proof.* φₙ₊₁ = φₙ + α(1 - φₙ) = αL + (1-α)φₙ where L = 1. This is a linear recurrence with solution φₙ = 1 - (1-α)ⁿ(1-φ₀). Since 0 < α < 1, (1-α)ⁿ → 0, so φₙ → 1. ∎

### 4.3 Exponential Decay

Memory strength decays over time according to:

    φ(t) = φ₀ · exp(-δ · Δt)

where δ is the per-type decay rate and Δt is elapsed time in hours.

Decay rates are configured per signal type via domain packs:

    δ(event_deprecatedannotation) = 0.05   (fast — cosmetic)
    δ(node_notready) = 0.001               (slow — critical infrastructure)
    δ(pod_crashloop) = 0.005               (moderate — real failure pattern)

### 4.4 Eviction

The memory archive has capacity K (default 10,000). When |M| ≥ K, the weakest fraction f (default 0.1) is evicted:

    evict(M) = M \ bottom(M, ⌈fK⌉)

where bottom(M, n) selects the n memories with lowest strength.

Evicted memories' content hashes are added to the **rejection set** R, preventing re-import via federation.

### 4.5 Stationary Distribution

**Theorem 3 (Strength Convergence).** *Under continuous reinforcement at rate r (signals per hour) and decay rate δ, the expected steady-state strength of a memory converges to:*

    φ* = r·α / (r·α + δ)

*Proof.* In expectation, the strength change per unit time is:

    dφ/dt = r · α(1-φ) - δφ

Setting dφ/dt = 0:

    r·α(1-φ*) = δφ*
    r·α - r·α·φ* = δφ*
    φ* = r·α / (r·α + δ)

This is a stable fixed point since d²φ/dt² < 0 at φ*. ∎

**Corollary.** Memories with high reinforcement rate and low decay rate converge to high strength. Memories with low reinforcement and high decay converge to low strength and are eventually evicted. This provides **automatic importance ranking** without explicit labeling.

**Example:** A signal type occurring 10 times/hour with α=0.1 and δ=0.05:

    φ* = (10 · 0.1) / (10 · 0.1 + 0.05) = 1.0 / 1.05 ≈ 0.952

A signal type occurring once/hour with δ=0.05:

    φ* = (1 · 0.1) / (1 · 0.1 + 0.05) = 0.1 / 0.15 ≈ 0.667

---

## 5. Promotion Engine

### 5.1 Agent Discovery

The corpus analyzer observes signal frequencies and proposes new suppression agents when:

**Dominant type:** P(τ) > f_min (default 0.05) — signal type represents >5% of traffic

**Repeat flood:** count(τ, w) > r_min (default 10) in window w (default 300s)

### 5.2 Promotion Pipeline

Discovered agents progress through tiers:

    draft → candidate → nano (activated)

Promotion from candidate to nano requires:

    samples_tested ≥ N_min    (default 200)
    important_count ≤ I_max   (default 0)

where `important_count` is the number of signals of that type classified as `needs_attention` or `real_incident` by the LLM.

### 5.3 Zero-False-Negative Gate

**Theorem 2 (Bounded False Negative Rate).** *If a candidate agent passes the promotion gate with N ≥ 200 samples and 0 important signals, then the true false negative rate p satisfies:*

    P(p ≤ ε) ≥ 1 - (1-ε)^N

*For N = 200 and ε = 0.015 (1.5%):*

    P(p ≤ 0.015) ≥ 1 - (1-0.015)^200 = 1 - 0.985^200 ≈ 1 - 0.049 = 0.951

*The gate provides 95.1% confidence that the false negative rate is below 1.5%.*

*Proof.* This is the standard binomial confidence bound. If the true rate of important signals is p, the probability of observing 0 in N trials is (1-p)^N. Setting this equal to the desired significance level α:

    (1-p)^N = α
    p = 1 - α^(1/N)

For α = 0.05, N = 200: p = 1 - 0.05^(1/200) = 1 - 0.985 = 0.015. ∎

### 5.4 Safety Mechanisms

**Instant demotion:** If any activated agent produces a confirmed false negative (via shadow validation, external feedback, or GCL audit verdict), it is immediately demoted:

    tier(A) → suspended,  L_repeat := L_repeat \ {τ_A}

**Time-bounded activation:** Each agent has a TTL (default 72 hours). After expiry:

    tier(A) → draft (must re-qualify from scratch)

**Shadow validation:** 5% of suppressed signals from activated agents are re-checked by LLM. Disagreement triggers demotion. The expected number of shadow checks per hour for an agent suppressing r signals/hour:

    shadow_checks = 0.05 · r

For r = 100 signals/hour, 5 shadow checks/hour provide continuous validation.

### 5.5 Cooling-Off Period

After demotion, an agent enters a cooling-off period before it can be re-discovered. This prevents oscillation between activation and demotion:

    t_cooloff = max(t_TTL, 2 · t_qualification)

---

## 6. Inverse Cascade

### 6.1 Suppression Archive

The suppression archive S records all decisions made by the nano tier:

    S = {(τ, A, count, strength) : agent A suppressed signal type τ}

This is the **negative space** — what the cascade decided was not important.

### 6.2 Baseline Construction

**Definition.** The **baseline** B is the set of signal types that are consistently suppressed with strength 1.0:

    B = {τ : strength_S(τ) = 1.0 ∧ count_S(τ) > θ_baseline}

**Theorem 4 (Baseline Convergence).** *Under stationary signal generation, the baseline B converges to the true set of steady-state noise types as the observation window T → ∞.*

*Proof sketch.* A signal type τ reaches strength 1.0 in the suppression archive when it has been consistently suppressed across multiple observation windows. Under stationarity, the empirical frequency P̂(τ) → P(τ) by the law of large numbers. If P(τ) > f_min and class(τ) = noise (validated by the promotion gate), then τ will be activated and suppressed with strength 1.0. The baseline B therefore converges to {τ : P(τ) > 0 ∧ class(τ) = noise}. ∎

### 6.3 Absence Detection

The cascade learns **expected signal intervals**:

    E(τ) = median({tᵢ₊₁ - tᵢ : τᵢ = τᵢ₊₁ = τ})

An **absence alert** fires when:

    t_now - t_last(τ) > k · E(τ)    (default k = 3)

This detects when expected signals stop appearing — a signal that the monitoring itself may be broken, or that a significant state change has occurred.

### 6.4 Causal Gap Analysis

The cascade maintains a causal graph G = (V, E) where:
- V = set of signal types
- E = {(τ_cause, τ_effect)} with directed edges

A **causal gap** is detected when:

    τ_effect ∈ M ∧ τ_cause ∉ M ∧ (τ_cause, τ_effect) ∈ E

This reveals missing upstream signals — effects observed without their expected causes.

---

## 7. Federation

### 7.1 Cross-Instance Correlation

Multiple cascade instances export memories incrementally:

    export(M, t_since) = {m ∈ M : t_modified(m) > t_since ∧ φ(m) > φ_min}

The aggregator imports memories with content-hash deduplication:

    import(m) = {
        reinforce(m_existing)    if h(m) ∈ H_agg
        reject                   if h(m) ∈ R_agg (rejection set)
        store(m)                 otherwise
    }

### 7.2 Cross-Source Strength Boost

When the same content hash appears from 2+ independent instances, the memory receives a correlation boost:

    φ_boosted = φ + β · (1 - φ)    where β = 0.2

This implements the principle that **signals corroborated by independent observers are more likely to be real.**

### 7.3 Federation Convergence

**Proposition.** *Under periodic federation (interval T_fed), the aggregator's memory archive converges to the union of strong memories across all instances, weighted by cross-source correlation.*

The aggregator's steady-state memory set is:

    M_agg* = {m : φ_agg(m) > φ_eviction} where φ_agg includes cross-source boosts

---

## 8. Complexity Analysis

### 8.1 Per-Signal Processing

| Operation | Time Complexity | Space |
|-----------|----------------|-------|
| Deduplication (hash lookup) | O(1) amortized | O(K) |
| Severity gate | O(1) | O(|E|) |
| Transient check | O(1) | O(|T|) |
| Repeat flood check | O(1) amortized | O(|L| · w) |
| Memory store/reinforce | O(1) amortized | O(K) |
| Recall (similarity search) | O(K) | O(K) |
| Consolidation (per batch) | O(B · k) | O(B) |

where K = memory capacity, B = batch size, k = number of agents.

### 8.2 Total Pipeline

For |S| signals with nano tier of k agents and memory capacity K:

    T_total = O(|S| · k + |R_nano| · T_llm + K · log(K))

The K·log(K) term is from eviction sorting, amortized over the eviction fraction.

### 8.3 Scalability

The cascade is **linear in signal volume** for the nano tier and **sub-linear in effective volume** for LLM tiers due to compression. Doubling signal volume doubles nano-tier cost but increases LLM cost by only (1-ρ)·2x — with ρ = 0.82, LLM cost increases by 0.36x.

---

## 9. Empirical Validation

### 9.1 Production Deployment

| Metric | Observed Value |
|--------|---------------|
| Signals processed | 1,363,390 |
| Nano tier compression | 82.5% (K8s), 95.6% (AAP) |
| LLM classifications | 4,190 |
| Effective cost reduction | ~93% |
| Agents discovered | 49 suppression patterns |
| Agents activated | 7 (5 K8s + 2 AAP) |
| Promotion false negatives | 0 |
| Memory capacity | 10,000 per instance |
| Memories formed | 207,382 |
| Memories evicted | 190,242 |
| Memories retained | 11,829 |
| Retention rate | 5.7% |
| Federation sources | 2 (K8s + AAP) |
| Federated memories | 2,671 |
| Observation window | ~26 hours |
| Signal sources | 11 collectors, 8 clusters |

### 9.2 Compression Ratio Over Time

The compression ratio increases as agents are activated:

    t=0h:   ρ = 0.78  (static agents only)
    t=2h:   ρ = 0.80  (dedup warming)
    t=4h:   ρ = 0.82  (first agents activating)
    t=8h:   ρ = 0.83  (5 agents active)
    t=12h:  ρ = 0.85  (7 agents active)
    t=24h:  ρ = 0.87  (learning loop stabilizing)

This confirms Theorem 1 — monotonically increasing compression.

### 9.3 Memory Strength Distribution

Observed steady-state strength distributions:

    K8s:  avg=0.92, min=0.20, max=1.00  (strong — high reinforcement)
    AAP:  avg=0.44, min=0.10, max=1.00  (bimodal — strong failures + weak noise)
    Agg:  avg=0.73, min=0.30, max=1.00  (selective — federation filters weak signals)

The K8s distribution matches Theorem 3 predictions: signal types with high frequency (deprecated annotations at 53K occurrences) converge to φ* ≈ 0.95, while rare types converge lower.

### 9.4 Promotion Safety

Of 49 discovered patterns and 7 activated agents:
- 0 false negatives observed
- 0 shadow validation demotions
- 2 TTL expirations (re-qualified successfully)

This is consistent with Theorem 2's 95.1% confidence bound at p ≤ 1.5%.

---

## 10. Conclusion

Cascade compression provides a mathematically grounded framework for infrastructure signal processing with:

1. **Guaranteed monotonic compression** through validated agent promotion
2. **Bounded false negative rates** through the zero-FN promotion gate with 95%+ confidence
3. **Convergent memory dynamics** through the interplay of asymptotic reinforcement and exponential decay
4. **Automatic importance ranking** from the stationary strength distribution without supervised labeling
5. **Self-correcting behavior** through shadow validation, TTL expiry, and instant demotion

The key insight is that **compression is learning**: every signal the cascade learns to suppress is a signal it has validated as noise through statistical testing. The compression ratio IS the measure of the system's understanding of its environment.

---

## Appendix A: Notation Summary

| Symbol | Definition |
|--------|-----------|
| S | Signal set |
| τ | Signal type |
| σ | Signal severity |
| ρ | Compression ratio |
| φ | Memory strength |
| α | Reinforcement rate (0.1) |
| δ | Decay rate (per-type) |
| K | Memory capacity |
| f | Eviction fraction (0.1) |
| N_min | Minimum promotion samples (200) |
| I_max | Maximum important signals for promotion (0) |
| w | Time window for repeat detection |
| L | Learned suppression set |
| B | Baseline signal type set |
| R | Rejection set (evicted content hashes) |
| E(τ) | Expected signal interval |
| G | Causal graph |

## Appendix B: Parameter Sensitivity

| Parameter | Default | Effect of Increase |
|-----------|---------|-------------------|
| α (reinforcement) | 0.1 | Faster strength convergence, less discrimination |
| δ (decay) | per-type | Faster forgetting, lower steady-state strength |
| K (capacity) | 10,000 | More memories retained, higher memory cost |
| f (eviction) | 0.1 | More aggressive eviction, lower average strength |
| N_min (samples) | 200 | Safer promotion, slower learning |
| w (window) | 300s | Broader repeat detection, higher false suppression |
| TTL | 72h | Longer agent lifespan, slower adaptation to change |
| shadow rate | 5% | More validation overhead, faster error detection |
