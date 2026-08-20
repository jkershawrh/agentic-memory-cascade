"""Pressure-aware inference scaler.

Monitors Linux PSI (Pressure Stall Information) metrics and cgroup v2
memory to dynamically load-shed or restore models on a CPU inference
node.  On non-Linux platforms the observer returns zero-pressure
snapshots so the scaler can be exercised in tests and development.
"""

from __future__ import annotations

import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional, Set

import yaml
from pydantic import BaseModel, Field

from ..resources import resource_path
from ..routing.corpora import CORPORA_TO_ENDPOINT, RoutingCorpora

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

Grade = Literal["green", "yellow", "red"]
Action = Literal["restore", "hold", "shed", "shed_aggressive"]


class PressureSnapshot(BaseModel):
    """Point-in-time reading of system pressure metrics."""

    timestamp: str = ""
    cpu_some_pct: float = 0.0
    cpu_full_pct: float = 0.0
    memory_some_pct: float = 0.0
    memory_full_pct: float = 0.0
    io_some_pct: float = 0.0
    memory_used_gb: float = 0.0
    memory_limit_gb: float = 0.0
    cpu_cores_available: int = 0


class ModelFootprint(BaseModel):
    """Resource footprint for a single model."""

    model: str
    params_b: float
    dtype: str
    serving_layer: str
    memory_gb: float
    cpu_cores_estimate: int


class ModelBudget(BaseModel):
    """Remaining resource budget after accounting for loaded models."""

    memory_gb: float = 0.0
    cpu_cores: int = 0
    pressure_level: str = "normal"


class PressureScorecard(BaseModel):
    """Rubric grades for every monitored metric plus overall verdict."""

    cpu_some_grade: Grade = "green"
    cpu_full_grade: Grade = "green"
    memory_some_grade: Grade = "green"
    memory_used_grade: Grade = "green"
    io_some_grade: Grade = "green"
    inference_budget_grade: Grade = "green"
    models_available_grade: Grade = "green"
    overall_grade: Grade = "green"
    action: Action = "restore"


class ScalerState(BaseModel):
    """Full snapshot of the scaler's internal state."""

    pressure: PressureSnapshot = Field(default_factory=PressureSnapshot)
    scorecard: PressureScorecard = Field(default_factory=PressureScorecard)
    budget: ModelBudget = Field(default_factory=ModelBudget)
    loaded_models: List[str] = Field(default_factory=list)
    evicted_models: List[str] = Field(default_factory=list)
    available_models: Set[str] = Field(default_factory=set)
    eviction_order: List[str] = Field(default_factory=list)
    last_action: str = "init"


class PressureThresholds(BaseModel):
    """Green/yellow thresholds for each rubric metric plus hysteresis."""

    cpu_some_green: float = 10.0
    cpu_some_yellow: float = 25.0
    cpu_full_green: float = 5.0
    cpu_full_yellow: float = 15.0
    memory_some_green: float = 10.0
    memory_some_yellow: float = 20.0
    memory_used_green: float = 70.0
    memory_used_yellow: float = 85.0
    io_some_green: float = 10.0
    io_some_yellow: float = 25.0
    inference_budget_green: float = 50.0
    inference_budget_yellow: float = 25.0
    models_available_green: float = 80.0
    models_available_yellow: float = 50.0
    min_shed_interval_s: float = 30.0
    min_restore_interval_s: float = 120.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BYTES_PER_PARAM = {
    "bfloat16": 2.0,
    "int8": 1.0,
    "Q4_K_M": 0.5625,
    "Q8_0": 1.0,
    "i2_s": 0.25,
}

_KV_CACHE_OVERHEAD = 1.3  # 30 %


def estimate_memory_gb(params_b: float, dtype: str) -> float:
    """Estimate model memory from parameter count and data type.

    Uses per-dtype bytes-per-parameter and adds 30 % overhead for KV
    cache and runtime buffers.
    """
    bpp = _BYTES_PER_PARAM.get(dtype, 2.0)
    return params_b * bpp * _KV_CACHE_OVERHEAD


