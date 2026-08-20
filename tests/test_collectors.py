"""Tests for collectors and domain packs."""

import pytest

from cascade_compression.collectors.base import BaseCollector
from cascade_compression.collectors.kubernetes import KubernetesSignal
from cascade_compression.collectors.aap import AAPSignal


class TestBaseCollector:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            BaseCollector()

    def test_describe_default(self):
        class Dummy(BaseCollector):
            def connect(self, config): return True
            def collect(self): return []
            def collect_all(self): return []
        d = Dummy()
        assert d.describe() == {"name": "base", "connected": False}


class TestKubernetesSignal:
    def test_pod_crashloop(self):
        sig = KubernetesSignal({"type": "pod", "name": "nginx", "phase": "Running",
                                "restarts": 10, "reason": "CrashLoopBackOff", "namespace": "default"})
        assert sig.signal_type == "pod_crashloop"
        assert sig.severity == "medium"
        assert sig.labels["domain"] == "kubernetes"

    def test_pod_pending(self):
        sig = KubernetesSignal({"type": "pod", "name": "app", "phase": "Pending",
                                "restarts": 0, "namespace": "staging"})
        assert sig.signal_type == "pod_pending"
        assert sig.severity == "low"

    def test_event(self):
        sig = KubernetesSignal({"type": "event", "name": "pod-1", "reason": "FailedMount",
                                "message": "Unable to mount volume", "namespace": "prod"})
        assert sig.signal_type == "event_failedmount"
        assert sig.severity == "medium"

    def test_node_healthy(self):
        sig = KubernetesSignal({"type": "node", "name": "worker-01",
                                "ready": "True", "memory_pressure": "False", "disk_pressure": "False"})
        assert sig.signal_type == "node_healthy"
        assert sig.severity == "info"

    def test_node_not_ready(self):
        sig = KubernetesSignal({"type": "node", "name": "worker-02",
                                "ready": "False", "memory_pressure": "False", "disk_pressure": "False"})
        assert sig.signal_type == "node_notready"
        assert sig.severity == "critical"


class TestAAPSignal:
    def test_job_succeeded(self):
        sig = AAPSignal({"id": 1, "name": "health-check", "status": "successful",
                         "failed": False, "elapsed": 10}, source="job")
        assert sig.signal_type == "job_successful"
        assert sig.severity == "info"
        assert sig.labels["domain"] == "aap"

    def test_job_failed(self):
        sig = AAPSignal({"id": 2, "name": "provision-cluster", "status": "failed",
                         "failed": True, "elapsed": 300}, source="job")
        assert sig.signal_type == "job_failed"
        assert sig.severity == "high"

    def test_job_failed_prod(self):
        sig = AAPSignal({"id": 3, "name": "prod-deploy", "status": "failed",
                         "failed": True, "elapsed": 60}, source="job")
        assert sig.severity == "critical"

    def test_task_event(self):
        sig = AAPSignal({"id": 4, "event": "runner_on_failed", "failed": True,
                         "host_name": "worker-01", "task": "Install package",
                         "play": "Setup", "role": "", "playbook": "setup.yml",
                         "job_id": 100, "stdout": "error: not found",
                         "job_name": "deploy", "changed": False}, source="task")
        assert sig.signal_type == "task_runner_on_failed"
        assert sig.severity == "high"
        assert "stdout" in sig.evidence

    def test_task_ok(self):
        sig = AAPSignal({"id": 5, "event": "runner_on_ok", "failed": False,
                         "host_name": "localhost", "task": "Gather Facts",
                         "play": "", "role": "", "playbook": "",
                         "job_id": 100, "stdout": "", "job_name": "",
                         "changed": False}, source="task")
        assert sig.signal_type == "task_runner_on_ok"
        assert sig.severity == "info"

    def test_activity_update(self):
        sig = AAPSignal({"id": 6, "operation": "update", "object1": "credential",
                         "object2": "aws-key", "changes": "rotated"}, source="activity")
        assert sig.signal_type == "config_update_credential"
        assert sig.severity == "medium"

    def test_activity_delete(self):
        sig = AAPSignal({"id": 7, "operation": "delete", "object1": "inventory",
                         "object2": "prod-cluster", "changes": ""}, source="activity")
        assert sig.signal_type == "config_delete_inventory"
        assert sig.severity == "high"


