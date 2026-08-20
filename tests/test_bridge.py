"""Tests for the standalone CascadeBridge."""

from cascade_compression.bridge import CascadeBridge, _to_cascade_signal, _BUILTIN_AGENT_NAMES


class FakeSignal:
    def __init__(self, signal_type="test_event", severity="info", namespace="default"):
        self.signal_id = id(self)
        self.cluster_id = "test-cluster"
        self.namespace = namespace
        self.resource_kind = "test"
        self.resource_name = "test-resource"
        self.signal_type = signal_type
        self.severity = severity
        self.evidence = {"message": f"{signal_type} on {namespace}"}
        self.labels = {"domain": "test"}


class TestBridgeInit:
    def test_creates_without_llm(self):
        bridge = CascadeBridge()
        assert bridge.enabled
        assert bridge.stats.signals_processed == 0

    def test_domain_is_set(self):
        bridge = CascadeBridge(domain="aap")
        assert bridge.domain == "aap"

    def test_env_defaults(self):
        bridge = CascadeBridge()
        assert bridge._llm_model == ""


class TestBridgeProcess:
    def test_processes_signals(self):
        bridge = CascadeBridge()
        signals = [FakeSignal() for _ in range(10)]
        result = bridge.process(signals)
        assert result["enabled"]
        assert result["signals"] == 10
        assert bridge.stats.signals_processed == 10

    def test_compression_ratio_updates(self):
        bridge = CascadeBridge()
        signals = [FakeSignal(severity="info") for _ in range(100)]
        bridge.process(signals)
        assert bridge.stats.compression_ratio > 0

    def test_empty_signals_returns_disabled(self):
        bridge = CascadeBridge()
        result = bridge.process([])
        assert not result["enabled"]

    def test_repeated_signals_get_deduplicated(self):
        bridge = CascadeBridge()
        signals = [FakeSignal(signal_type="same_event", severity="medium") for _ in range(50)]
        bridge.process(signals)
        assert bridge.stats.cascade_handled > 0

    def test_high_severity_passes_through(self):
        bridge = CascadeBridge()
        signals = [FakeSignal(signal_type=f"critical_{i}", severity="critical") for i in range(5)]
        bridge.process(signals)
        assert bridge.stats.cascade_forwarded > 0

    def test_stats_accumulate(self):
        bridge = CascadeBridge()
        bridge.process([FakeSignal() for _ in range(10)])
        bridge.process([FakeSignal() for _ in range(10)])
        assert bridge.stats.signals_processed == 20


class TestSignalConversion:
    def test_converts_signal(self):
        sig = FakeSignal(signal_type="pod_crash", severity="high")
        cascade_sig = _to_cascade_signal(sig)
        assert cascade_sig.signal_type == "pod_crash"
        assert cascade_sig.severity == "high"
        assert cascade_sig.content.get("message")

    def test_handles_missing_fields(self):
        class MinimalSignal:
            signal_id = 1
            signal_type = "test"
            severity = "info"
        cascade_sig = _to_cascade_signal(MinimalSignal())
        assert cascade_sig.signal_type == "test"


class TestBridgeStats:
    def test_get_stats(self):
        bridge = CascadeBridge()
        bridge.process([FakeSignal() for _ in range(10)])
        stats = bridge.get_stats()
        assert "signals_processed" in stats
        assert "compression_ratio" in stats
        assert "fn_count" in stats
        assert stats["fn_count"] is None
        assert stats["fn_status"] == "not_measured"

    def test_false_negative_feedback_is_measured(self):
        bridge = CascadeBridge()
        bridge.record_feedback("alert", was_suppressed=True, is_important=True)
        bridge.record_feedback("alert", was_suppressed=False, is_important=True)
        stats = bridge.get_stats()
        assert stats["fn_count"] == 1
        assert stats["fn_rate"] == 50.0
        assert stats["fn_status"] == "measured"

    def test_get_llm_summary_empty(self):
        bridge = CascadeBridge()
        summary = bridge.get_llm_summary()
        assert summary["classified"] == 0
        assert summary["noise"] == 0
        assert summary["important"] == 0
        assert summary["activated_types"] == []

    def test_promotion_log_empty(self):
        bridge = CascadeBridge()
        assert bridge.get_promotion_log() == []


class TestLedgerIntegration:
    def test_no_ledger_no_error(self):
        bridge = CascadeBridge()
        signals = [FakeSignal() for _ in range(10)]
        result = bridge.process(signals)
        assert result["enabled"]

    def test_ledger_url_set(self):
        bridge = CascadeBridge(ledger_url="http://fake:28099")
        assert bridge._ledger_url == "http://fake:28099"


class TestGCLBuiltinFiltering:
    def test_builtin_agent_names_match_agents_py(self):
        from cascade_compression.cascade.agents import default_agents
        agents = default_agents()
        builtin_names = {a.name for a in agents}
        assert builtin_names == _BUILTIN_AGENT_NAMES

    def test_builtin_agent_fails_not_counted_as_fn(self):
        bridge = CascadeBridge()
        assert bridge._fn_count == 0
        assert bridge._gcl_builtin_fails == 0
        assert "deduplicate" in _BUILTIN_AGENT_NAMES

    def test_dynamic_agent_fails_counted_as_fn(self):
        bridge = CascadeBridge()
        bridge.record_feedback("my_dynamic_type", was_suppressed=True, is_important=True)
        assert bridge._fn_count == 1
        assert bridge._fn_evaluated == 1

    def test_gcl_builtin_fails_in_stats(self):
        bridge = CascadeBridge()
        bridge._gcl_builtin_fails = 42
        stats = bridge.get_stats()
        assert stats["gcl_builtin_fails"] == 42

    def test_gcl_builtin_fails_persisted(self):
        bridge = CascadeBridge()
        bridge._gcl_builtin_fails = 100
        bridge._fn_count = 0
        stats = bridge.get_stats()
        assert stats["gcl_builtin_fails"] == 100
        assert stats["fn_count"] is None  # no FN evaluated
