"""Cascade Compression — CPU Inference Routing Engine.

Three-tier cascade (nano/micro/macro) with benchmark-graded model selection,
workload classification, and strategy routing for Intel Xeon CPU inference.
"""

from .routing.bootstrapper import (
    ClassificationScorecard,
    WorkloadBootstrapper,
    WorkloadFingerprint,
    WorkloadProfile,
)
from .routing.corpora import (
    CORPORA_TO_ENDPOINT,
    LANE_LATENCY_TARGETS,
    LANE_MODELS,
    LANE_RESPONSE_FORMAT,
    TASK_LATENCY_THRESHOLDS,
    TASK_TO_LANE,
    CorporaEntry,
    ModelConfig,
    RoutingCorpora,
    RubricScorecard,
    grade_task_latency,
    load_corpora,
    reload_corpora,
    resolve_lane,
    resolve_lane_model,
    resolve_lane_response_format,
)
from .infra.fleet_manager import FleetManager, FleetPlan, FleetScorecard, ModelAllocation
from .routing.models import RoutingDecision
from .infra.scaler import (
    InferenceScaler,
    ModelBudget,
    ModelFootprint,
    ModelLifecycleManager,
    PressureObserver,
    PressureScorecard,
    PressureSnapshot,
    ScalerState,
    estimate_memory_gb,
)
from .routing.strategy_router import DEFAULT_STRATEGY, InferenceStrategy, StrategyRouter
from .routing.task_mapping import (
    DEEPFIELD_TASK_TO_BENCHMARK_SHAPE,
    resolve_benchmark_task,
)

__all__ = [
    "RoutingCorpora", "CorporaEntry", "ModelConfig", "RubricScorecard",
    "CORPORA_TO_ENDPOINT", "TASK_LATENCY_THRESHOLDS", "LANE_RESPONSE_FORMAT",
    "grade_task_latency", "load_corpora", "reload_corpora", "resolve_lane_response_format",
    "InferenceStrategy", "StrategyRouter", "DEFAULT_STRATEGY",
    "WorkloadBootstrapper", "WorkloadProfile", "WorkloadFingerprint", "ClassificationScorecard",
    "RoutingDecision",
    "DEEPFIELD_TASK_TO_BENCHMARK_SHAPE", "resolve_benchmark_task",
    "InferenceScaler", "ScalerState", "PressureSnapshot", "PressureScorecard",
    "ModelFootprint", "ModelBudget", "PressureObserver", "estimate_memory_gb",
    "ModelLifecycleManager",
    "FleetManager", "FleetPlan", "FleetScorecard", "ModelAllocation",
]