class TestPrometheusSignal:
    def test_metric_signal(self):
        from cascade_compression.collectors.prometheus import PrometheusSignal
        sig = PrometheusSignal({
            "signal_type": "metric_node_memory_exhausted", "severity": "critical",
            "source": "prometheus", "cluster": "ocpv05", "namespace": "",
            "name": "worker-01", "message": "node_memory: worker-01 value=0.95",
        })
        assert sig.signal_type == "metric_node_memory_exhausted"
        assert sig.severity == "critical"
        assert sig.labels["domain"] == "prometheus"
        assert sig.labels["cluster"] == "ocpv05"

    def test_alert_signal(self):
        from cascade_compression.collectors.prometheus import PrometheusSignal
        sig = PrometheusSignal({
            "signal_type": "alert_kubenodenotready", "severity": "critical",
            "source": "alertmanager", "cluster": "infra01", "namespace": "",
            "name": "worker-02", "message": "Alert KubeNodeNotReady: Node not ready",
        })
        assert sig.signal_type == "alert_kubenodenotready"
        assert sig.severity == "critical"


class TestGitOpsSignal:
    def test_app_degraded(self):
        from cascade_compression.collectors.gitops import GitOpsSignal
        sig = GitOpsSignal({
            "signal_type": "app_degraded", "severity": "high",
            "name": "sandbox-operator", "namespace": "openshift-gitops",
            "message": "ArgoCD app sandbox-operator degraded",
        })
        assert sig.signal_type == "app_degraded"
        assert sig.severity == "high"
        assert sig.labels["domain"] == "gitops"

    def test_sync_failed(self):
        from cascade_compression.collectors.gitops import GitOpsSignal
        sig = GitOpsSignal({
            "signal_type": "sync_failed", "severity": "high",
            "name": "monitoring", "namespace": "openshift-gitops",
            "message": "ArgoCD app monitoring sync failed",
        })
        assert sig.signal_type == "sync_failed"


class TestOVNSignal:
    def test_network_unavailable(self):
        from cascade_compression.collectors.ovn import OVNSignal
        sig = OVNSignal({
            "signal_type": "node_network_unavailable", "severity": "critical",
            "kind": "Node", "name": "worker-03",
            "message": "Node worker-03 network unavailable",
        })
        assert sig.signal_type == "node_network_unavailable"
        assert sig.severity == "critical"
        assert sig.labels["domain"] == "ovn"


class TestGovernorSignal:
    def test_policy_denied(self):
        from cascade_compression.collectors.governor import GovernorSignal
        sig = GovernorSignal({
            "signal_type": "policy_denied", "severity": "high",
            "kind": "decision", "name": "create-sandbox",
            "message": "Policy denied: create on sandbox: quota exceeded",
        })
        assert sig.signal_type == "policy_denied"
        assert sig.severity == "high"
        assert sig.labels["domain"] == "governor"


class TestCollectorImports:
    def test_all_collectors_importable(self):
        from cascade_compression.collectors.prometheus import PrometheusCollector
        from cascade_compression.collectors.gitops import GitOpsCollector
        from cascade_compression.collectors.ovn import OVNCollector
        from cascade_compression.collectors.governor import GovernorCollector
        assert all(c.name for c in [
            PrometheusCollector(), GitOpsCollector(),
            OVNCollector(), GovernorCollector(),
        ])

    def test_knowledge_collectors_importable(self):
        from cascade_compression.collectors.jira import JiraCollector
        from cascade_compression.collectors.git import GitCollector
        from cascade_compression.collectors.confluence import ConfluenceCollector
        assert all(c.name for c in [
            JiraCollector(), GitCollector(), ConfluenceCollector(),
        ])