def build_model_roster(corpora: RoutingCorpora) -> list[ModelFootprint]:
    """Build :class:`ModelFootprint` entries from a compiled corpora.

    Scans every entry (primary, fallback, alternatives) and also
    cross-references :data:`CORPORA_TO_ENDPOINT` for completeness.
    """
    seen: dict[str, ModelFootprint] = {}

    for industry_entries in corpora.entries.values():
        for task_entries in industry_entries.values():
            for entry in task_entries.values():
                configs = [entry.config]
                if entry.fallback:
                    configs.append(entry.fallback)
                configs.extend(entry.alternatives)
                for cfg in configs:
                    if cfg.model not in seen:
                        mem = estimate_memory_gb(cfg.params, cfg.dtype)
                        seen[cfg.model] = ModelFootprint(
                            model=cfg.model,
                            params_b=cfg.params,
                            dtype=cfg.dtype,
                            serving_layer=cfg.serving_layer,
                            memory_gb=mem,
                            cpu_cores_estimate=max(1, round(cfg.params * 2)),
                        )

    # Include roster entries not found in the entries tree (placeholder)
    for name in corpora.model_roster:
        if name not in seen and name in CORPORA_TO_ENDPOINT:
            seen[name] = ModelFootprint(
                model=name,
                params_b=0.0,
                dtype="bfloat16",
                serving_layer="ovms",
                memory_gb=0.0,
                cpu_cores_estimate=1,
            )

    return list(seen.values())


# ---------------------------------------------------------------------------
# YAML config loader
# ---------------------------------------------------------------------------


def _load_thresholds_from_yaml() -> PressureThresholds:
    """Load thresholds from ``config/scaler.yaml`` next to the package."""
    try:
        config_path = resource_path("config", "scaler.yaml")
    except FileNotFoundError:
        return PressureThresholds()

    with open(config_path) as f:
        data = yaml.safe_load(f)

    rubric = data.get("rubric", {})
    hysteresis = data.get("hysteresis", {})

    return PressureThresholds(
        cpu_some_green=rubric.get("cpu_some_pct", {}).get("green", 10.0),
        cpu_some_yellow=rubric.get("cpu_some_pct", {}).get("yellow", 25.0),
        cpu_full_green=rubric.get("cpu_full_pct", {}).get("green", 5.0),
        cpu_full_yellow=rubric.get("cpu_full_pct", {}).get("yellow", 15.0),
        memory_some_green=rubric.get("memory_some_pct", {}).get("green", 10.0),
        memory_some_yellow=rubric.get("memory_some_pct", {}).get("yellow", 20.0),
        memory_used_green=rubric.get("memory_used_pct", {}).get("green", 70.0),
        memory_used_yellow=rubric.get("memory_used_pct", {}).get("yellow", 85.0),
        io_some_green=rubric.get("io_some_pct", {}).get("green", 10.0),
        io_some_yellow=rubric.get("io_some_pct", {}).get("yellow", 25.0),
        inference_budget_green=rubric.get("inference_budget_pct", {}).get("green", 50.0),
        inference_budget_yellow=rubric.get("inference_budget_pct", {}).get("yellow", 25.0),
        models_available_green=rubric.get("models_available_pct", {}).get("green", 80.0),
        models_available_yellow=rubric.get("models_available_pct", {}).get("yellow", 50.0),
        min_shed_interval_s=hysteresis.get("min_shed_interval_s", 30.0),
        min_restore_interval_s=hysteresis.get("min_restore_interval_s", 120.0),
    )


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

_GRADE_RANK = {"green": 0, "yellow": 1, "red": 2}


def _grade_lower(value: float, green: float, yellow: float) -> Grade:
    """Grade a *lower-is-better* metric."""
    if value <= green:
        return "green"
    if value <= yellow:
        return "yellow"
    return "red"


def _grade_higher(value: float, green: float, yellow: float) -> Grade:
    """Grade a *higher-is-better* metric."""
    if value >= green:
        return "green"
    if value >= yellow:
        return "yellow"
    return "red"


def _worst_grade(*grades: Grade) -> Grade:
    return max(grades, key=lambda g: _GRADE_RANK.get(g, 2))


# ---------------------------------------------------------------------------
# PressureObserver
# ---------------------------------------------------------------------------

_PSI_AVG10 = re.compile(r"avg10=(\d+\.\d+)")


