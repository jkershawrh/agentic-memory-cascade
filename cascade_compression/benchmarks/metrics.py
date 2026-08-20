"""Metric collection and aggregation for benchmark results."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class SampleResult:
    """Single inference sample measurement."""
    model: str
    task_id: str
    industry: str
    lever: str
    concurrency: int = 1
    latency_ms: int = 0
    ttft_ms: int = 0
    output_tokens: int = 0
    prompt_tokens: int = 0
    output_text: str = ""
    quality: str = "unknown"  # correct / incorrect / unknown
    is_cold: bool = False
    error: str | None = None


@dataclass
class AggregatedMetrics:
    """Aggregated metrics for a model × task × lever combination."""
    model: str
    task_id: str
    industry: str
    lever: str
    concurrency: int = 1
    samples: int = 0

    # Latency (ms)
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_min_ms: float = 0.0
    latency_max_ms: float = 0.0

    # Time to first token (ms)
    ttft_p50_ms: float = 0.0
    ttft_p95_ms: float = 0.0

    # Throughput
    throughput_tok_s: float = 0.0
    output_tokens_median: float = 0.0

    # Quality
    quality_accuracy: float = 0.0
    quality_counts: dict = field(default_factory=dict)

    # Cold start
    cold_start_ms: float = 0.0
    warm_steady_ms: float = 0.0
    warm_cache_speedup: float = 0.0

    # Variance
    coefficient_of_variation: float = 0.0
    variance_flagged: bool = False


def percentile(data: list[float], pct: float) -> float:
    """Calculate percentile from a sorted list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (pct / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def aggregate(samples: list[SampleResult], variance_threshold: float = 0.20) -> AggregatedMetrics:
    """Aggregate individual samples into summary metrics."""
    if not samples:
        return AggregatedMetrics(model="", task_id="", industry="", lever="")

    first = samples[0]
    valid = [s for s in samples if s.error is None]
    cold = [s for s in valid if s.is_cold]
    warm = [s for s in valid if not s.is_cold]

    latencies = [s.latency_ms for s in warm] if warm else [s.latency_ms for s in valid]
    ttfts = [s.ttft_ms for s in warm if s.ttft_ms > 0]

    # Throughput: total output tokens / total wall-clock seconds
    total_tokens = sum(s.output_tokens for s in warm)
    total_seconds = sum(s.latency_ms for s in warm) / 1000.0 if warm else 0
    tok_s = total_tokens / total_seconds if total_seconds > 0 else 0

    # Quality
    quality_counts = {}
    for s in valid:
        quality_counts[s.quality] = quality_counts.get(s.quality, 0) + 1
    correct = quality_counts.get("correct", 0)
    total_quality = correct + quality_counts.get("incorrect", 0)
    accuracy = correct / total_quality if total_quality > 0 else 0.0

    # Cold start
    cold_latency = statistics.median([s.latency_ms for s in cold]) if cold else 0
    warm_latency = statistics.median(latencies) if latencies else 0
    cache_speedup = cold_latency / warm_latency if warm_latency > 0 and cold_latency > 0 else 0

    # Variance
    cov = 0.0
    if len(latencies) >= 2:
        mean = statistics.mean(latencies)
        if mean > 0:
            cov = statistics.stdev(latencies) / mean

    return AggregatedMetrics(
        model=first.model,
        task_id=first.task_id,
        industry=first.industry,
        lever=first.lever,
        concurrency=first.concurrency,
        samples=len(valid),
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        latency_p99_ms=percentile(latencies, 99),
        latency_min_ms=min(latencies) if latencies else 0,
        latency_max_ms=max(latencies) if latencies else 0,
        ttft_p50_ms=percentile(ttfts, 50) if ttfts else 0,
        ttft_p95_ms=percentile(ttfts, 95) if ttfts else 0,
        throughput_tok_s=round(tok_s, 2),
        output_tokens_median=statistics.median([s.output_tokens for s in warm]) if warm else 0,
        quality_accuracy=round(accuracy, 4),
        quality_counts=quality_counts,
        cold_start_ms=cold_latency,
        warm_steady_ms=warm_latency,
        warm_cache_speedup=round(cache_speedup, 2),
        coefficient_of_variation=round(cov, 4),
        variance_flagged=cov > variance_threshold,
    )
