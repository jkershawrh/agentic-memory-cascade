"""Regression coverage for production-readiness review findings."""

import json
import ssl
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.corpus_analyzer import CorpusAnalyzer, PatternCandidate
from cascade_compression.cascade.dynamic_agents import (
    DominantNoiseSuppressor,
    RepeatFloodSuppressor,
)
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.promotion import AgentMetrics, RuleAgent
from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal
from cascade_compression.cascade.service import SignalInput, _to_signal
from cascade_compression.cli import _collector_config
from cascade_compression.collectors.aap import AAPCollector
from cascade_compression.collectors.finance import FinanceCollector
from cascade_compression.collectors.kubernetes import KubernetesCollector
from cascade_compression.integrations.ledger import write_decisions


def _candidate(signal_type="routine_event", pattern_type="dominant_type"):
    metrics = AgentMetrics(
        name=f"suppress_{signal_type}",
        config={"signal_type": signal_type, "pattern_type": pattern_type},
    )
    rule = RuleAgent({
        "name": metrics.name,
        "signal_types": [signal_type],
        "condition": {
            "field": "signal_type",
            "operator": "eq",
            "value": signal_type,
        },
        "classification": "routine_noise",
    })
    return metrics, rule


def test_dynamic_suppressors_never_suppress_medium_or_higher():
    signals = [
        Signal(
            signal_type="incident",
            severity=severity,
            source="api",
            namespace="prod",
        )
        for severity in ("medium", "high", "critical")
    ]

    repeat = RepeatFloodSuppressor(max_repeats=0)
    repeat.add_signal_type("incident")
    dominant = DominantNoiseSuppressor()
    dominant.add_noise_type("incident")

    assert repeat.process(signals) == []
    assert dominant.process(signals) == []


def test_suppression_cannot_override_an_escalation():
    class Escalate:
        name = "escalate"
        stage = 1

        def process(self, signals):
            return [CascadeDecision(
                signal_id=signals[0].signal_id,
                agent_name=self.name,
                outcome=Outcome.ESCALATE,
            )]

    class Suppress:
        name = "suppress"
        stage = 2

        def process(self, signals):
            return [CascadeDecision(
                signal_id=signals[0].signal_id,
                agent_name=self.name,
                outcome=Outcome.SUPPRESS,
            )]

    signal = Signal(signal_type="incident", severity="low")
    result = CascadePipeline([Escalate(), Suppress()]).run([signal])

    assert [item.signal_id for item in result.remaining] == [signal.signal_id]
    assert result.suppressed_count == 0


def test_auto_promotion_requires_200_noise_samples():
    bridge = CascadeBridge()
    candidate = _candidate()
    bridge.corpus_analyzer.maybe_analyze = lambda: [candidate]
    bridge._llm_noise_counts["routine_event"] = 199
    bridge._discover_and_promote()
    assert "routine_event" not in bridge._activated_types

    bridge.corpus_analyzer.maybe_analyze = lambda: []
    bridge._llm_noise_counts["routine_event"] = 200
    bridge._discover_and_promote()
    assert bridge._activated_patterns == {"routine_event": "dominant_type"}


def test_any_important_classification_blocks_auto_activation():
    bridge = CascadeBridge()
    bridge.corpus_analyzer.maybe_analyze = lambda: [_candidate()]
    bridge._llm_noise_counts["routine_event"] = 200
    bridge._llm_important_counts["routine_event"] = 1

    bridge._discover_and_promote()

    assert "routine_event" not in bridge._activated_types


def test_state_restores_only_the_original_suppressor_kind(tmp_path, monkeypatch):
    state_file = tmp_path / "cascade-state.json"
    monkeypatch.setenv("CASCADE_STATE_FILE", str(state_file))
    bridge = CascadeBridge()
    bridge._activated_types.add("repeat_event")
    bridge._activated_patterns["repeat_event"] = "repeat_flood"
    bridge._repeat_suppressor.add_signal_type("repeat_event")
    bridge._save_state()

    restored = CascadeBridge()

    assert "repeat_event" in restored._repeat_suppressor._signal_types
    assert "repeat_event" not in restored._noise_suppressor._noise_types