class PressureObserver:
    """Reads Linux PSI and cgroup v2 memory stats.

    On non-Linux platforms every reading returns a zero-pressure snapshot
    so the rest of the stack can be exercised without mocking.
    """

    def read(self) -> PressureSnapshot:
        """Return a current :class:`PressureSnapshot`."""
        ts = datetime.now(timezone.utc).isoformat()

        if platform.system() != "Linux":
            import os

            return PressureSnapshot(
                timestamp=ts,
                cpu_cores_available=os.cpu_count() or 1,
            )

        cpu = self._read_proc_pressure("/proc/pressure/cpu")
        mem = self._read_proc_pressure("/proc/pressure/memory")
        io = self._read_proc_pressure("/proc/pressure/io")
        cg_used, cg_limit = self._read_cgroup_memory()

        import os

        return PressureSnapshot(
            timestamp=ts,
            cpu_some_pct=cpu.get("some", 0.0),
            cpu_full_pct=cpu.get("full", 0.0),
            memory_some_pct=mem.get("some", 0.0),
            memory_full_pct=mem.get("full", 0.0),
            io_some_pct=io.get("some", 0.0),
            memory_used_gb=cg_used,
            memory_limit_gb=cg_limit,
            cpu_cores_available=os.cpu_count() or 1,
        )

    # -- internal ------------------------------------------------------------

    @staticmethod
    def _read_proc_pressure(path: str) -> dict[str, float]:
        """Parse a ``/proc/pressure/*`` file and return avg10 values."""
        result: dict[str, float] = {}
        try:
            text = Path(path).read_text()
        except (FileNotFoundError, PermissionError):
            return result

        for line in text.strip().splitlines():
            kind = line.split()[0]  # "some" or "full"
            match = _PSI_AVG10.search(line)
            if match:
                result[kind] = float(match.group(1))
        return result

    @staticmethod
    def _read_cgroup_memory() -> tuple[float, float]:
        """Read cgroup v2 memory usage and limit in GB."""
        used_gb = 0.0
        limit_gb = 0.0
        try:
            raw = Path("/sys/fs/cgroup/memory.current").read_text().strip()
            used_gb = int(raw) / (1024 ** 3)
        except (FileNotFoundError, PermissionError, ValueError):
            pass
        try:
            raw = Path("/sys/fs/cgroup/memory.max").read_text().strip()
            if raw != "max":
                limit_gb = int(raw) / (1024 ** 3)
        except (FileNotFoundError, PermissionError, ValueError):
            pass
        return used_gb, limit_gb


# ---------------------------------------------------------------------------
# InferenceScaler
# ---------------------------------------------------------------------------


