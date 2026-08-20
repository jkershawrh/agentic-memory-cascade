# Your AI Platform Forgets Everything It Learns. Here's How to Fix That.

**Author:** Jonathan Kershaw
**Target:** Red Hat Developer blog (developers.redhat.com/blog)
**Keywords:** cascade compression, institutional memory, signal processing, CPU inference, federated AI, OpenShift, Intel Xeon

---

Your monitoring system processed a million signals yesterday. Your automation platform ran ten thousand jobs. Your security stack evaluated a hundred thousand events. How many of those signals actually mattered?

More importantly — does your system remember which ones mattered last time?

It doesn't. And that's the problem nobody is solving.

## The filing cabinet problem

Every enterprise has the same architecture for institutional knowledge: store everything, query later. Splunk, Elastic, a data lake, an S3 bucket full of JSON. The theory is that if you keep every signal, you can always go back and find what you need.

In practice, this is a filing cabinet the size of a warehouse. It remembers every heartbeat check, every routine success, every duplicate flood. What it cannot do is tell you what mattered. Search requires knowing what to search for — which assumes the knowledge you're trying to build.

When your senior SRE leaves, they take with them twenty years of pattern recognition: "last time the scheduler showed pressure and the AAP job failed simultaneously, the root cause was a storage controller firmware bug." That association exists nowhere in your data lake. It lived in a person's head, and now it's gone.

Your system has perfect recall and zero comprehension.

## What if your signal pipeline could remember?

