"""Cascade bridge — connects collector → pipeline → LLM.

Standalone. No dependency on deepfield or any consuming application.
The bridge initializes the cascade pipeline, processes signals from any
collector, and optionally forwards survivors to an LLM for classification.
"""

import json
import logging
import os
import random
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from .cascade.agents import default_agents
from .cascade.corpus_analyzer import CorpusAnalyzer
from .cascade.dynamic_agents import ContextualNoiseSuppressor, DominantNoiseSuppressor, RepeatFloodSuppressor
from .cascade.inverse import SuppressionArchive
from .cascade.memory import MemoryArchive
from .cascade.pipeline import CascadePipeline
from .cascade.promotion import AgentMetrics, Baseline, PromotionEngine, RuleAgent
from .cascade.protocol import Signal, chat_completions_url

log = logging.getLogger(__name__)

_PROMOTION_MIN_SAMPLES = 200
_PROMOTION_ALLOWED_IMPORTANT = 0

_BUILTIN_AGENT_NAMES = frozenset({
    "deduplicate", "transient_suppressor", "severity_gate",
    "pattern_classifier", "threshold_classifier", "trend_detector",
})


def _is_noise_classification(classification: str) -> bool:
    return "routine_noise" in classification or "known_pattern" in classification


@dataclass
class BridgeStats:
    signals_processed: int = 0
    cascade_handled: int = 0
    cascade_forwarded: int = 0
    llm_classified: int = 0
    llm_noise: int = 0
    llm_important: int = 0
    compression_ratio: float = 0.0
    started_at: str = ""
    last_updated: str = ""


def _to_cascade_signal(sig: Any) -> Signal:
    """Convert any signal object to the cascade Signal protocol."""
    return Signal(
        signal_id=getattr(sig, "signal_id", uuid4()),
        signal_type=getattr(sig, "signal_type", ""),
        severity=getattr(sig, "severity", "info"),
        source=getattr(sig, "resource_name", ""),
        namespace=getattr(sig, "namespace", ""),
        cluster=str(getattr(sig, "cluster_id", ""))[:8],
        content={
            "resource_kind": getattr(sig, "resource_kind", ""),
            "message": getattr(sig, "evidence", {}).get("message", ""),
            **{k: v for k, v in getattr(sig, "evidence", {}).items() if k != "message"},
        },
        labels=getattr(sig, "labels", {}),
    )