class TestJiraSignal:
    def test_reopened_issue(self):
        from cascade_compression.collectors.jira import JiraCollector
        c = JiraCollector()
        signals = c._issue_to_signals({
            "key": "PROJ-100",
            "fields": {
                "summary": "Login broken",
                "status": {"name": "Reopened"},
                "assignee": {"displayName": "Alice"},
                "reporter": {"displayName": "Bob"},
                "issuetype": {"name": "Bug"},
                "priority": {"name": "Critical"},
                "labels": [],
                "comment": {"total": 3, "comments": []},
                "updated": "2026-08-19T10:00:00Z",
                "created": "2026-08-18T10:00:00Z",
            },
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "incident_repeat"
        assert signals[0].severity == "high"
        assert signals[0].labels["domain"] == "knowledge"
        assert signals[0].labels["source"] == "jira"

    def test_decision_churn(self):
        from cascade_compression.collectors.jira import JiraCollector
        c = JiraCollector()
        signals = c._issue_to_signals({
            "key": "ARCH-50",
            "fields": {
                "summary": "API versioning strategy",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Charlie"},
                "reporter": None,
                "issuetype": {"name": "Task"},
                "priority": {"name": "Major"},
                "labels": [],
                "comment": {"total": 22, "comments": []},
                "updated": "2026-08-19T10:00:00Z",
                "created": "2026-08-10T10:00:00Z",
            },
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "decision_revisited"

    def test_documentation_gap(self):
        from cascade_compression.collectors.jira import JiraCollector
        c = JiraCollector()
        signals = c._issue_to_signals({
            "key": "OPS-30",
            "fields": {
                "summary": "Missing runbook for failover",
                "status": {"name": "Open"},
                "assignee": None,
                "reporter": {"displayName": "Dave"},
                "issuetype": {"name": "Task"},
                "priority": {"name": "Major"},
                "labels": ["docs-needed"],
                "comment": {"total": 0, "comments": []},
                "updated": "2026-08-19T10:00:00Z",
                "created": "2026-08-19T09:00:00Z",
            },
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "documentation_gap"

    def test_onboarding_question(self):
        from cascade_compression.collectors.jira import JiraCollector
        c = JiraCollector()
        signals = c._issue_to_signals({
            "key": "DEV-10",
            "fields": {
                "summary": "Setup guide",
                "status": {"name": "Open"},
                "assignee": None,
                "reporter": None,
                "issuetype": {"name": "Task"},
                "priority": {"name": "Minor"},
                "labels": [],
                "comment": {"total": 1, "comments": [
                    {"author": {"displayName": "NewHire"}, "body": "Where do I find the VPN config?"},
                ]},
                "updated": "2026-08-19T10:00:00Z",
                "created": "2026-08-19T09:00:00Z",
            },
        })
        question_sigs = [s for s in signals if s.signal_type == "onboarding_question"]
        assert len(question_sigs) == 1
        assert "NewHire" in question_sigs[0].evidence["message"]

    def test_priority_mapping(self):
        from cascade_compression.collectors.jira import JiraCollector
        assert JiraCollector._priority_to_severity("Blocker") == "critical"
        assert JiraCollector._priority_to_severity("Critical") == "high"
        assert JiraCollector._priority_to_severity("Major") == "medium"
        assert JiraCollector._priority_to_severity("Minor") == "low"
        assert JiraCollector._priority_to_severity("Trivial") == "info"
        assert JiraCollector._priority_to_severity("Unknown") == "info"


class TestGitSignal:
    def test_hotfix_commit(self):
        from cascade_compression.collectors.git import GitSignal
        sig = GitSignal({
            "kind": "commit",
            "instance": "github",
            "repo": "org/app",
            "org": "org",
            "ref": "abc12345",
            "author": "alice",
            "signal_type": "hotfix_pattern",
            "severity": "high",
            "message": "alice hotfix in org/app: hotfix: fix crash on login",
            "topic": "hotfix",
        })
        assert sig.signal_type == "hotfix_pattern"
        assert sig.severity == "high"
        assert sig.labels["domain"] == "knowledge"
        assert sig.labels["source"] == "git"

    def test_regular_commit(self):
        from cascade_compression.collectors.git import GitSignal
        sig = GitSignal({
            "kind": "commit",
            "instance": "github",
            "repo": "org/app",
            "ref": "def67890",
            "author": "bob",
            "signal_type": "regular_commit",
            "severity": "info",
            "message": "bob commit in org/app: add tests",
        })
        assert sig.signal_type == "regular_commit"
        assert sig.severity == "info"

    def test_pr_decision_churn(self):
        from cascade_compression.collectors.git import GitSignal
        sig = GitSignal({
            "kind": "pull_request",
            "instance": "github",
            "repo": "org/api",
            "ref": "#42",
            "author": "charlie",
            "signal_type": "decision_revisited",
            "severity": "medium",
            "message": "PR org/api#42 has 25 comments",
            "topic": "decision_churn",
        })
        assert sig.signal_type == "decision_revisited"
        assert sig.labels["person"] == "charlie"

    def test_collector_describe(self):
        from cascade_compression.collectors.git import GitCollector
        c = GitCollector()
        desc = c.describe()
        assert desc["name"] == "git"
        assert desc["connected"] is False


class TestConfluenceSignal:
    def test_runbook_decay(self):
        from cascade_compression.collectors.confluence import ConfluenceSignal
        sig = ConfluenceSignal({
            "instance": "confluence",
            "page_id": "123",
            "title": "Deploy Runbook",
            "space": "OPS",
            "author": "alice",
            "signal_type": "runbook_decay",
            "severity": "medium",
            "message": "'Deploy Runbook' in OPS has 25 versions",
            "topic": "runbook",
        })
        assert sig.signal_type == "runbook_decay"
        assert sig.severity == "medium"
        assert sig.labels["domain"] == "knowledge"
        assert sig.labels["source"] == "confluence"

    def test_page_to_signals_runbook_decay(self):
        from cascade_compression.collectors.confluence import ConfluenceCollector
        c = ConfluenceCollector()
        signals = c._page_to_signals({
            "id": "1001",
            "title": "Database Failover Runbook",
            "space": {"key": "OPS"},
            "version": {"number": 25, "by": {"displayName": "alice"}, "when": "2026-08-19"},
            "history": {"lastUpdated": {"by": {"displayName": "alice"}, "when": "2026-08-19"},
                        "createdBy": {"displayName": "bob"}, "createdDate": "2025-01-01"},
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "runbook_decay"

    def test_page_to_signals_stale_runbook(self):
        from cascade_compression.collectors.confluence import ConfluenceCollector
        c = ConfluenceCollector()
        signals = c._page_to_signals({
            "id": "1002",
            "title": "Emergency Troubleshooting Playbook",
            "space": {"key": "OPS"},
            "version": {"number": 1, "by": {"displayName": "charlie"}, "when": "2025-01-01"},
            "history": {"lastUpdated": {}, "createdBy": {}, "createdDate": "2025-01-01"},
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "documentation_gap"

    def test_page_to_signals_postmortem(self):
        from cascade_compression.collectors.confluence import ConfluenceCollector
        c = ConfluenceCollector()
        signals = c._page_to_signals({
            "id": "1003",
            "title": "2026-08-15 Postmortem: API Outage",
            "space": {"key": "INC"},
            "version": {"number": 3, "by": {"displayName": "dave"}, "when": "2026-08-16"},
            "history": {"lastUpdated": {"by": {"displayName": "dave"}},
                        "createdBy": {"displayName": "dave"}, "createdDate": "2026-08-15"},
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "incident_learning"

    def test_page_to_signals_architecture_churn(self):
        from cascade_compression.collectors.confluence import ConfluenceCollector
        c = ConfluenceCollector()
        signals = c._page_to_signals({
            "id": "1004",
            "title": "ADR: Event Bus Architecture",
            "space": {"key": "ARCH"},
            "version": {"number": 12, "by": {"displayName": "eve"}, "when": "2026-08-19"},
            "history": {"lastUpdated": {"by": {"displayName": "eve"}},
                        "createdBy": {"displayName": "eve"}, "createdDate": "2026-06-01"},
        })
        assert len(signals) == 1
        assert signals[0].signal_type == "decision_revisited"

    def test_collector_describe(self):
        from cascade_compression.collectors.confluence import ConfluenceCollector
        c = ConfluenceCollector()
        desc = c.describe()
        assert desc["name"] == "confluence"
        assert desc["connected"] is False


class TestDomainPacks:
    def test_kubernetes_pack(self):
        from cascade_compression.domains import kubernetes
        assert kubernetes.DOMAIN == "kubernetes"
        assert kubernetes.COLLECTOR_CLASS is not None
        assert "routine_noise" in kubernetes.SYSTEM_PROMPT

    def test_aap_pack(self):
        from cascade_compression.domains import aap
        assert aap.DOMAIN == "aap"
        assert aap.COLLECTOR_CLASS is not None
        assert "routine_noise" in aap.SYSTEM_PROMPT

    def test_finance_pack(self):
        from cascade_compression.domains import finance
        assert finance.DOMAIN == "finance"
        assert "routine_noise" in finance.SYSTEM_PROMPT