We built [cascade compression](https://github.com/jkershawrh/cascade-compression) to prove that most AI signals don't need AI — a self-tuning pipeline that achieves 99.1% compression on 142.4 million production Kubernetes signals with zero false negatives. The framework discovers what is noise, validates it continuously, and handles it in microseconds. Only the 1% that actually needs inference ever touches an LLM.

But here's what we noticed: the signals that survive the cascade — the 1% that matter — are exactly the signals worth remembering. They are the production outages, the security breaches, the novel failure modes. If you store them, you get institutional memory for free.

Not a data lake. Not a log. A curated, compressed archive of events the system determined were significant enough to require deep reasoning. Institutional knowledge that forms automatically from your own signal streams.

## How it works

The cascade already has the primitives of memory. It just wasn't naming them that way.

**Encoding.** When the corpus analyzer detects a recurring pattern and proposes a new agent, the system is encoding — it noticed something and created a representation for it. The five-tier promotion ladder (draft → candidate → nano → micro → macro) is consolidation: the representation is tested, strengthened, and moved from volatile to durable storage.

**Recall.** Every nano agent that fires on an incoming signal is performing recall. The agent doesn't look up a database row. It IS the learned pattern, expressed as executable logic. "I have seen this before, and I know what it means."

**Forgetting.** The 72-hour TTL on activated agents is natural decay. If a pattern doesn't recur, the agent expires. Shadow validation and governance audits trigger corrective forgetting — the system unlearns what it got wrong.

We extended these primitives into five explicit memory capabilities.

### 1. Survivor archive

Signals that survive the cascade — the ones that need inference — are automatically stored as memories. Each memory has a strength score derived from severity (critical signals start strong) that decays exponentially over time and is reinforced whenever the memory is recalled or the same signal recurs.

The archive is capacity-bounded (default 10,000 memories). When full, the weakest memories are evicted. This means the archive naturally accumulates the most significant events. After hours of processing, it's a summary. After days, it's institutional knowledge. After months, it's organizational memory.

Content-hash deduplication ensures that the same event occurring repeatedly doesn't fill the archive — it reinforces the existing memory instead. One strong memory is worth more than a thousand duplicates.

### 2. Recall

When a new signal arrives, the recall engine searches the archive: "Have I seen anything like this before?" Four similarity functions — signal type match, label overlap, content feature cosine similarity, and text trigram matching — produce a composite score weighted by memory strength.

This turns the cascade into a case-based reasoning engine. New signal → find similar survivors → surface what happened last time → inform the current response. The system gets smarter as its memory grows.

Performance is under 50 milliseconds for 1,000 memories. Pure Python. No vector database. No external dependencies. It runs on the same CPU that runs the cascade.

### 3. Consolidation

Periodically, the archive re-runs its memories through the current cascade pipeline. This is the machine equivalent of memory consolidation during sleep — replaying events and deciding what to keep.

As the cascade evolves — new agents discovered, new patterns learned — previously novel signals may now be recognized as noise. A heartbeat check that seemed important on day one is recognized as routine by day thirty. Consolidation applies current knowledge to old memories. Noise decays. Critical incidents survive indefinitely because the pipeline never suppresses them (zero false-negative invariant).

The archive naturally compresses over time, keeping only what still matters.

### 4. Priming

After a significant incident — a critical memory formation — the system enters a heightened attention state. A priming window opens for related signal types: info-severity signals that would normally be dropped by the severity gate are instead escalated to the LLM for classification.

The priming agent can only escalate, never suppress. It strictly increases sensitivity in the fail-open direction. The effect decays linearly over a configurable window (default 4 hours). This mirrors biological priming — your brain is more sensitive to car horns for hours after a near-miss. The cascade is more sensitive to related signals after a production incident.

### 5. Federation

This is where it gets interesting.

Multiple cascade instances — each monitoring a different signal source — can share memories. A Kubernetes cascade and an Ansible cascade each form their own domain-specific memories independently. A federation job exports survivors from both and imports them into a central memory aggregator.

When the same content hash appears from two independent sources, the strength boost is the system recognizing that something real happened — multiple independent observers saw the same thing. Cross-source correlation becomes associative memory: "Last time network latency spiked AND an AAP job failed, the root cause was a storage controller firmware bug."

No single cascade could form that association. The federated cascade discovers it automatically.

## Per-tier model routing

The memory architecture introduces per-tier model selection. Not every signal deserves the same model. The cascade routes to different LLMs based on severity:

- **Micro tier** (medium/low severity): A fast, small model like granite-2b. Handles the bulk of inference in 500-700ms on Xeon 6. This is the workhorse — the vast majority of signals that survive the nano tier are routine classifications.
- **Macro tier** (critical/high severity): A deeper reasoning model like granite-8b-instruct. 800-900ms on the same hardware. Reserved for signals where the classification decision has real consequences — a missed fraud transaction, a dropped security alert, a production outage that needs root cause analysis.

Both models run on CPU. No GPU required. The per-tier routing reduces average inference latency because most signals hit the faster model, while the signals that genuinely need deeper reasoning get it. Two environment variables: `CASCADE_MICRO_MODEL` and `CASCADE_MACRO_MODEL`. Set them to the same model if you want uniform inference, or split them when you want to optimize the cost-accuracy tradeoff.

The memory domain pack — the domain configuration for the aggregator — uses its own classification taxonomy optimized for knowledge curation: `routine_noise` (session ephemera, expired config), `known_pattern` (already in the knowledge base), `needs_attention` (novel insight worth adding), `real_incident` (contradiction or correction that must be enforced globally). The macro model handles the last two. The micro model handles the first two. The domain pack decides which model you need before the model ever runs.

## What the deployment looks like

The federated cascade runs as three pods and a CronJob on Red Hat OpenShift. No new infrastructure — it deploys alongside your existing workloads on the same Intel Xeon 6 hardware. The entire stack (cascade engine, LLM inference, memory archive, governance) runs on 16.5 CPU cores and 16 GB of RAM. On a 128-core Xeon 6 server, that's 13% utilization with full governance enabled.

```
K8s Events  ──→ [ cascade-k8s  ] ──→ K8s Memories  ──┐
                                                       │  every 5 min
AAP Signals ──→ [ cascade-aap  ] ──→ AAP Memories  ──┤──→ Federation
                                                       │      Job
                                                       ▼
                                               [ cascade-memory ]
                                               (memory aggregator)
```

```bash
# Deploy the entire federated stack
oc apply -f deploy/openshift-federated.yaml
oc create secret generic cascade-llm \
  --from-literal=url=https://your-llm/v1 \
  --from-literal=key=sk-... \
  -n cascade-compression
```

Each cascade instance persists state to a PVC. The federation CronJob runs every 5 minutes, exporting memories above a minimum strength threshold and importing them into the aggregator. The aggregator runs consolidation, priming, and recall across both domains.

The memory aggregator's `/recall` endpoint is the organization's institutional memory, queryable in real time: "Given this signal, what have we seen before — across all sources?"

## The numbers

592 tests. Zero failures. Zero regressions against the validated base pipeline.

The memory architecture preserves every safety invariant of the original cascade:

- **Zero false-negative gate**: Memory formation happens after pipeline processing. Pipeline decisions are identical with or without the archive.
- **Shadow validation**: Continues to re-check suppressed signals. Disagreements still trigger instant demotion.
- **Governance audit**: The immutable ledger records memory events alongside cascade decisions.

Memory-specific metrics measured at runtime: archive size, recall latency, consolidation compression ratio, federated source count, priming escalation count. Every number is queryable via `/memories/stats`.

## The bigger point

Organizations don't just need cheaper inference. They need systems that learn.

The standard enterprise knowledge management stack — wikis, runbooks, incident postmortems — depends on humans writing things down. Most of the time, they don't. When they do, the documentation rots as the system evolves. When the person who wrote it leaves, the context goes with them.

Cascade compression offers a different model. Institutional memory that forms automatically, compresses what doesn't matter, strengthens what recurs, and surfaces precedent when new incidents arrive. Not a filing cabinet. Not a knowledge base someone has to maintain. Memory that works the way memory should — by deciding what matters and letting everything else go.

The cascade doesn't replace human judgment. It captures and preserves the output of human judgment — via LLM classification that encodes expert reasoning — and makes it durable.

Your data lake remembers everything. The cascade remembers what mattered.

There is a compression ratio that applies to human knowledge too. Of the thousands of incidents your team has handled, a handful shaped how your organization operates. Those are your core memories. The cascade finds them automatically, strengthens them through repetition, and makes them available to every system in the fleet — not just the person who happened to be on call that night.

---

**Try it.** The cascade compression framework is open source. Deploy it on OpenShift, point it at any structured signal stream, and within an hour it tells you what percentage of your signals need AI. Within a day, it starts forming institutional memory.

- [GitHub: cascade-compression](https://github.com/jkershawrh/cascade-compression)
- [Red Hat OpenShift trial](https://www.redhat.com/en/technologies/cloud-computing/openshift/try-it)
