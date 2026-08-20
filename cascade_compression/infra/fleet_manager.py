"""Fleet manager — workload-driven model deployment planner.

Given a workload type, node resources, and the corpora, determines which
models to load at what replica count and grades the resulting fleet plan.
"""

from __future__ import annotations

import subprocess
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from ..routing.corpora import CorporaEntry, RoutingCorpora, load_corpora
from .scaler import estimate_memory_gb
from ..routing.strategy_router import InferenceStrategy, StrategyRouter

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

Grade = Literal["green", "yellow", "red"]


class FleetScorecard(BaseModel):
    """Rubric-graded fleet health state."""

    fleet_utilization_pct: float = 0.0
    fleet_utilization_grade: Grade = "red"       # >=70 green, >=40 yellow
    replica_coverage_grade: Grade = "red"        # all tiers covered = green, macro missing = yellow, micro missing = red
    memory_headroom_pct: float = 0.0
    memory_headroom_grade: Grade = "red"         # >=20% green, >=10% yellow
    model_ready_pct: float = 0.0
    model_ready_grade: Grade = "red"             # 100% green, >=80% yellow
    aggregate_throughput_tok_s: float = 0.0
    aggregate_throughput_grade: Grade = "red"     # >=100 green, >=50 yellow
    overall_grade: Grade = "red"


class ModelAllocation(BaseModel):
    """A model deployment decision."""

    model: str
    tier: str                    # "micro" or "macro"
    replicas: int
    memory_per_replica_gb: float
    total_memory_gb: float
    estimated_throughput_tok_s: float


class FleetPlan(BaseModel):
    """Complete deployment plan for a workload."""

    workload_type: str
    strategy_name: str
    memory_budget_gb: float
    cpu_cores: int
    allocations: List[ModelAllocation] = Field(default_factory=list)
    total_memory_used_gb: float = 0.0
    scorecard: FleetScorecard = Field(default_factory=FleetScorecard)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GRADE_RANK = {"green": 0, "yellow": 1, "red": 2}


def _worst_grade(*grades: Grade) -> Grade:
    return max(grades, key=lambda g: _GRADE_RANK.get(g, 2))


# ---------------------------------------------------------------------------
# Workload-to-industry + tasks mapping
# ---------------------------------------------------------------------------

# Maps workload types to (industry, [task_names]) so the fleet manager
# knows which corpora entries to look up.
_WORKLOAD_TASKS = {
    "fraud-triage": ("fsi", ["dispute-classification", "fraud-scoring"]),
    "dispute-resolution": ("fsi", ["dispute-classification", "loan-document-extraction"]),
    "loan-document-intake": ("fsi", ["loan-document-extraction"]),
    "compliance-screening": ("fsi", ["dispute-classification", "fraud-scoring"]),
    "infrastructure-monitoring": ("basic", ["classify-short", "extract-medium", "summarize-long"]),
    "claims-processing": ("insurance", ["claims-triage", "policy-extraction", "underwriting-risk"]),
    "retail-operations": ("retail", ["demand-classification", "product-categorization", "review-sentiment"]),
    "telecom-operations": ("telecom", ["ticket-routing", "network-anomaly", "churn-prediction"]),
    "generic": ("basic", ["classify-short", "extract-medium", "summarize-long"]),
    "slim-classification": ("basic", ["classify-short"]),
}


# ---------------------------------------------------------------------------
# FleetManager
# ---------------------------------------------------------------------------