class InferenceScaler:
    """Pressure-driven model load-shedding and restoration engine.

    Models are evicted largest-first when pressure rises above rubric
    thresholds, and restored smallest-first when pressure returns to
    green.  Hysteresis intervals prevent flapping.
    """

    def __init__(
        self,
        model_roster: list[ModelFootprint],
        total_memory_gb: float = 64.0,
        total_cpu_cores: int = 32,
        thresholds: Optional[PressureThresholds] = None,
    ) -> None:
        self._roster = {fp.model: fp for fp in model_roster}
        self._total_memory_gb = total_memory_gb
        self._total_cpu_cores = total_cpu_cores
        self._thresholds = thresholds or _load_thresholds_from_yaml()

        # Eviction order: largest memory first
        self._eviction_order = [
            fp.model
            for fp in sorted(model_roster, key=lambda fp: fp.memory_gb, reverse=True)
        ]

        # Start with every model loaded
        self._loaded: list[str] = list(self._eviction_order)
        self._evicted: list[str] = []

        # State
        self._pressure = PressureSnapshot()
        self._scorecard = PressureScorecard()
        self._budget = self._compute_budget()
        self._last_action = "init"

        # Hysteresis timestamps
        self._last_shed_time: float = 0.0
        self._last_restore_time: float = 0.0

    # -- public API ----------------------------------------------------------

    def observe_pressure(self, snapshot: PressureSnapshot) -> ScalerState:
        """Grade *snapshot*, execute any eviction/restoration, return state."""
        self._pressure = snapshot
        self._scorecard = self._grade_snapshot(snapshot)

        now = time.time()
        action = self._scorecard.action

        if action == "restore":
            if now - self._last_restore_time >= self._thresholds.min_restore_interval_s:
                restored = self._restore_next()
                if restored:
                    self._last_restore_time = now
                    self._last_action = f"restored:{restored}"
                else:
                    self._last_action = "restore:noop"
            else:
                self._last_action = "restore:hysteresis"
        elif action in ("shed", "shed_aggressive"):
            if now - self._last_shed_time >= self._thresholds.min_shed_interval_s:
                evicted = self._evict_next()
                if evicted:
                    self._last_shed_time = now
                    self._last_action = f"evicted:{evicted}"
                else:
                    self._last_action = "shed:noop"
            else:
                self._last_action = "shed:hysteresis"
        else:
            self._last_action = "hold"

        self._budget = self._compute_budget()
        return self.current_state()

    def current_state(self) -> ScalerState:
        """Return the current :class:`ScalerState`."""
        return ScalerState(
            pressure=self._pressure,
            scorecard=self._scorecard,
            budget=self._budget,
            loaded_models=list(self._loaded),
            evicted_models=list(self._evicted),
            available_models=set(self._loaded),
            eviction_order=list(self._eviction_order),
            last_action=self._last_action,
        )

    def available_models(self) -> set[str]:
        """Return the set of currently-loaded model names."""
        return set(self._loaded)

    def force_evict(self, model: str) -> ScalerState:
        """Force-evict a specific model regardless of pressure."""
        if model in self._loaded:
            self._loaded.remove(model)
            self._evicted.append(model)
            self._last_action = f"force_evicted:{model}"
            self._budget = self._compute_budget()
        return self.current_state()

    def force_restore(self, model: str) -> ScalerState:
        """Force-restore a specific model regardless of pressure."""
        if model in self._evicted:
            self._evicted.remove(model)
            self._loaded.append(model)
            self._last_action = f"force_restored:{model}"
            self._budget = self._compute_budget()
        return self.current_state()

    # -- grading -------------------------------------------------------------

    def _grade_snapshot(self, snapshot: PressureSnapshot) -> PressureScorecard:
        """Apply rubric thresholds to produce a :class:`PressureScorecard`."""
        t = self._thresholds

        cpu_some = _grade_lower(snapshot.cpu_some_pct, t.cpu_some_green, t.cpu_some_yellow)
        cpu_full = _grade_lower(snapshot.cpu_full_pct, t.cpu_full_green, t.cpu_full_yellow)
        mem_some = _grade_lower(snapshot.memory_some_pct, t.memory_some_green, t.memory_some_yellow)
        io_some = _grade_lower(snapshot.io_some_pct, t.io_some_green, t.io_some_yellow)

        # Memory-used percentage: prefer cgroup limit, fall back to node total
        limit = snapshot.memory_limit_gb if snapshot.memory_limit_gb > 0 else self._total_memory_gb
        mem_used_pct = (snapshot.memory_used_gb / limit * 100) if limit > 0 else 0.0
        mem_used = _grade_lower(mem_used_pct, t.memory_used_green, t.memory_used_yellow)

        # Derived metrics
        budget = self._compute_budget()
        inference_budget_pct = (budget.memory_gb / self._total_memory_gb * 100) if self._total_memory_gb > 0 else 0.0
        total_models = len(self._roster)
        models_available_pct = (len(self._loaded) / total_models * 100) if total_models > 0 else 0.0

        inf_budget = _grade_higher(inference_budget_pct, t.inference_budget_green, t.inference_budget_yellow)
        models_avail = _grade_higher(models_available_pct, t.models_available_green, t.models_available_yellow)

        # Availability is an outcome of scaling, not resource pressure. Including
        # it here creates a positive-feedback loop where an eviction requests
        # another eviction and prevents restoration.
        pressure_grades = [cpu_some, cpu_full, mem_some, mem_used, io_some, inf_budget]
        overall = _worst_grade(*pressure_grades)

        # Determine action
        reds = sum(1 for g in pressure_grades if g == "red")
        yellows = sum(1 for g in pressure_grades if g == "yellow")

        if reds >= 2:
            action: Action = "shed_aggressive"
        elif reds >= 1:
            action = "shed"
        elif yellows >= 1:
            action = "hold"
        else:
            action = "restore"

        return PressureScorecard(
            cpu_some_grade=cpu_some,
            cpu_full_grade=cpu_full,
            memory_some_grade=mem_some,
            memory_used_grade=mem_used,
            io_some_grade=io_some,
            inference_budget_grade=inf_budget,
            models_available_grade=models_avail,
            overall_grade=overall,
            action=action,
        )

    # -- eviction / restoration ----------------------------------------------

    def _evict_next(self) -> Optional[str]:
        """Evict the largest still-loaded model.  Returns its name or ``None``."""
        for model in self._eviction_order:
            if model in self._loaded:
                self._loaded.remove(model)
                self._evicted.append(model)
                return model
        return None

    def _restore_next(self) -> Optional[str]:
        """Restore the most-recently evicted (smallest) model.  Returns its name or ``None``."""
        if not self._evicted:
            return None
        model = self._evicted.pop()  # last evicted = smallest
        self._loaded.append(model)
        return model

    # -- budget accounting ---------------------------------------------------

    def _compute_budget(self) -> ModelBudget:
        loaded_mem = sum(self._roster[m].memory_gb for m in self._loaded if m in self._roster)
        loaded_cpu = sum(self._roster[m].cpu_cores_estimate for m in self._loaded if m in self._roster)
        pressure_level = self._scorecard.overall_grade if self._scorecard else "normal"
        # Map grade to pressure level name
        level_map = {"green": "normal", "yellow": "elevated", "red": "critical"}
        return ModelBudget(
            memory_gb=self._total_memory_gb - loaded_mem,
            cpu_cores=self._total_cpu_cores - loaded_cpu,
            pressure_level=level_map.get(pressure_level, "normal"),
        )