def test_legacy_state_is_not_restored_with_broader_semantics(tmp_path, monkeypatch):
    state_file = tmp_path / "legacy-state.json"
    state_file.write_text(json.dumps({"activated_types": ["unknown_kind"]}))
    monkeypatch.setenv("CASCADE_STATE_FILE", str(state_file))

    restored = CascadeBridge()

    assert restored._activated_types == set()


def test_repeat_candidate_uses_the_actual_signal_type():
    analyzer = CorpusAnalyzer()
    candidate = PatternCandidate(
        pattern_type="repeat_flood",
        key="repeat:pod_crashloop",
        count=20,
        total_signals=100,
        frequency=0.2,
    )

    metrics, rule = analyzer._candidate_to_agent(candidate)

    assert metrics.config["signal_type"] == "pod_crashloop"
    assert rule.signal_types == ["pod_crashloop"]


def test_replay_data_path_is_forwarded_to_collectors():
    assert _collector_config(data_path="signals.json") == {
        "data_path": "signals.json"
    }


def test_aap_collector_uses_explicit_database_url():
    collector = AAPCollector()
    collector._get_conn = lambda: object()

    assert collector.connect({"db_url": "postgresql://db.example/aap"}) is True
    assert collector._db_url == "postgresql://db.example/aap"


def test_finance_replay_loads_json_and_csv_files(tmp_path):
    json_path = tmp_path / "transactions.json"
    json_path.write_text(json.dumps([{
        "id": "txn-1",
        "amount": 12.50,
        "transaction_type": "card",
        "merchant": "Example",
    }]))
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text(
        "id,amount,transaction_type,merchant,is_recurring,velocity_1h\n"
        "txn-2,20.25,card,Example,false,1\n"
    )

    json_collector = FinanceCollector()
    csv_collector = FinanceCollector()

    assert json_collector.connect({"data_path": str(json_path)}) is True
    assert csv_collector.connect({"data_path": str(csv_path)}) is True
    assert len(json_collector.collect_all()) == 1
    assert len(csv_collector.collect_all()) == 1


def test_finance_replay_rejects_unrelated_json_shape(tmp_path):
    data_path = tmp_path / "not-transactions.json"
    data_path.write_text(json.dumps({"tasks": {}}))
    collector = FinanceCollector()

    assert collector.connect({"data_path": str(data_path)}) is False


def test_service_preserves_caller_signal_id():
    signal_id = uuid4()
    signal = _to_signal(SignalInput(signal_id=signal_id, signal_type="incident"))
    assert signal.signal_id == signal_id


def test_ledger_uses_verified_tls_and_checks_the_response():
    signal = Signal(signal_type="incident", severity="low")
    decision = CascadeDecision(
        signal_id=signal.signal_id,
        agent_name="test",
        outcome=Outcome.SUPPRESS,
    )
    response = MagicMock()
    client = MagicMock()
    client.post.return_value = response
    context = MagicMock()
    context.__enter__.return_value = client

    with patch("httpx.Client", return_value=context) as client_factory:
        write_decisions(
            "https://ledger.example",
            "token",
            SimpleNamespace(decisions=[decision]),
            [signal],
            "test",
        )

    client_factory.assert_called_once_with(timeout=10)
    response.raise_for_status.assert_called_once_with()


def test_kubernetes_collector_keeps_system_tls_verification_without_sa_ca():
    collector = KubernetesCollector()
    collector._api_url = "https://kubernetes.example"
    response = MagicMock()
    response.read.return_value = b"{}"
    context = MagicMock()
    context.__enter__.return_value = response

    with patch(
        "cascade_compression.collectors.kubernetes.os.path.exists",
        return_value=False,
    ):
        with patch(
            "cascade_compression.collectors.kubernetes.urlopen",
            return_value=context,
        ) as request:
            assert collector._get("/api") == {}

    tls_context = request.call_args.kwargs["context"]
    assert tls_context.check_hostname is True
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