class FleetManager:
    """Workload-driven model deployment planner.

    Given a workload type, node resources, and the corpora, determines
    which models to deploy at what replica count and grades the plan.
    """

    def __init__(
        self,
        corpora: Optional[RoutingCorpora] = None,
        memory_budget_gb: float = 64.0,
        cpu_cores: int = 32,
    ) -> None:
        self._corpora = corpora or load_corpora()
        self._memory_budget_gb = memory_budget_gb
        self._cpu_cores = cpu_cores
        self._strategy_router = StrategyRouter()

    def plan(self, workload_type: str) -> FleetPlan:
        """Generate a deployment plan for a workload type.

        Algorithm:
        1. Resolve strategy from workload_type
        2. Look up corpora entries for the workload's industry + tasks
        3. Collect unique models needed across all tasks/tiers
        4. Estimate memory per model using estimate_memory_gb()
        5. Allocate replicas:
           - Classification-tier (micro <=1GB): fill remaining budget with replicas
           - Medium-tier (micro 1-5GB): 2-3 replicas
           - Macro-tier (macro >5GB): 1 replica
        6. Grade the plan with FleetScorecard
        """
        strategy = self._strategy_router.resolve_strategy(workload_type)
        industry, tasks = _WORKLOAD_TASKS.get(
            workload_type, ("basic", ["classify-short"])
        )

        # Collect unique models from corpora entries
        model_entries: dict[str, tuple[str, CorporaEntry]] = {}  # model -> (tier, entry)
        for task in tasks:
            for tier in ("micro", "macro"):
                entry = self._corpora.lookup(industry, task, tier, strategy=strategy)
                if entry and entry.config.model not in model_entries:
                    model_entries[entry.config.model] = (entry.tier, entry)

        # For slim strategy, limit to at most 2 models
        if workload_type == "slim-classification":
            items = list(model_entries.items())[:2]
            model_entries = dict(items)

        # Build allocations
        allocations: list[ModelAllocation] = []
        total_used = 0.0
        remaining_budget = self._memory_budget_gb

        # Sort: macro first (1 replica), then medium, then small (most replicas)
        sorted_models = sorted(
            model_entries.items(),
            key=lambda kv: kv[1][1].config.params,
            reverse=True,
        )

        for model_name, (tier, entry) in sorted_models:
            mem_per_replica = estimate_memory_gb(entry.config.params, entry.config.dtype)
            if mem_per_replica <= 0:
                mem_per_replica = 0.1  # floor for tiny models

            # Skip if even one replica won't fit
            if mem_per_replica > remaining_budget:
                continue

            # Determine replicas by tier and size
            if tier == "macro" or mem_per_replica > 5.0:
                actual_tier = "macro"
                replicas = 1
            elif mem_per_replica > 1.0:
                actual_tier = "micro"
                replicas = min(3, max(1, int(remaining_budget / mem_per_replica)))
                replicas = max(replicas, 2)  # at least 2 for medium tier
            else:
                actual_tier = "micro"
                # Classification-tier: fill remaining budget
                max_replicas = max(1, int(remaining_budget / mem_per_replica))
                replicas = min(max_replicas, 8)  # cap at 8

            total_mem = mem_per_replica * replicas
            if total_mem > remaining_budget:
                replicas = max(1, int(remaining_budget / mem_per_replica))
                total_mem = mem_per_replica * replicas

            if replicas < 1:
                continue

            # Estimate throughput from scorecard
            throughput = entry.scorecard.throughput_tok_s * replicas

            alloc = ModelAllocation(
                model=model_name,
                tier=actual_tier,
                replicas=replicas,
                memory_per_replica_gb=round(mem_per_replica, 3),
                total_memory_gb=round(total_mem, 3),
                estimated_throughput_tok_s=round(throughput, 2),
            )
            allocations.append(alloc)
            total_used += total_mem
            remaining_budget -= total_mem

        plan = FleetPlan(
            workload_type=workload_type,
            strategy_name=strategy.name,
            memory_budget_gb=self._memory_budget_gb,
            cpu_cores=self._cpu_cores,
            allocations=allocations,
            total_memory_used_gb=round(total_used, 3),
        )

        plan.scorecard = self._grade_plan(plan)
        return plan

    def apply(self, plan: FleetPlan, dry_run: bool = True) -> FleetScorecard:
        """Apply a fleet plan by scaling deployments.

        If dry_run=True, just return what would happen.
        If dry_run=False, execute oc scale commands.
        """
        if not dry_run:
            for alloc in plan.allocations:
                deployment = ModelLifecycleManager._model_to_deployment(alloc.model)
                cmd = [
                    "oc", "scale", "deployment", deployment,
                    f"--replicas={alloc.replicas}",
                    "-n", "triforce",
                ]
                subprocess.run(cmd, check=True, capture_output=True, text=True)

        return plan.scorecard

    def current_fleet(self) -> FleetPlan:
        """Read current state from cluster (oc get deploy)."""
        try:
            result = subprocess.run(
                ["oc", "get", "deploy", "-n", "triforce", "-o", "json"],
                capture_output=True, text=True, check=True,
            )
            import json
            data = json.loads(result.stdout)
            allocations = []
            total_used = 0.0
            for item in data.get("items", []):
                name = item.get("metadata", {}).get("name", "")
                replicas = item.get("spec", {}).get("replicas", 0)
                if replicas > 0 and name.startswith(("ovms-", "llama-", "vllm-")):
                    alloc = ModelAllocation(
                        model=name,
                        tier="micro",
                        replicas=replicas,
                        memory_per_replica_gb=0.0,
                        total_memory_gb=0.0,
                        estimated_throughput_tok_s=0.0,
                    )
                    allocations.append(alloc)

            return FleetPlan(
                workload_type="current",
                strategy_name="live",
                memory_budget_gb=self._memory_budget_gb,
                cpu_cores=self._cpu_cores,
                allocations=allocations,
                total_memory_used_gb=total_used,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return FleetPlan(
                workload_type="current",
                strategy_name="live",
                memory_budget_gb=self._memory_budget_gb,
                cpu_cores=self._cpu_cores,
            )

    def _grade_plan(self, plan: FleetPlan) -> FleetScorecard:
        """Grade a fleet plan against the rubric."""
        # Fleet utilization: % of memory budget used
        utilization_pct = (
            (plan.total_memory_used_gb / plan.memory_budget_gb * 100)
            if plan.memory_budget_gb > 0 else 0.0
        )
        if utilization_pct >= 70:
            utilization_grade: Grade = "green"
        elif utilization_pct >= 40:
            utilization_grade = "yellow"
        else:
            utilization_grade = "red"

        # Replica coverage: check if both tiers are present
        has_micro = any(a.tier == "micro" for a in plan.allocations)
        has_macro = any(a.tier == "macro" for a in plan.allocations)
        if has_micro and has_macro:
            coverage_grade: Grade = "green"
        elif has_micro and not has_macro:
            coverage_grade = "yellow"
        else:
            coverage_grade = "red"

        # Memory headroom
        headroom_pct = (
            ((plan.memory_budget_gb - plan.total_memory_used_gb) / plan.memory_budget_gb * 100)
            if plan.memory_budget_gb > 0 else 0.0
        )
        if headroom_pct >= 20:
            headroom_grade: Grade = "green"
        elif headroom_pct >= 10:
            headroom_grade = "yellow"
        else:
            headroom_grade = "red"

        # Model ready %: all allocated models are "ready" (in a plan, 100% by definition)
        total_models = len(plan.allocations)
        ready_pct = 100.0 if total_models > 0 else 0.0
        if ready_pct >= 100.0:
            ready_grade: Grade = "green"
        elif ready_pct >= 80.0:
            ready_grade = "yellow"
        else:
            ready_grade = "red"

        # Aggregate throughput
        agg_throughput = sum(a.estimated_throughput_tok_s for a in plan.allocations)
        if agg_throughput >= 100:
            throughput_grade: Grade = "green"
        elif agg_throughput >= 50:
            throughput_grade = "yellow"
        else:
            throughput_grade = "red"

        overall = _worst_grade(
            utilization_grade,
            coverage_grade,
            headroom_grade,
            ready_grade,
            throughput_grade,
        )

        return FleetScorecard(
            fleet_utilization_pct=round(utilization_pct, 2),
            fleet_utilization_grade=utilization_grade,
            replica_coverage_grade=coverage_grade,
            memory_headroom_pct=round(headroom_pct, 2),
            memory_headroom_grade=headroom_grade,
            model_ready_pct=ready_pct,
            model_ready_grade=ready_grade,
            aggregate_throughput_tok_s=round(agg_throughput, 2),
            aggregate_throughput_grade=throughput_grade,
            overall_grade=overall,
        )


# Re-export ModelLifecycleManager so fleet_manager users can access it
from .scaler import ModelLifecycleManager  # noqa: E402, F401