# ---------------------------------------------------------------------------
# ModelLifecycleManager
# ---------------------------------------------------------------------------


class ModelLifecycleManager:
    """Executes scaler decisions by scaling model deployments."""

    def __init__(self, namespace: str = "triforce", dry_run: bool = True):
        self.namespace = namespace
        self.dry_run = dry_run
        self._last_actions: list[str] = []

    def evict_model(self, model: str) -> bool:
        """Scale deployment to 0 replicas."""
        return self.scale_replicas(model, 0)

    def restore_model(self, model: str, replicas: int = 1) -> bool:
        """Scale deployment back up."""
        return self.scale_replicas(model, replicas)

    def scale_replicas(self, model: str, replicas: int) -> bool:
        """Adjust replica count."""
        deployment = self._model_to_deployment(model)
        action = f"scale {deployment} --replicas={replicas} -n {self.namespace}"
        self._last_actions.append(action)

        if self.dry_run:
            return True

        import subprocess

        try:
            subprocess.run(
                ["oc", "scale", "deployment", deployment,
                 f"--replicas={replicas}", "-n", self.namespace],
                check=True, capture_output=True, text=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @staticmethod
    def _model_to_deployment(model: str) -> str:
        """Map model alias to k8s deployment name.

        Covers all 19 models in the roster.
        """
        mapping = {
            "granite-350m":                  "ovms-granite-350m",
            "granite-4-0-h-tiny-cpu":        "ovms-granite-tiny",
            "granite-2b-cpu":                "ovms-granite-2b",
            "qwen25-3b-cpu":                 "ovms-qwen25-3b",
            "granite-4.1-3b":                "ovms-granite-41-3b",
            "phi3-mini-cpu":                 "ovms-phi3-mini",
            "granite-3-2-8b-instruct-cpu":   "ovms-granite-8b",
            "granite-4.1-8b":                "ovms-granite-41-8b",
            "granite-2b-int8":               "ovms-granite-2b-int8",
            "granite-2b-cpu-speculative":    "ovms-granite-2b-speculative",
            "bitnet-2b":                     "llama-bitnet-2b",
            "smollm2-360m":                  "llama-smollm2-360m",
            "smollm2-1.7b":                  "llama-smollm2-17b",
            "phi4-mini":                     "llama-phi4-mini",
            "gemma2-2b":                     "llama-gemma2-2b",
            "qwen36-moe-35b-a3b":           "llama-qwen36-moe",
            "granite-2b-q4":                 "llama-granite-2b-q4",
            "granite-2b-q8":                 "llama-granite-2b-q8",
            "granite-8b-q4":                 "llama-granite-8b-q4",
        }
        return mapping.get(model, f"ovms-{model}")