class CascadeBridge:
    """Standalone cascade bridge.

    Usage::

        bridge = CascadeBridge(
            llm_url="https://llm.example.com/v1",
            llm_key="sk-...",
            llm_model="your-model-name",
            system_prompt="You are classifying...",
        )
        signals = collector.collect()
        result = bridge.process(signals)
    """

    def __init__(
        self,
        llm_url: str = "",
        llm_key: str = "",
        llm_model: str = "",
        system_prompt: str = "",
        domain: str = "default",
        ledger_url: str = "",
        ledger_token: str = "",
    ):
        self.domain = domain
        self._llm_url = llm_url or os.getenv("CASCADE_LLM_URL", os.getenv("LITELLM_API_BASE", ""))
        self._llm_key = llm_key or os.getenv("CASCADE_LLM_KEY", os.getenv("LITELLM_API_KEY", ""))
        self._llm_model = llm_model or os.getenv("CASCADE_LLM_MODEL", "")
        self._micro_model = os.getenv("CASCADE_MICRO_MODEL", "") or self._llm_model
        self._macro_model = os.getenv("CASCADE_MACRO_MODEL", "") or self._llm_model
        self._gpu_url = os.getenv("CASCADE_GPU_URL", "")
        self._gpu_key = os.getenv("CASCADE_GPU_KEY", "") or self._llm_key
        self._gpu_model = os.getenv("CASCADE_GPU_MODEL", "")
        self._gpu_analyses: list = []
        self._gpu_analyses_file = os.getenv("CASCADE_GPU_ANALYSES_FILE", "")
        self._system_prompt = system_prompt
        self._ledger_url = ledger_url or os.getenv("CASCADE_LEDGER_URL", "")
        self._ledger_token = ledger_token or os.getenv("CASCADE_LEDGER_TOKEN", "")

        self.enabled = True
        self.pipeline = CascadePipeline(default_agents())
        self._human_gate = bool(os.getenv("CASCADE_HUMAN_GATE", ""))
        self.promotion = PromotionEngine(human_gate_enabled=self._human_gate)
        self.stats = BridgeStats(started_at=datetime.now(timezone.utc).isoformat())

        self._repeat_suppressor = RepeatFloodSuppressor(max_repeats=3, window_seconds=300)
        self._noise_suppressor = DominantNoiseSuppressor(window_seconds=300)
        self._contextual_suppressor = ContextualNoiseSuppressor(
            context_key=os.getenv("CASCADE_CONTEXT_KEY", "namespace"),
            window_seconds=300,
        )
        self.pipeline.register(self._repeat_suppressor)
        self.pipeline.register(self._noise_suppressor)
        self.pipeline.register(self._contextual_suppressor)
        self._activated_types: set = set()
        self._activated_patterns: Dict[str, str] = {}
        self._activated_contexts: Dict[str, set] = defaultdict(set)

        self.corpus_analyzer = CorpusAnalyzer(
            min_frequency=0.05, min_repeat_count=10,
            repeat_window_seconds=300, analysis_interval=300,
        )

        self._llm_buffer: list = []
        self._llm_max_buffer = int(os.getenv("CASCADE_LLM_BUFFER", "10000"))
        self._llm_batch_size = int(os.getenv("CASCADE_LLM_BATCH", "500"))
        self._llm_max_concurrent = int(os.getenv("CASCADE_LLM_THREADS", "3"))
        self._llm_results: list = []
        self._llm_active_threads = 0
        self._llm_lock = threading.Lock()
        self._llm_noise_counts: Dict[str, int] = defaultdict(int)
        self._llm_important_counts: Dict[str, int] = defaultdict(int)
        self._llm_failure_counts: Dict[str, int] = defaultdict(int)
        self._llm_context_noise: Dict[str, int] = defaultdict(int)
        self._llm_context_important: Dict[str, int] = defaultdict(int)

        self._agent_metrics: Dict[str, AgentMetrics] = {}
        self._agent_rules: Dict[str, RuleAgent] = {}
        self._promotion_log: list = []
        self._last_promotion_time: float = 0
        self._promotion_interval: float = 300

        self._fn_count = 0
        self._fn_evaluated = 0
        self._fn_types: Dict[str, int] = defaultdict(int)
        self._gcl_builtin_fails = 0
        self._verdict_watermark: str = ""
        self._verdict_poll_running = False

        self._shadow_sample_rate = float(os.getenv("CASCADE_SHADOW_RATE", "0.05"))
        self._shadow_buffer: list = []
        self._shadow_max_buffer = 100
        self._shadow_running = False
        self._shadow_checks = 0
        self._shadow_demotions = 0

        self._activation_ttl_hours = float(os.getenv("CASCADE_ACTIVATION_TTL_HOURS", "72"))
        self._activation_timestamps: Dict[str, str] = {}

        self.baseline = Baseline(normal_ranges={})
        self._observed_ranges: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))

        self._memory_enabled = bool(os.getenv("CASCADE_MEMORY_ENABLED", "1"))
        self._memory_max = int(os.getenv("CASCADE_MEMORY_MAX", "10000"))
        self.memory_archive = MemoryArchive(max_capacity=self._memory_max) if self._memory_enabled else None
        self.suppression_archive = SuppressionArchive(max_capacity=self._memory_max) if self._memory_enabled else None

        self._state_file = os.getenv("CASCADE_STATE_FILE", "")

        # Meta-cascade: self-monitoring signals
        self._meta_signals: list = []
        self._meta_max_buffer = 500
        self._meta_target = os.getenv("CASCADE_META_TARGET", "")

        self._restore_state()

    def process(self, signals: list) -> Dict:
        if not self.enabled or not signals:
            return {"enabled": False}

        cascade_signals = [_to_cascade_signal(s) for s in signals]

        t0 = time.monotonic()
        cascade_result = self.pipeline.run(cascade_signals)
        cascade_ms = (time.monotonic() - t0) * 1000

        self._last_remaining = list(cascade_result.remaining)
        remaining_count = len(cascade_result.remaining)
        handled_count = len(signals) - remaining_count
        self.stats.signals_processed += len(signals)
        self.stats.cascade_handled += handled_count
        self.stats.cascade_forwarded += remaining_count
        if self.stats.signals_processed > 0:
            self.stats.compression_ratio = self.stats.cascade_handled / self.stats.signals_processed
        self.stats.last_updated = datetime.now(timezone.utc).isoformat()

        self.corpus_analyzer.observe(cascade_signals)

        has_suppressors = self._activated_types or self._activated_contexts
        if has_suppressors and self._shadow_sample_rate > 0 and self._llm_url:
            self._queue_shadow_samples(cascade_result, cascade_signals)

        if self.suppression_archive is not None:
            self.suppression_archive.record_batch(
                cascade_result.decisions, cascade_signals)

        if self.memory_archive is not None:
            for sig in cascade_result.remaining:
                classification = ""
                for d in cascade_result.decisions:
                    if d.signal_id == sig.signal_id and d.classification:
                        classification = d.classification
                        break
                self.memory_archive.store(sig, classification=classification)

        # Meta: detect memory pressure (above 80% capacity)
        if self.memory_archive is not None:
            mem_usage = self.memory_archive.size / max(1, self.memory_archive._max_capacity)
            if mem_usage > 0.8:
                self._emit_meta(
                    "meta_memory_pressure", "medium",
                    f"Memory archive at {mem_usage:.0%} "
                    f"({self.memory_archive.size}/{self.memory_archive._max_capacity})",
                    usage=round(mem_usage, 3),
                    size=self.memory_archive.size,
                    capacity=self.memory_archive._max_capacity,
                )

        for sig in cascade_result.remaining:
            self._llm_buffer.append({
                "signal_type": sig.signal_type,
                "severity": sig.severity,
                "namespace": sig.namespace,
                "content": sig.content,
            })
        if len(self._llm_buffer) > self._llm_max_buffer:
            self._llm_buffer = self._llm_buffer[-self._llm_max_buffer:]

        # Meta: detect LLM starvation (buffer above 80% and all threads busy)
        buffer_pct = len(self._llm_buffer) / max(1, self._llm_max_buffer)
        if (buffer_pct > 0.8
                and self._llm_active_threads >= self._llm_max_concurrent
                and self._llm_url):
            self._emit_meta(
                "meta_llm_starvation", "high",
                f"LLM buffer at {buffer_pct:.0%} ({len(self._llm_buffer)}/{self._llm_max_buffer}) "
                f"with {self._llm_active_threads}/{self._llm_max_concurrent} threads busy",
                buffer_size=len(self._llm_buffer),
                buffer_max=self._llm_max_buffer,
                active_threads=self._llm_active_threads,
                max_threads=self._llm_max_concurrent,
            )

        while self._llm_buffer and self._llm_active_threads < self._llm_max_concurrent and self._llm_url:
            batch = list(self._llm_buffer[:self._llm_batch_size])
            self._llm_buffer = self._llm_buffer[self._llm_batch_size:]
            with self._llm_lock:
                self._llm_active_threads += 1
            threading.Thread(target=self._run_llm, args=(batch,), daemon=True).start()

        if self._shadow_buffer and not self._shadow_running and self._llm_url:
            batch = list(self._shadow_buffer[:20])
            self._shadow_buffer = self._shadow_buffer[20:]
            threading.Thread(target=self._run_shadow_llm, args=(batch,), daemon=True).start()

        now = time.monotonic()
        if now - self._last_promotion_time > self._promotion_interval:
            self._last_promotion_time = now
            self._discover_and_promote()
            self._flush_memory_events()
            self._save_state()
            if self._ledger_url and not self._verdict_poll_running:
                threading.Thread(
                    target=self._poll_gcl_verdicts, daemon=True,
                ).start()

        if self._ledger_url:
            threading.Thread(
                target=self._write_to_ledger,
                args=(cascade_result, cascade_signals),
                daemon=True,
            ).start()

        return {
            "enabled": True,
            "signals": len(signals),
            "cascade_ms": round(cascade_ms, 1),
            "compression": round(cascade_result.compression_ratio, 3),
        }

    def _discover_and_promote(self):
        new_agents = self.corpus_analyzer.maybe_analyze()
        for agent_metrics, rule in new_agents:
            sig_type = agent_metrics.config.get("signal_type", "")
            pattern_type = agent_metrics.config.get("pattern_type", "")
            if not sig_type or pattern_type not in {"repeat_flood", "dominant_type"}:
                continue

            self._agent_metrics[agent_metrics.name] = agent_metrics
            self._agent_rules[agent_metrics.name] = rule
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "discovered",
                "agent": agent_metrics.name,
                "tier": "draft",
                "status": "awaiting_llm_validation",
                "config": agent_metrics.config,
            })

        for name, agent_metrics in self._agent_metrics.items():
            sig_type = agent_metrics.config.get("signal_type", "")
            if not sig_type or sig_type in self._activated_types:
                continue

            noise = self._llm_noise_counts.get(sig_type, 0)
            important = self._llm_important_counts.get(sig_type, 0)
            total = noise + important
            if total == 0:
                continue

            unsafe_rate = important / total
            agent_metrics.samples_tested = total
            agent_metrics.accuracy = noise / total
            agent_metrics.false_positive_rate = unsafe_rate
            agent_metrics.false_negative_rate = unsafe_rate
            agent_metrics.coverage = 1.0
            agent_metrics.rubric_status = (
                "green"
                if total >= _PROMOTION_MIN_SAMPLES
                and important <= _PROMOTION_ALLOWED_IMPORTANT
                else "red"
            )

            previous_tier = agent_metrics.tier
            while agent_metrics.tier in {"draft", "candidate"}:
                current_tier = agent_metrics.tier
                self.promotion.check_promotion(agent_metrics)
                if agent_metrics.tier == current_tier:
                    break
            if agent_metrics.tier != previous_tier:
                self._promotion_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "promoted",
                    "agent": name,
                    "tier": agent_metrics.tier,
                    "status": agent_metrics.rubric_status,
                    "samples": total,
                    "important": important,
                })

            if (
                agent_metrics.tier != "nano"
                or total < _PROMOTION_MIN_SAMPLES
                or important > _PROMOTION_ALLOWED_IMPORTANT
            ):
                continue

            pattern_type = agent_metrics.config["pattern_type"]
            if pattern_type == "repeat_flood":
                self._repeat_suppressor.add_signal_type(sig_type)
            else:
                self._noise_suppressor.add_noise_type(sig_type)
            self._activated_types.add(sig_type)
            self._activated_patterns[sig_type] = pattern_type
            self._activation_timestamps[sig_type] = datetime.now(timezone.utc).isoformat()
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "activated",
                "agent": name,
                "tier": "nano",
                "status": "validated",
                "samples": total,
                "important": important,
                "config": agent_metrics.config,
            })
            log.info("ACTIVATED: %s (%d noise, %d important)",
                     sig_type, noise, important)
            self._emit_meta(
                "meta_agent_activated", "info",
                f"Agent activated for {sig_type} ({pattern_type})",
                agent=name, pattern_type=pattern_type,
                samples=total, noise=noise, important=important,
            )

        self._discover_contextual_noise()
        self._check_activation_ttl()
        self._flush_promotion_events()

    def _discover_contextual_noise(self):
        """Promote (signal_type, context) pairs as contextual noise.

        When a type has mixed importance overall (can't be promoted),
        check if specific contexts within it are pure noise.
        """
        _CTX_MIN_SAMPLES = 100
        _CTX_MAX_IMPORTANT_RATE = 0.02

        for ctx_key, noise_count in self._llm_context_noise.items():
            sig_type, ctx_value = ctx_key.split(":", 1)
            if not ctx_value:
                continue
            if sig_type in self._activated_types:
                continue
            if ctx_value in self._activated_contexts.get(sig_type, set()):
                continue

            important = self._llm_context_important.get(ctx_key, 0)
            total = noise_count + important
            if total < _CTX_MIN_SAMPLES:
                continue
            important_rate = important / total
            if important_rate > _CTX_MAX_IMPORTANT_RATE:
                continue

            self._contextual_suppressor.add_noise_context(sig_type, ctx_value)
            self._activated_contexts[sig_type].add(ctx_value)
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "contextual_activated",
                "agent": f"ctx_{sig_type}_{ctx_value}",
                "tier": "nano",
                "status": "validated",
                "samples": total,
                "important": important,
                "important_rate": round(important_rate, 4),
                "context_key": self._contextual_suppressor._context_key,
                "context_value": ctx_value,
            })
            log.info("CONTEXTUAL ACTIVATION: %s in %s=%s (%d samples, %.1f%% important)",
                     sig_type, self._contextual_suppressor._context_key,
                     ctx_value, total, important_rate * 100)

    def _check_activation_ttl(self):
        """Suspend agents whose activation has expired."""
        if not self._activation_timestamps or self._activation_ttl_hours <= 0:
            return

        now = datetime.now(timezone.utc)
        expired = []
        for sig_type, ts_str in list(self._activation_timestamps.items()):
            try:
                activated_at = datetime.fromisoformat(ts_str)
                age_hours = (now - activated_at).total_seconds() / 3600
                if age_hours >= self._activation_ttl_hours:
                    expired.append(sig_type)
            except (ValueError, TypeError):
                continue

        for sig_type in expired:
            for name, metrics in self._agent_metrics.items():
                if metrics.config.get("signal_type") == sig_type and metrics.tier in {"nano", "micro", "macro"}:
                    self.promotion.demote(
                        metrics,
                        reason=f"activation TTL expired ({self._activation_ttl_hours}h)",
                    )
                    self.promotion.reactivate(metrics)
                    metrics.samples_tested = 0
                    log.warning("Agent '%s' TTL expired for %s — must re-qualify",
                                name, sig_type)
                    self._emit_meta(
                        "meta_agent_demoted", "high",
                        f"Agent '{name}' TTL expired for {sig_type}",
                        agent=name, related_type=sig_type,
                        reason="ttl_expired",
                    )
                    break
            self._deactivate_agent(sig_type)
            self._activation_timestamps.pop(sig_type, None)

    def _classify_one(self, sig, client):
        text = (
            f"{sig['signal_type']} {sig['severity']}: "
            f"{sig.get('content', {}).get('message', '')} "
            f"namespace={sig.get('namespace', '')}"
        )
        sev = sig.get("severity", "medium")
        model = self._macro_model if sev in ("critical", "high") else self._micro_model
        t0 = time.monotonic()
        r = client.post(
            chat_completions_url(self._llm_url),
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": text},
                ],
                "max_tokens": 5,
                "temperature": 0,
            },
            headers={"Authorization": f"Bearer {self._llm_key}"},
        )
        r.raise_for_status()
        ms = (time.monotonic() - t0) * 1000
        ans = r.json()["choices"][0]["message"]["content"].strip().lower()
        return sig, ans, ms, model, sev

    def _build_evidence_bundle(self, sig: dict) -> dict:
        bundle = {"signal": sig, "related_memories": [], "causal_chain": [], "entity_context": []}
        if not self.memory_archive:
            return bundle
        try:
            from .cascade.recall import RecallEngine
            from .cascade.protocol import Signal
            query_signal = Signal(
                signal_type=sig.get("signal_type", ""),
                severity=sig.get("severity", "info"),
                source="", namespace=sig.get("namespace", ""),
                content=sig.get("content", {}), labels={},
            )
            engine = RecallEngine(self.memory_archive)
            results = engine.recall(query_signal, top_k=5, reinforce=False)
            bundle["related_memories"] = [
                {"signal_type": r.memory.signal.signal_type,
                 "strength": round(r.memory.strength, 3),
                 "message": r.memory.signal.content.get("message", "")[:150],
                 "score": round(r.score, 3)}
                for r in results
            ]
        except Exception as e:
            log.debug("Evidence bundle recall failed: %s", str(e)[:60])
        return bundle

    def _escalate_to_gpu(self, sig: dict, classification: str, client):
        if not self._gpu_url:
            return None
        try:
            bundle = self._build_evidence_bundle(sig)
            memories_text = "\n".join(
                f"- [{m['signal_type']}] (str={m['strength']}) {m['message']}"
                for m in bundle.get("related_memories", [])
            ) or "No related memories found."

            prompt = (
                f"Analyze this infrastructure signal with full context.\n\n"
                f"Signal: {sig['signal_type']} {sig['severity']}\n"
                f"Message: {sig.get('content', {}).get('message', '')}\n"
                f"Namespace: {sig.get('namespace', '')}\n"
                f"CPU classification: {classification}\n\n"
                f"Related memories (what the platform remembers):\n{memories_text}\n\n"
                f"Respond in JSON:\n"
                f'{{"root_cause": "...", "impact": "...", '
                f'"remediation": ["step1", "step2"], "confidence": 0.0-1.0}}'
            )

            t0 = time.monotonic()
            r = client.post(
                chat_completions_url(self._gpu_url),
                json={
                    "model": self._gpu_model,
                    "messages": [
                        {"role": "system", "content": "You are an infrastructure analyst. Analyze signals and provide structured root cause analysis in JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {self._gpu_key}"},
            )
            r.raise_for_status()
            ms = (time.monotonic() - t0) * 1000
            raw = r.json()["choices"][0]["message"]["content"].strip()

            try:
                analysis = json.loads(raw)
            except json.JSONDecodeError:
                analysis = {"raw_response": raw}

            analysis["model"] = self._gpu_model
            analysis["latency_ms"] = round(ms)
            analysis["signal_type"] = sig["signal_type"]
            analysis["severity"] = sig.get("severity", "")
            analysis["namespace"] = sig.get("namespace", "")
            analysis["classification"] = classification
            analysis["timestamp"] = datetime.now(timezone.utc).isoformat()
            analysis["evidence_bundle_size"] = len(bundle.get("related_memories", []))

            self._gpu_analyses.append(analysis)
            if len(self._gpu_analyses) > 500:
                self._gpu_analyses = self._gpu_analyses[-500:]

            if self._gpu_analyses_file:
                try:
                    self._append_gpu_analysis(analysis)
                except Exception as e:
                    log.debug("GPU analysis file write failed: %s", str(e)[:60])

            log.info("GPU analysis: %s → %s (%.0fms, model=%s)",
                     sig["signal_type"], analysis.get("root_cause", "?")[:50], ms, self._gpu_model)

            return analysis
        except Exception as e:
            log.debug("GPU escalation failed: %s", str(e)[:60])
            return None

    _GPU_MAX_LINES = 10000
    _CONTEXT_MAP_MAX = 5000

    def _prune_context_map(self, ctx_map: dict) -> dict:
        if len(ctx_map) <= self._CONTEXT_MAP_MAX:
            return ctx_map
        sorted_keys = sorted(ctx_map, key=ctx_map.get, reverse=True)
        return {k: ctx_map[k] for k in sorted_keys[:self._CONTEXT_MAP_MAX]}

    def _append_gpu_analysis(self, analysis: dict):
        import fcntl
        path = self._gpu_analyses_file
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(analysis) + "\n")
        try:
            with open(path) as f:
                lines = f.readlines()
            if len(lines) > self._GPU_MAX_LINES:
                keep = lines[-self._GPU_MAX_LINES:]
                with open(path, "w") as f:
                    f.writelines(keep)
                log.info("GPU analyses rotated: %d → %d lines", len(lines), len(keep))
        except Exception:
            pass
            fcntl.flock(f, fcntl.LOCK_UN)

    def _run_llm(self, signals):
        try:
            import httpx
            from concurrent.futures import ThreadPoolExecutor, as_completed

            llm_workers = int(os.getenv("CASCADE_LLM_WORKERS", "8"))
            with httpx.Client(timeout=60) as client:
                with ThreadPoolExecutor(max_workers=llm_workers) as pool:
                    futures = {pool.submit(self._classify_one, sig, client): sig for sig in signals}
                    for future in as_completed(futures):
                        orig_sig = futures[future]
                        try:
                            sig, ans, ms, model, sev = future.result()
                            self.stats.llm_classified += 1

                            self._llm_results.append({
                                "signal_type": sig["signal_type"],
                                "classification": ans,
                                "latency_ms": round(ms),
                                "model": model,
                                "tier": "macro" if sev in ("critical", "high") else "micro",
                            })
                            if len(self._llm_results) > 1000:
                                self._llm_results = self._llm_results[-1000:]

                            ctx_key = f"{sig['signal_type']}:{sig.get('namespace', '')}"
                            if _is_noise_classification(ans):
                                self.stats.llm_noise += 1
                                self._llm_noise_counts[sig["signal_type"]] += 1
                                self._llm_context_noise[ctx_key] += 1
                            else:
                                self.stats.llm_important += 1
                                self._llm_important_counts[sig["signal_type"]] += 1
                                self._llm_context_important[ctx_key] += 1

                                if self._gpu_url:
                                    gpu_result = self._escalate_to_gpu(sig, ans, client)
                                    if gpu_result and self.memory_archive:
                                        from .cascade.protocol import Signal as CascadeSignal
                                        mem_sig = CascadeSignal(
                                            signal_type=sig["signal_type"],
                                            severity=sig.get("severity", "info"),
                                            source="", namespace=sig.get("namespace", ""),
                                            content=sig.get("content", {}), labels={},
                                        )
                                        self.memory_archive.store(
                                            mem_sig, classification=ans,
                                            metadata={"analysis": gpu_result},
                                        )

                        except Exception as e:
                            model_key = orig_sig.get("signal_type", "unknown")
                            self._llm_failure_counts[model_key] += 1
                            count = self._llm_failure_counts[model_key]
                            if count <= 5 or count % 100 == 0:
                                log.warning("LLM call failed (%dx): %s", count, str(e)[:80])
        except Exception as e:
            log.warning("LLM error: %s", e)
        finally:
            with self._llm_lock:
                self._llm_active_threads -= 1

    def _queue_shadow_samples(self, cascade_result, cascade_signals):
        """Sample suppressed signals from activated agents for LLM re-check.

        Covers both type-level and contextual suppressions.
        """
        signal_map = {s.signal_id: s for s in cascade_signals}
        candidates = []
        for d in cascade_result.decisions:
            if d.outcome.value not in ("suppress", "drop"):
                continue
            sig = signal_map.get(d.signal_id)
            if not sig:
                continue
            if sig.signal_type in self._activated_types:
                candidates.append(sig)
            elif sig.signal_type in self._activated_contexts:
                ctx = getattr(sig, self._contextual_suppressor._context_key, "")
                if ctx in self._activated_contexts[sig.signal_type]:
                    candidates.append(sig)

        if not candidates or self._shadow_sample_rate <= 0:
            return

        sample_size = max(1, int(len(candidates) * self._shadow_sample_rate))
        sample = random.sample(candidates, min(sample_size, len(candidates)))
        for sig in sample:
            self._shadow_buffer.append({
                "signal_type": sig.signal_type,
                "severity": sig.severity,
                "namespace": sig.namespace,
                "content": sig.content,
            })
        if len(self._shadow_buffer) > self._shadow_max_buffer:
            self._shadow_buffer = self._shadow_buffer[-self._shadow_max_buffer:]

    def _run_shadow_llm(self, signals):
        """Re-classify suppressed signals via independent model.

        Uses the GPU model (different from micro/macro) when available
        to break circular validation. Falls back to macro model.
        """
        self._shadow_running = True
        try:
            import httpx
            shadow_url = self._gpu_url or self._llm_url
            shadow_key = self._gpu_key or self._llm_key
            shadow_model = self._gpu_model if self._gpu_url else self._macro_model
            with httpx.Client(timeout=60) as client:
                for sig in signals:
                    text = (
                        f"{sig['signal_type']} {sig['severity']}: "
                        f"{sig.get('content', {}).get('message', '')} "
                        f"namespace={sig.get('namespace', '')}"
                    )
                    try:
                        r = client.post(
                            chat_completions_url(shadow_url),
                            json={
                                "model": shadow_model,
                                "messages": [
                                    {"role": "system", "content": self._system_prompt},
                                    {"role": "user", "content": text},
                                ],
                                "max_tokens": 5,
                                "temperature": 0,
                            },
                            headers={"Authorization": f"Bearer {shadow_key}"},
                        )
                        r.raise_for_status()
                        ans = r.json()["choices"][0]["message"]["content"].strip().lower()
                        self._shadow_checks += 1

                        if not _is_noise_classification(ans):
                            self._shadow_demotions += 1
                            ns = sig.get("namespace", "")
                            ctx_key = self._contextual_suppressor._context_key

                            if (sig["signal_type"] in self._activated_contexts
                                    and ns in self._activated_contexts.get(sig["signal_type"], set())):
                                self._contextual_suppressor.remove_noise_context(sig["signal_type"], ns)
                                self._activated_contexts[sig["signal_type"]].discard(ns)
                                log.warning(
                                    "SHADOW: contextual FN — %s in %s=%s is important "
                                    "(classified '%s'), context demoted",
                                    sig["signal_type"], ctx_key, ns, ans,
                                )
                            else:
                                self.record_feedback(
                                    sig["signal_type"],
                                    was_suppressed=True,
                                    is_important=True,
                                )
                                log.warning(
                                    "SHADOW: type-level FN — %s is important "
                                    "(classified '%s'), agent demoted",
                                    sig["signal_type"], ans,
                                )
                            self._emit_meta(
                                "meta_shadow_demotion", "critical",
                                f"Shadow caught FN: {sig['signal_type']} "
                                f"in {ctx_key}={ns} classified '{ans}'",
                                related_type=sig["signal_type"],
                                context_value=ns,
                                llm_classification=ans,
                            )

                    except Exception as e:
                        log.debug("Shadow LLM call failed: %s", str(e)[:60])
        except Exception as e:
            log.warning("Shadow LLM error: %s", e)
        finally:
            self._shadow_running = False

    def _write_to_ledger(self, cascade_result, cascade_signals):
        try:
            from .integrations.ledger import write_decisions
            write_decisions(
                self._ledger_url, self._ledger_token,
                cascade_result, cascade_signals, self.domain,
            )
        except Exception as e:
            log.debug("Ledger write failed: %s", str(e)[:60])

    def _flush_promotion_events(self):
        events = self.promotion.drain_events()
        if not events or not self._ledger_url:
            return
        for event in events:
            self._promotion_log.append({
                "timestamp": event.timestamp,
                "event": event.event_type,
                "agent": event.agent_name,
                "from_tier": event.from_tier,
                "to_tier": event.to_tier,
                "reason": event.reason,
            })
            threading.Thread(
                target=self._write_promotion_to_ledger,
                args=(event.to_dict(),),
                daemon=True,
            ).start()

    def _write_promotion_to_ledger(self, event_dict):
        try:
            from .integrations.ledger import write_promotion_event
            write_promotion_event(
                self._ledger_url, self._ledger_token,
                event_dict, self.domain,
            )
        except Exception as e:
            log.debug("Promotion ledger write failed: %s", str(e)[:60])

    def _flush_memory_events(self):
        if not self.memory_archive or not self._ledger_url:
            return
        events = self.memory_archive.drain_events()
        for event in events:
            threading.Thread(
                target=self._write_memory_event_to_ledger,
                args=(event.to_dict(),),
                daemon=True,
            ).start()

    def _write_memory_event_to_ledger(self, event_dict):
        try:
            from .integrations.ledger import write_memory_event
            write_memory_event(
                self._ledger_url, self._ledger_token,
                event_dict, self.domain,
            )
        except Exception as e:
            log.debug("Memory ledger write failed: %s", str(e)[:60])

    def _deactivate_agent(self, signal_type: str):
        pattern_type = self._activated_patterns.pop(signal_type, None)
        self._activated_types.discard(signal_type)
        if pattern_type == "repeat_flood":
            self._repeat_suppressor.remove_signal_type(signal_type)
        elif pattern_type == "dominant_type":
            self._noise_suppressor.remove_noise_type(signal_type)

    def _poll_gcl_verdicts(self):
        """Poll the ledger for GCL FAILS verdicts and trigger demotion."""
        self._verdict_poll_running = True
        try:
            import httpx

            headers = {"Content-Type": "application/json"}
            if self._ledger_token:
                headers["Authorization"] = f"Bearer {self._ledger_token}"

            params = {"entry_type": "audit.verdict", "page_size": "100"}
            if self._verdict_watermark:
                params["from_ts"] = self._verdict_watermark

            with httpx.Client(timeout=15) as client:
                r = client.get(
                    f"{self._ledger_url.rstrip('/')}/api/entries",
                    params=params,
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()

            entries = data if isinstance(data, list) else data.get("entries", [])

            for entry in entries:
                ts = entry.get("timestamp", entry.get("created_at", ""))
                if ts and ts > self._verdict_watermark:
                    self._verdict_watermark = ts

                content = entry.get("content", "")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        continue

                if not isinstance(content, dict):
                    continue

                if content.get("verdict") != "FAILS":
                    continue

                agent_name = content.get("original_agent", "")
                signal_type = content.get("subject_type", "")
                reason = content.get("reason", "GCL audit verdict: FAILS")

                if agent_name in _BUILTIN_AGENT_NAMES:
                    self._gcl_builtin_fails += 1
                    log.debug(
                        "GCL FAILS for built-in agent %s (not counting as FN): %s",
                        agent_name, reason,
                    )
                    continue

                if signal_type:
                    self.record_feedback(
                        signal_type, was_suppressed=True, is_important=True,
                    )
                    log.warning(
                        "GCL FAILS verdict → demotion: agent=%s signal_type=%s reason=%s",
                        agent_name, signal_type, reason,
                    )

            if entries:
                log.info("Polled %d audit verdicts, watermark=%s",
                         len(entries), self._verdict_watermark[:19])

        except Exception as e:
            log.debug("Verdict poll failed: %s", str(e)[:80])
        finally:
            self._verdict_poll_running = False

    def _save_state(self):
        if not self._state_file:
            return
        try:
            state = {
                "version": 3,
                "activated_agents": dict(self._activated_patterns),
                "activated_contexts": {k: sorted(v) for k, v in self._activated_contexts.items()},
                "llm_noise_counts": dict(self._llm_noise_counts),
                "llm_important_counts": dict(self._llm_important_counts),
                "llm_context_noise": dict(self._prune_context_map(self._llm_context_noise)),
                "llm_context_important": dict(self._prune_context_map(self._llm_context_important)),
                "known_patterns": list(self.corpus_analyzer._known_patterns),
                "candidate_agents": [{
                    "name": metrics.name,
                    "tier": metrics.tier,
                    "config": metrics.config,
                    "rule": {
                        "name": rule.name,
                        "signal_types": rule.signal_types,
                        "condition": rule.condition,
                        "classification": rule.classification,
                    },
                } for name, metrics in self._agent_metrics.items()
                  if (rule := self._agent_rules.get(name)) is not None],
                "verdict_watermark": self._verdict_watermark,
                "gcl_builtin_fails": self._gcl_builtin_fails,
                "activation_timestamps": dict(self._activation_timestamps),
                "stats_snapshot": {
                    "signals_processed": self.stats.signals_processed,
                    "llm_classified": self.stats.llm_classified,
                },
                "memory_archive": self.memory_archive.to_dict() if self.memory_archive else None,
            }
            with open(self._state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            log.warning("Failed to save state: %s", e)

    def _restore_state(self):
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file) as f:
                state = json.load(f)
            self._llm_noise_counts = defaultdict(int, state.get("llm_noise_counts", {}))
            self._llm_important_counts = defaultdict(
                int, state.get("llm_important_counts", {})
            )

            self._verdict_watermark = state.get("verdict_watermark", "")
            self._gcl_builtin_fails = state.get("gcl_builtin_fails", 0)
            self._activation_timestamps = state.get("activation_timestamps", {})
            if state.get("version") in (2, 3):
                self.corpus_analyzer._known_patterns = set(state.get("known_patterns", []))
                for candidate in state.get("candidate_agents", []):
                    metrics = AgentMetrics(
                        name=candidate["name"],
                        tier=candidate.get("tier", "draft"),
                        config=candidate.get("config", {}),
                    )
                    self._agent_metrics[metrics.name] = metrics
                    self._agent_rules[metrics.name] = RuleAgent(candidate["rule"])

                self._activated_patterns = dict(state.get("activated_agents", {}))
                self._activated_types = set(self._activated_patterns)
                for sig_type, pattern_type in self._activated_patterns.items():
                    if pattern_type == "repeat_flood":
                        self._repeat_suppressor.add_signal_type(sig_type)
                    elif pattern_type == "dominant_type":
                        self._noise_suppressor.add_noise_type(sig_type)

                self._llm_context_noise = defaultdict(int, state.get("llm_context_noise", {}))
                self._llm_context_important = defaultdict(int, state.get("llm_context_important", {}))
                for sig_type, contexts in state.get("activated_contexts", {}).items():
                    for ctx in contexts:
                        self._contextual_suppressor.add_noise_context(sig_type, ctx)
                        self._activated_contexts[sig_type].add(ctx)
            else:
                # Legacy state did not preserve the suppressor kind. Revalidate it
                # instead of restoring a type with broader suppression semantics.
                self.corpus_analyzer._known_patterns = set()
                log.warning("Legacy cascade state requires safe rediscovery")
            memory_data = state.get("memory_archive")
            if memory_data and self.memory_archive is not None:
                self.memory_archive = MemoryArchive.from_dict(memory_data)

            self._promote_orphaned_noise_types()

            log.info("Restored: %d activated, %d LLM counts, %d patterns",
                     len(self._activated_types), len(self._llm_noise_counts),
                     len(self.corpus_analyzer._known_patterns))
        except Exception as e:
            log.warning("Failed to restore state: %s", e)

    def _promote_orphaned_noise_types(self):
        """Activate noise types with strong historical LLM evidence on restore.

        Uses a 2% important-rate threshold (vs zero-tolerance for live promotion)
        because historical data accumulates borderline classifications over time.
        A type with 40K noise and 369 important (0.9%) is safe to suppress.
        """
        for sig_type, noise_count in self._llm_noise_counts.items():
            if sig_type in self._activated_types:
                continue
            important = self._llm_important_counts.get(sig_type, 0)
            total = noise_count + important
            important_rate = important / total if total > 0 else 1.0
            if total < _PROMOTION_MIN_SAMPLES or important_rate > 0.02:
                continue
            self._noise_suppressor.add_noise_type(sig_type)
            self._activated_types.add(sig_type)
            self._activated_patterns[sig_type] = "dominant_type"
            self._activation_timestamps[sig_type] = datetime.now(timezone.utc).isoformat()
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "activated",
                "agent": f"restored_{sig_type}",
                "tier": "nano",
                "status": "validated_from_history",
                "samples": total,
                "important": important,
                "important_rate": round(important_rate, 4),
            })
            log.info("RESTORED ACTIVATION: %s (%d noise, %d important, %.1f%% rate)",
                     sig_type, noise_count, important, important_rate * 100)

    def get_stats(self) -> Dict:
        stats = asdict(self.stats)
        measured = self._fn_evaluated > 0
        stats["fn_count"] = self._fn_count if measured else None
        stats["fn_rate"] = (
            round(self._fn_count / self._fn_evaluated * 100, 3)
            if measured else None
        )
        stats["fn_evaluated"] = self._fn_evaluated
        stats["fn_status"] = "measured" if measured else "not_measured"
        stats["fn_types"] = dict(sorted(self._fn_types.items(), key=lambda x: -x[1])[:10])
        stats["shadow_checks"] = self._shadow_checks
        stats["shadow_demotions"] = self._shadow_demotions
        stats["shadow_sample_rate"] = self._shadow_sample_rate
        stats["activation_ttl_hours"] = self._activation_ttl_hours
        stats["gcl_builtin_fails"] = self._gcl_builtin_fails
        return stats

    # -- Meta-cascade: self-monitoring -----------------------------------------

    def _emit_meta(self, signal_type: str, severity: str, message: str,
                   **kwargs) -> None:
        """Append a meta signal describing an internal cascade event."""
        meta = {
            "signal_type": signal_type,
            "severity": severity,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "domain": self.domain,
            **kwargs,
        }
        self._meta_signals.append(meta)
        if len(self._meta_signals) > self._meta_max_buffer:
            self._meta_signals = self._meta_signals[-self._meta_max_buffer:]
        log.debug("META: %s [%s] %s", signal_type, severity, message)
        if self._meta_target:
            threading.Thread(
                target=self._post_meta_signal, args=(meta,), daemon=True,
            ).start()

    def _post_meta_signal(self, meta: dict) -> None:
        """POST a meta signal to an external meta-cascade instance."""
        try:
            import httpx
            with httpx.Client(timeout=10) as client:
                client.post(
                    self._meta_target.rstrip("/") + "/cascade",
                    json={"signals": [{
                        "signal_type": meta["signal_type"],
                        "severity": meta["severity"],
                        "content": {"message": meta["message"], **{
                            k: v for k, v in meta.items()
                            if k not in ("signal_type", "severity", "message",
                                         "timestamp", "domain")
                        }},
                    }]},
                )
        except Exception as e:
            log.debug("Meta POST failed: %s", str(e)[:60])

    def get_meta_signals(self, limit: int = 50) -> list:
        """Return the most recent meta signals."""
        return self._meta_signals[-limit:]

    def check_consolidation_meta(self, result: dict) -> None:
        """Emit meta signal if consolidation evicted a large fraction."""
        processed = result.get("processed", 0)
        evicted = result.get("evicted", 0)
        if processed > 0 and evicted / processed > 0.10:
            self._emit_meta(
                "meta_consolidation_eviction_spike", "high",
                f"Consolidation evicted {evicted}/{processed} memories "
                f"({evicted / processed:.0%})",
                evicted=evicted,
                processed=processed,
                eviction_rate=round(evicted / processed, 3),
            )

    def record_feedback(
        self, signal_type: str, *, was_suppressed: bool, is_important: bool
    ) -> None:
        """Record externally verified ground truth for FN telemetry.

        If a suppressed signal was important (false negative) and the
        responsible agent is activated, that agent is instantly demoted.
        """
        self._fn_evaluated += 1
        if was_suppressed and is_important:
            self._fn_count += 1
            self._fn_types[signal_type] += 1
            self._emit_meta(
                "meta_fn_detected", "critical",
                f"False negative confirmed: {signal_type} was suppressed but important",
                related_type=signal_type,
                fn_total=self._fn_count,
                fn_evaluated=self._fn_evaluated,
            )

            if signal_type in self._activated_types:
                for name, metrics in self._agent_metrics.items():
                    sig = metrics.config.get("signal_type", "")
                    if sig == signal_type and metrics.tier in {"nano", "micro", "macro"}:
                        metrics.last_batch_fn_count = 1
                        self.promotion.demote(
                            metrics,
                            reason=f"FN confirmed by external feedback: {signal_type}",
                        )
                        self._deactivate_agent(signal_type)
                        self._flush_promotion_events()
                        log.warning("Agent '%s' demoted via feedback: %s was important",
                                    name, signal_type)
                        self._emit_meta(
                            "meta_agent_demoted", "high",
                            f"Agent '{name}' demoted: FN confirmed for {signal_type}",
                            agent=name, related_type=signal_type,
                            reason="fn_confirmed",
                        )
                        break

    def get_llm_results(self, limit: int = 20) -> list:
        return self._llm_results[-limit:]

    def get_llm_summary(self) -> Dict:
        top_noise = sorted(self._llm_noise_counts.items(), key=lambda x: -x[1])[:10]
        return {
            "classified": self.stats.llm_classified,
            "noise": self.stats.llm_noise,
            "important": self.stats.llm_important,
            "noise_pct": round(self.stats.llm_noise / max(1, self.stats.llm_classified) * 100, 1),
            "top_noise_types": [{
                "signal_type": k,
                "noise_count": v,
                "activated": k in self._activated_types,
            } for k, v in top_noise],
            "activated_types": list(self._activated_types),
            "activated_contexts": {k: sorted(v) for k, v in self._activated_contexts.items()},
            "contextual_suppressor_count": sum(len(v) for v in self._activated_contexts.values()),
            "buffer_size": len(self._llm_buffer),
            "running": self._llm_active_threads > 0,
        }

    def get_gpu_analyses(self, limit: int = 20) -> list:
        return self._gpu_analyses[-limit:]

    def get_promotion_log(self, limit: int = 30) -> list:
        return self._promotion_log[-limit:]

    def get_discovered_agents(self) -> list:
        if hasattr(self, "corpus_analyzer"):
            return self.corpus_analyzer.get_proposed_agents()
        return []
