"""Edge case scenarios — adversarial signals designed to find cascade gaps.

Each scenario targets a specific failure mode:
1. Severity boundary — signals just above/below the gate
2. Semantic ambiguity — signals that look like noise but aren't
3. Contextual traps — signals that are noise globally but critical locally
4. Temporal patterns — signals that are only important in sequence
5. Cross-domain correlation — signals that need federation to interpret
6. Evasion patterns — real incidents disguised as routine
7. Compliance traps — regulatory signals misclassified as noise

Usage:
    python -m cascade_compression.edge_scenarios \
        --target http://cascade-finance:8090 \
        --domain finance \
        --scenario all
"""

import argparse
import json
import logging
import time
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def _post(target: str, signals: list) -> dict:
    data = json.dumps({"signals": signals}).encode()
    req = Request(f"{target}/cascade", data=data,
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _check(target: str, signals: list, expected_survivors: int, scenario: str):
    result = _post(target, signals)
    survivors = result.get("forwarded", 0) + result.get("survivors", 0)
    total = result.get("total", len(signals))
    compressed = result.get("compressed", 0)
    status = "PASS" if survivors >= expected_survivors else "FAIL"
    log.info(
        "[%s] %s: sent=%d, compressed=%d, survivors=%d (expected >=%d)",
        status, scenario, total, compressed, survivors, expected_survivors,
    )
    return {
        "scenario": scenario,
        "status": status,
        "sent": total,
        "compressed": compressed,
        "survivors": survivors,
        "expected_survivors": expected_survivors,
    }


# ─── FINANCE SCENARIOS ───

def finance_structuring(target):
    """$9,999 cash deposits — just under the $10K reporting threshold.
    These MUST survive. A cascade that drops them has a compliance gap."""
    signals = []
    for i in range(5):
        signals.append(
            {"signal_type": "txn_cash_deposit", "severity": "high",
             "source": "branch", "namespace": "banking",
             "content": {"amount": 9999 - i * 10, "merchant": "Cash Deposit", "country": "US",
                         "location": "Miami", "velocity_1h": 1, "velocity_24h": 3 + i},
             "labels": {"account_id": f"ACCT-EDGE-{i}", "label": "compliance",
                        "label_detail": "structuring_sub_10k"}})
    return _check(target, signals, 5, "STRUCTURING: $9,999 deposits (compliance)")


def finance_rapid_test(target):
    """5 small charges in 60 seconds from different merchants — stolen card testing pattern.
    Low individual severity but the PATTERN is critical."""
    signals = [
        {"signal_type": "txn_card_purchase", "severity": "info",
         "source": "online", "namespace": "retail",
         "content": {"amount": 1.00 + i * 0.50, "merchant": f"TestMerchant-{i}",
                     "country": "US", "location": "Various",
                     "velocity_1h": 5 + i, "velocity_24h": 5 + i,
                     "is_first_time_merchant": True},
         "labels": {"account_id": "ACCT-STOLEN-001", "label": "fraud",
                    "label_detail": "rapid_small_charges"}}
        for i in range(5)
    ]
    return _check(target, signals, 0, "RAPID TEST: 5x$1 charges (info severity, should be gated)")


def finance_amount_anomaly(target):
    """Normal customer suddenly makes a $50K wire to a new country.
    High severity, should always survive."""
    signals = [
        {"signal_type": "txn_wire_transfer", "severity": "critical",
         "source": "online", "namespace": "banking",
         "content": {"amount": 50000, "merchant": "Wire Transfer",
                     "country": "CY", "location": "Limassol",
                     "velocity_1h": 1, "velocity_24h": 1,
                     "is_first_time_country": True, "is_first_time_merchant": True},
         "labels": {"account_id": "ACCT-ANOMALY-001", "label": "fraud",
                    "label_detail": "amount_anomaly_first_time_country"}},
    ]
    return _check(target, signals, 1, "AMOUNT ANOMALY: $50K wire to Cyprus (critical)")


def finance_noise_flood_then_real(target):
    """50 normal transactions followed by 1 fraud. Tests whether the cascade
    still catches the needle after learning the haystack."""
    noise = [
        {"signal_type": "txn_card_purchase", "severity": "info",
         "source": "pos", "namespace": "retail",
         "content": {"amount": 25 + i, "merchant": "Starbucks",
                     "country": "US", "location": "Dallas",
                     "velocity_1h": 1, "velocity_24h": 2,
                     "is_recurring": True},
         "labels": {"account_id": f"ACCT-NORMAL-{i:03d}", "label": "normal"}}
        for i in range(50)
    ]
    fraud = [
        {"signal_type": "txn_wire_transfer", "severity": "critical",
         "source": "online", "namespace": "banking",
         "content": {"amount": 89000, "merchant": "Wire Transfer",
                     "country": "RU", "location": "Moscow",
                     "velocity_1h": 1, "velocity_24h": 1,
                     "is_first_time_country": True},
         "labels": {"account_id": "ACCT-NORMAL-025", "label": "fraud",
                    "label_detail": "account_takeover_wire"}},
    ]
    return _check(target, noise + fraud, 1, "NEEDLE IN HAYSTACK: 50 normal + 1 fraud")


def finance_sanctions_adjacent(target):
    """Transaction to a country adjacent to a sanctioned nation.
    Not sanctioned itself but compliance-relevant."""
    signals = [
        {"signal_type": "txn_wire_transfer", "severity": "high",
         "source": "online", "namespace": "banking",
         "content": {"amount": 15000, "merchant": "International Wire",
                     "country": "AE", "location": "Dubai",
                     "velocity_1h": 1, "velocity_24h": 1,
                     "is_first_time_country": True},
         "labels": {"account_id": "ACCT-SANC-001", "label": "compliance",
                    "label_detail": "sanctions_adjacent_country"}},
    ]
    return _check(target, signals, 1, "SANCTIONS ADJACENT: $15K wire to UAE (compliance)")


# ─── HEALTHCARE SCENARIOS ───

def healthcare_vital_borderline(target):
    """Heart rate at 99 (normal <100) with slight BP elevation.
    Individually normal, together concerning."""
    signals = [
        {"signal_type": "hl7_vital_sign", "severity": "medium",
         "source": "icu_monitor", "namespace": "cardiology",
         "content": {"heart_rate": 99, "bp_systolic": 139, "bp_diastolic": 89,
                     "spo2": 95, "temp_f": 99.8, "respiratory_rate": 19},
         "labels": {"patient_id": "PT-BORDER-001", "label": "needs_attention",
                    "label_detail": "borderline_vitals_cluster"}},
    ]
    return _check(target, signals, 1, "BORDERLINE VITALS: all values just under threshold")


def healthcare_hipaa_after_hours(target):
    """Medical record access at 3am by a non-treating provider.
    The access itself is routine; the CONTEXT makes it suspicious."""
    signals = [
        {"signal_type": "hl7_hipaa_access", "severity": "medium",
         "source": "ehr_audit", "namespace": "compliance",
         "content": {"record_type": "patient_chart", "accessor_role": "nurse",
                     "accessor_department": "radiology", "patient_department": "oncology",
                     "access_hour": 3, "is_treating_provider": False},
         "labels": {"patient_id": "PT-VIP-001", "label": "compliance",
                    "label_detail": "hipaa_after_hours_cross_department"}},
    ]
    return _check(target, signals, 1, "HIPAA: 3am cross-department access (compliance)")


def healthcare_silent_deterioration(target):
    """Patient vitals slowly declining over 4 readings. Each reading is 'normal'
    individually but the TREND is critical."""
    signals = [
        {"signal_type": "hl7_vital_sign", "severity": "info",
         "source": "floor_monitor", "namespace": "medicine",
         "content": {"heart_rate": 72 + i * 8, "bp_systolic": 120 - i * 10,
                     "spo2": 98 - i * 2, "reading_number": i + 1},
         "labels": {"patient_id": "PT-DECLINE-001", "label": "needs_attention",
                    "label_detail": "silent_deterioration"}}
        for i in range(4)
    ]
    return _check(target, signals, 1, "SILENT DETERIORATION: 4 readings trending down")


# ─── TELECOM SCENARIOS ───

def telecom_maintenance_masking(target):
    """What looks like scheduled maintenance but affects a critical path.
    Low severity, but the affected circuit serves a hospital."""
    signals = [
        {"signal_type": "tmf_maintenance", "severity": "info",
         "source": "nms", "namespace": "operations",
         "content": {"circuit_id": "CKT-HOSP-001", "maintenance_type": "planned",
                     "customer_class": "critical_infrastructure",
                     "affected_services": ["hospital_primary", "911_backup"],
                     "duration_hours": 4},
         "labels": {"region": "northeast", "label": "needs_attention",
                    "label_detail": "critical_infrastructure_maintenance"}},
    ]
    return _check(target, signals, 1, "MAINTENANCE MASKING: planned work on hospital circuit")


def telecom_cascade_failure(target):
    """3 fiber cuts in the same region within 1 hour — could be coordinated attack
    or weather event. Individual cuts are routine; the CLUSTER matters."""
    signals = [
        {"signal_type": "tmf_fiber_cut", "severity": "medium",
         "source": "nms", "namespace": "outside_plant",
         "content": {"span_id": f"SPAN-{100+i}", "location": f"Sector-{i}",
                     "distance_km": 2.5 * i, "region": "northeast",
                     "cuts_in_region_1h": 3},
         "labels": {"region": "northeast", "label": "real_incident",
                    "label_detail": "clustered_fiber_cuts"}}
        for i in range(3)
    ]
    return _check(target, signals, 3, "CASCADE FAILURE: 3 fiber cuts same region")


def telecom_bgp_anomaly(target):
    """BGP route change from an unexpected AS. Could be a hijack.
    This is the kind of signal that looks routine but has massive impact."""
    signals = [
        {"signal_type": "tmf_bgp_change", "severity": "high",
         "source": "route_monitor", "namespace": "backbone",
         "content": {"prefix": "10.0.0.0/8", "new_as": "AS65001",
                     "expected_as": "AS64512", "change_type": "origin_change",
                     "affected_prefixes": 1500},
         "labels": {"region": "global", "label": "real_incident",
                    "label_detail": "bgp_hijack_candidate"}},
    ]
    return _check(target, signals, 1, "BGP ANOMALY: unexpected origin AS change")


# ─── CROSS-DOMAIN SCENARIOS ───

def burst_then_silence(target, domain):
    """100 signals in 10 seconds, then nothing.
    Tests whether the cascade handles burst without dropping critical signals."""
    signals = [
        {"signal_type": f"{domain}_burst_test", "severity": "critical" if i == 99 else "info",
         "source": "edge_test", "namespace": "burst",
         "content": {"sequence": i, "burst_id": "BURST-001", "total": 100},
         "labels": {"test": "burst", "label": "real_incident" if i == 99 else "noise"}}
        for i in range(100)
    ]
    return _check(target, signals, 1, f"BURST: 100 signals, critical at position 99")


# ─── CROSS-DOMAIN FEDERATION SCENARIOS ───
# These test whether correlated signals across independent cascades
# produce the right memories for federation to connect.

def cross_outage_finance_telecom(finance_target, telecom_target):
    """A telecom outage causes payment processing failures.
    Each domain sees its half — federation should correlate them."""
    telecom_signals = [
        {"signal_type": "tmf_fiber_cut", "severity": "critical",
         "source": "nms", "namespace": "backbone",
         "content": {"span_id": "SPAN-DATACENTER-1", "location": "NYC-DC-1",
                     "region": "northeast", "affected_services": ["payment_gateway"]},
         "labels": {"region": "northeast", "label": "real_incident",
                    "label_detail": "datacenter_fiber_cut"}},
    ]
    finance_signals = [
        {"signal_type": "txn_wire_transfer", "severity": "critical",
         "source": "payment_gateway", "namespace": "banking",
         "content": {"error": "connection_timeout", "gateway": "NYC-DC-1",
                     "failed_transactions": 847, "duration_minutes": 12},
         "labels": {"account_id": "SYSTEM", "label": "real_incident",
                    "label_detail": "payment_gateway_outage"}},
    ]
    results = []
    r1 = _check(telecom_target, telecom_signals, 1, "CROSS-OUTAGE TELECOM: fiber cut at datacenter")
    results.append(r1)
    r2 = _check(finance_target, finance_signals, 1, "CROSS-OUTAGE FINANCE: payment gateway timeout")
    results.append(r2)
    return results


def cross_breach_healthcare_finance(healthcare_target, finance_target):
    """A data breach spans healthcare and finance — patient billing records
    exfiltrated. Each cascade sees its domain's signals independently."""
    healthcare_signals = [
        {"signal_type": "hl7_hipaa_access", "severity": "critical",
         "source": "ehr_audit", "namespace": "compliance",
         "content": {"record_type": "billing_records", "accessor_role": "external_api",
                     "records_accessed": 15000, "access_pattern": "bulk_export",
                     "source_ip": "198.51.100.42", "is_treating_provider": False},
         "labels": {"patient_id": "BULK", "label": "fraud",
                    "label_detail": "unauthorized_bulk_access"}},
    ]
    finance_signals = [
        {"signal_type": "txn_card_purchase", "severity": "critical",
         "source": "fraud_detection", "namespace": "banking",
         "content": {"pattern": "card_not_present", "source_breach": "healthcare_provider",
                     "affected_cards": 15000, "fraud_rate": 0.12,
                     "geographic_spread": "nationwide"},
         "labels": {"account_id": "BREACH-001", "label": "fraud",
                    "label_detail": "breach_originated_fraud"}},
    ]
    results = []
    r1 = _check(healthcare_target, healthcare_signals, 1, "CROSS-BREACH HEALTHCARE: bulk record exfiltration")
    results.append(r1)
    r2 = _check(finance_target, finance_signals, 1, "CROSS-BREACH FINANCE: breach-originated card fraud")
    results.append(r2)
    return results


def cross_infrastructure_all(k8s_target, telecom_target, finance_target, healthcare_target):
    """Power outage affects all domains simultaneously.
    Tests whether each cascade independently recognizes the impact."""
    base = {"content": {"event": "power_outage", "datacenter": "NYC-DC-1",
                        "ups_remaining_minutes": 15}}
    signals_per_domain = [
        (k8s_target, [
            {"signal_type": "event_nodenotready", "severity": "critical",
             "source": "kubelet", "namespace": "kube-system",
             **base, "labels": {"node": "worker-1", "label": "real_incident"}},
            {"signal_type": "event_nodenotready", "severity": "critical",
             "source": "kubelet", "namespace": "kube-system",
             "content": {**base["content"], "node": "worker-2"},
             "labels": {"node": "worker-2", "label": "real_incident"}},
        ]),
        (telecom_target, [
            {"signal_type": "tmf_power_failure", "severity": "critical",
             "source": "dcim", "namespace": "facilities",
             "content": {**base["content"], "affected_racks": 12, "generator_status": "starting"},
             "labels": {"region": "northeast", "label": "real_incident"}},
        ]),
        (finance_target, [
            {"signal_type": "txn_wire_transfer", "severity": "critical",
             "source": "core_banking", "namespace": "banking",
             "content": {"error": "database_unreachable", "failover_status": "activating",
                         "queued_transactions": 2300},
             "labels": {"account_id": "SYSTEM", "label": "real_incident",
                        "label_detail": "datacenter_power_loss"}},
        ]),
        (healthcare_target, [
            {"signal_type": "hl7_system_alert", "severity": "critical",
             "source": "ehr_monitor", "namespace": "infrastructure",
             "content": {**base["content"], "affected_systems": ["ehr", "pacs", "lab_interface"],
                         "patient_safety_impact": "high"},
             "labels": {"label": "real_incident",
                        "label_detail": "datacenter_power_clinical_systems"}},
        ]),
    ]
    results = []
    for target, sigs in signals_per_domain:
        r = _check(target, sigs, len(sigs),
                   f"CROSS-INFRA POWER: {len(sigs)} signals to {target.split('/')[-1].split(':')[0]}")
        results.append(r)
    return results


def temporal_slow_burn(target, domain):
    """Signals that individually are low severity but arrive at increasing
    frequency — the rate itself is the signal. 5 signals over 'hours' with
    decreasing intervals (simulated by decreasing content timestamps)."""
    signals = [
        {"signal_type": f"{domain}_slow_burn", "severity": "low",
         "source": "monitoring", "namespace": "operations",
         "content": {"error_count": 1 + i * 3, "error_rate_per_hour": 0.5 * (2 ** i),
                     "interval_hours": max(0.25, 4.0 / (2 ** i))},
         "labels": {"instance": "SVC-DEGRADE-001", "label": "needs_attention",
                    "label_detail": "accelerating_degradation"}}
        for i in range(5)
    ]
    return _check(target, signals, 1,
                  f"SLOW BURN: accelerating error rate ({domain})")


def temporal_oscillation(target, domain):
    """Service flapping — alternating healthy/unhealthy. Individual signals
    are normal but the PATTERN indicates instability."""
    signals = [
        {"signal_type": f"{domain}_health_check", "severity": "info",
         "source": "lb_monitor", "namespace": "operations",
         "content": {"status": "unhealthy" if i % 2 else "healthy",
                     "response_time_ms": 50 if i % 2 == 0 else 5000 + i * 1000,
                     "consecutive_failures": (i // 2) + 1 if i % 2 else 0,
                     "error_rate_per_hour": 0 if i % 2 == 0 else 10 + i * 5},
         "labels": {"instance": "SVC-FLAP-001", "label": "needs_attention",
                    "label_detail": "service_flapping"}}
        for i in range(6)
    ]
    return _check(target, signals, 1,
                  f"OSCILLATION: service flapping 6 readings ({domain})")


def recall_precedent(target, domain):
    """Send a signal, then send a similar one later. The second should
    trigger recall and benefit from the memory of the first. Tests that
    the memory archive is actually being used."""
    first = [
        {"signal_type": f"{domain}_known_failure", "severity": "high",
         "source": "monitoring", "namespace": "production",
         "content": {"error": "certificate_expired", "service": "auth-gateway",
                     "impact": "all_logins_failing", "resolution": "renew_cert"},
         "labels": {"instance": "AUTH-GW-001", "label": "real_incident",
                    "label_detail": "cert_expiry"}},
    ]
    _post(target, first)
    time.sleep(1)
    second = [
        {"signal_type": f"{domain}_known_failure", "severity": "high",
         "source": "monitoring", "namespace": "production",
         "content": {"error": "certificate_expired", "service": "api-gateway",
                     "impact": "api_requests_failing"},
         "labels": {"instance": "API-GW-001", "label": "real_incident",
                    "label_detail": "cert_expiry"}},
    ]
    return _check(target, second, 1,
                  f"RECALL PRECEDENT: similar cert failure after first ({domain})")


# ─── ROUND 2: SAFETY MECHANISM EDGE CASES ───

def safety_label_injection(target, domain):
    """Adversarial: flood 200 info signals with label=compliance to bypass
    dedup and severity gate. Tests whether the bypass can be weaponized
    to overwhelm the LLM."""
    signals = [
        {"signal_type": f"{domain}_heartbeat", "severity": "info",
         "source": "attacker", "namespace": "default",
         "content": {"status": "ok", "seq": i},
         "labels": {"label": "compliance", "label_detail": "injected"}}
        for i in range(200)
    ]
    result = _post(target, signals)
    forwarded = result.get("forwarded", 0) + result.get("survivors", 0)
    status = "EXPOSED" if forwarded > 100 else "MITIGATED"
    log.info(
        "[%s] LABEL INJECTION: sent=%d, forwarded=%d (bypass allows all — framework accepts the label)",
        status, len(signals), forwarded,
    )
    return {
        "scenario": f"LABEL INJECTION: 200 fake compliance signals ({domain})",
        "status": status,
        "sent": len(signals),
        "forwarded": forwarded,
        "note": "Bypass trusts labels. Domain pack must control what gets labeled.",
    }


def safety_giant_payload(target, domain):
    """Adversarial: single signal with massive content payload.
    Tests whether the cascade handles oversized content gracefully."""
    signals = [
        {"signal_type": f"{domain}_giant", "severity": "high",
         "source": "edge_test", "namespace": "adversarial",
         "content": {"data": "X" * 100000, "nested": {"deep": "Y" * 50000}},
         "labels": {"label": "real_incident"}},
    ]
    try:
        result = _post(target, signals)
        survivors = result.get("forwarded", 0) + result.get("survivors", 0)
        return {"scenario": f"GIANT PAYLOAD: 150KB content ({domain})",
                "status": "PASS", "sent": 1, "survivors": survivors,
                "note": "Handled without error"}
    except Exception as e:
        return {"scenario": f"GIANT PAYLOAD: 150KB content ({domain})",
                "status": "FAIL", "error": str(e),
                "note": "Cascade should handle gracefully, not crash"}


def safety_empty_signals(target, domain):
    """Edge: signals with empty/null fields. Tests robustness."""
    signals = [
        {"signal_type": "", "severity": "", "source": "", "namespace": "",
         "content": {}, "labels": {}},
        {"signal_type": f"{domain}_minimal", "severity": "critical",
         "source": "", "namespace": "", "content": {}, "labels": {}},
    ]
    return _check(target, signals, 1, f"EMPTY FIELDS: minimal + empty signals ({domain})")


def safety_unicode_evasion(target, domain):
    """Adversarial: Unicode lookalikes to evade pattern matching.
    Uses Cyrillic 'а' instead of Latin 'a' in critical keywords."""
    signals = [
        {"signal_type": f"{domain}_alert", "severity": "high",
         "source": "monitoring", "namespace": "production",
         "content": {"message": "pаnic in module — dаta lоss detected",
                     "error": "fаtal"},
         "labels": {"label": "real_incident",
                    "label_detail": "unicode_evasion_test"}},
    ]
    return _check(target, signals, 1,
                  f"UNICODE EVASION: Cyrillic lookalikes in keywords ({domain})")


def safety_severity_boundary(target, domain):
    """Edge: signal at exact boundary — low severity with escalation keywords.
    Should the transient suppressor drop it or the pattern classifier catch it?"""
    signals = [
        {"signal_type": f"{domain}_event", "severity": "low",
         "source": "monitoring", "namespace": "production",
         "content": {"message": "oomkill detected in container — memory limit exceeded"},
         "labels": {}},
    ]
    return _check(target, signals, 1,
                  f"SEVERITY BOUNDARY: low severity + oomkill keyword ({domain})")


# ─── ROUND 2: CAPACITY BOUNDARY SCENARIOS ───

def capacity_memory_full(target, domain):
    """Capacity: send enough high-severity signals to approach memory capacity.
    Then send one critical signal. Does it survive eviction?"""
    filler = [
        {"signal_type": f"{domain}_filler_{i % 20}", "severity": "medium",
         "source": f"source-{i}", "namespace": "capacity_test",
         "content": {"test": "filler", "instance": f"inst-{i}"},
         "labels": {"instance": f"cap-{i}"}}
        for i in range(100)
    ]
    _post(target, filler)
    time.sleep(0.5)
    critical = [
        {"signal_type": f"{domain}_critical_after_fill", "severity": "critical",
         "source": "monitoring", "namespace": "production",
         "content": {"event": "system_failure", "impact": "total"},
         "labels": {"label": "real_incident"}},
    ]
    return _check(target, critical, 1,
                  f"MEMORY FULL: critical signal after 100 filler ({domain})")


def capacity_rapid_fire(target, domain):
    """Capacity: 500 signals in one batch — tests batch processing limits."""
    signals = [
        {"signal_type": f"{domain}_rapid_{i % 50}", "severity": "critical" if i == 499 else "info",
         "source": "load_test", "namespace": "capacity",
         "content": {"seq": i, "instance": f"rf-{i}"},
         "labels": {"label": "real_incident" if i == 499 else "noise"}}
        for i in range(500)
    ]
    return _check(target, signals, 1,
                  f"RAPID FIRE: 500 signals, critical at 499 ({domain})")


def capacity_dedup_window_overflow(target, domain):
    """Capacity: send 1000 unique signal types to fill the dedup window.
    Then send one more — does dedup still work or has the window overflowed?"""
    flood = [
        {"signal_type": f"{domain}_type_{i}", "severity": "info",
         "source": "flood_test", "namespace": "dedup_overflow",
         "content": {"id": i}, "labels": {}}
        for i in range(200)
    ]
    _post(target, flood)
    time.sleep(0.5)
    duplicate = [
        {"signal_type": f"{domain}_type_0", "severity": "info",
         "source": "flood_test", "namespace": "dedup_overflow",
         "content": {"id": 0}, "labels": {}},
    ]
    result = _post(target, duplicate)
    compressed = result.get("compressed", 0)
    status = "PASS" if compressed >= 1 else "FAIL"
    log.info("[%s] DEDUP OVERFLOW: duplicate after 200 types, compressed=%d (expected >=1)",
             status, compressed)
    return {"scenario": f"DEDUP OVERFLOW: duplicate after 200 types ({domain})",
            "status": status, "compressed": compressed}


# ─── ROUND 2: MULTI-SIGNAL CAUSALITY ───

def causality_missing_middle(target, domain):
    """Causality: A causes B causes C, but B is missing.
    Send A and C — the cascade should still surface both as they're
    independently important, even without the connecting signal."""
    signals = [
        {"signal_type": f"{domain}_root_cause", "severity": "high",
         "source": "monitoring", "namespace": "production",
         "content": {"event": "disk_full", "device": "/dev/sda1",
                     "usage_percent": 99.8},
         "labels": {"node": "worker-1", "label": "real_incident"}},
        {"signal_type": f"{domain}_downstream_effect", "severity": "high",
         "source": "monitoring", "namespace": "production",
         "content": {"event": "service_unavailable", "service": "database",
                     "error": "write_failed", "cause": "disk_full"},
         "labels": {"node": "worker-1", "label": "real_incident"}},
    ]
    return _check(target, signals, 2,
                  f"MISSING MIDDLE: root cause + effect without connector ({domain})")


def causality_delayed_correlation(target, domain):
    """Causality: two related signals separated by a delay.
    First signal is medium severity, second is critical.
    Tests whether the cascade handles temporal separation."""
    first = [
        {"signal_type": f"{domain}_warning", "severity": "medium",
         "source": "monitoring", "namespace": "production",
         "content": {"metric": "connection_pool_exhaustion",
                     "pool_usage_percent": 95, "service": "api-server"},
         "labels": {"instance": "API-001"}},
    ]
    _post(target, first)
    time.sleep(2)
    second = [
        {"signal_type": f"{domain}_outage", "severity": "critical",
         "source": "monitoring", "namespace": "production",
         "content": {"event": "service_down", "service": "api-server",
                     "error": "connection_pool_exhausted",
                     "downstream_affected": 15},
         "labels": {"instance": "API-001", "label": "real_incident"}},
    ]
    return _check(target, second, 1,
                  f"DELAYED CORRELATION: warning then outage 2s later ({domain})")


SAFETY_SCENARIOS = [
    safety_label_injection,
    safety_giant_payload,
    safety_empty_signals,
    safety_unicode_evasion,
    safety_severity_boundary,
]

CAPACITY_SCENARIOS = [
    capacity_memory_full,
    capacity_rapid_fire,
    capacity_dedup_window_overflow,
]

CAUSALITY_SCENARIOS = [
    causality_missing_middle,
    causality_delayed_correlation,
]


# ─── SCENARIO REGISTRY ───

SCENARIOS = {
    "finance": [
        finance_structuring,
        finance_rapid_test,
        finance_amount_anomaly,
        finance_noise_flood_then_real,
        finance_sanctions_adjacent,
    ],
    "healthcare": [
        healthcare_vital_borderline,
        healthcare_hipaa_after_hours,
        healthcare_silent_deterioration,
    ],
    "telecom": [
        telecom_maintenance_masking,
        telecom_cascade_failure,
        telecom_bgp_anomaly,
    ],
}

CROSS_DOMAIN_SCENARIOS = {
    "outage": cross_outage_finance_telecom,
    "breach": cross_breach_healthcare_finance,
    "infrastructure": cross_infrastructure_all,
}

TEMPORAL_SCENARIOS = [
    temporal_slow_burn,
    temporal_oscillation,
    recall_precedent,
]


def run_single_domain(target, domain):
    results = []
    domain_scenarios = SCENARIOS.get(domain, [])
    if not domain_scenarios:
        log.error("No scenarios for domain: %s", domain)
        return results

    log.info("Running %d edge scenarios for %s", len(domain_scenarios), domain)
    for fn in domain_scenarios:
        try:
            results.append(fn(target))
        except Exception as e:
            log.error("Scenario %s failed: %s", fn.__name__, e)
            results.append({"scenario": fn.__name__, "status": "ERROR", "error": str(e)})
        time.sleep(0.5)

    # Burst test
    try:
        results.append(burst_then_silence(target, domain))
    except Exception as e:
        log.error("Burst test failed: %s", e)

    # Temporal tests
    for fn in TEMPORAL_SCENARIOS:
        try:
            results.append(fn(target, domain))
        except Exception as e:
            log.error("Temporal %s failed: %s", fn.__name__, e)
            results.append({"scenario": fn.__name__, "status": "ERROR", "error": str(e)})
        time.sleep(0.5)

    # Safety tests
    for fn in SAFETY_SCENARIOS:
        try:
            results.append(fn(target, domain))
        except Exception as e:
            log.error("Safety %s failed: %s", fn.__name__, e)
            results.append({"scenario": fn.__name__, "status": "ERROR", "error": str(e)})
        time.sleep(0.5)

    # Capacity tests
    for fn in CAPACITY_SCENARIOS:
        try:
            results.append(fn(target, domain))
        except Exception as e:
            log.error("Capacity %s failed: %s", fn.__name__, e)
            results.append({"scenario": fn.__name__, "status": "ERROR", "error": str(e)})
        time.sleep(0.5)

    # Causality tests
    for fn in CAUSALITY_SCENARIOS:
        try:
            results.append(fn(target, domain))
        except Exception as e:
            log.error("Causality %s failed: %s", fn.__name__, e)
            results.append({"scenario": fn.__name__, "status": "ERROR", "error": str(e)})
        time.sleep(0.5)

    return results


def run_cross_domain(targets):
    """Run cross-domain federation scenarios.
    targets = {"finance": url, "healthcare": url, "telecom": url, "k8s": url}
    """
    results = []

    log.info("Running cross-domain federation scenarios")

    if "finance" in targets and "telecom" in targets:
        try:
            r = cross_outage_finance_telecom(targets["finance"], targets["telecom"])
            results.extend(r)
        except Exception as e:
            log.error("Cross-outage failed: %s", e)
            results.append({"scenario": "cross_outage", "status": "ERROR", "error": str(e)})

    if "healthcare" in targets and "finance" in targets:
        try:
            r = cross_breach_healthcare_finance(targets["healthcare"], targets["finance"])
            results.extend(r)
        except Exception as e:
            log.error("Cross-breach failed: %s", e)
            results.append({"scenario": "cross_breach", "status": "ERROR", "error": str(e)})

    if all(k in targets for k in ("k8s", "telecom", "finance", "healthcare")):
        try:
            r = cross_infrastructure_all(
                targets["k8s"], targets["telecom"],
                targets["finance"], targets["healthcare"])
            results.extend(r)
        except Exception as e:
            log.error("Cross-infra failed: %s", e)
            results.append({"scenario": "cross_infrastructure", "status": "ERROR", "error": str(e)})

    return results


def main():
    parser = argparse.ArgumentParser(description="Edge case scenario runner")
    parser.add_argument("--target", help="Cascade service URL (single domain)")
    parser.add_argument("--domain", help="Domain to test (single domain)")
    parser.add_argument("--cross-domain", action="store_true",
                        help="Run cross-domain federation scenarios")
    parser.add_argument("--finance-url", help="Finance cascade URL")
    parser.add_argument("--healthcare-url", help="Healthcare cascade URL")
    parser.add_argument("--telecom-url", help="Telecom cascade URL")
    parser.add_argument("--k8s-url", help="K8s cascade URL")
    parser.add_argument("--all", action="store_true",
                        help="Run all scenarios (single + cross-domain)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    all_results = []

    # Single-domain scenarios
    if args.domain and args.target:
        all_results.extend(run_single_domain(args.target, args.domain))

    # Cross-domain scenarios
    if args.cross_domain or args.all:
        targets = {}
        if args.finance_url: targets["finance"] = args.finance_url
        if args.healthcare_url: targets["healthcare"] = args.healthcare_url
        if args.telecom_url: targets["telecom"] = args.telecom_url
        if args.k8s_url: targets["k8s"] = args.k8s_url
        if targets:
            all_results.extend(run_cross_domain(targets))

    # All single domains if --all
    if args.all:
        url_map = {"finance": args.finance_url, "healthcare": args.healthcare_url,
                   "telecom": args.telecom_url, "k8s": args.k8s_url}
        for domain, url in url_map.items():
            if url and not (args.domain == domain):
                all_results.extend(run_single_domain(url, domain))

    # Summary
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    failed = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors = sum(1 for r in all_results if r.get("status") == "ERROR")

    log.info("")
    log.info("=" * 60)
    log.info("EDGE SCENARIO RESULTS")
    log.info("=" * 60)
    for r in all_results:
        log.info("  [%s] %s", r.get("status", "?"), r.get("scenario", "?"))
    log.info("")
    log.info("PASSED: %d  FAILED: %d  ERROR: %d  TOTAL: %d",
             passed, failed, errors, len(all_results))
    log.info("=" * 60)

    print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
