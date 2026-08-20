"""Synthetic telecom signal generator for cascade benchmarking.

Generates TMF621-style telecom events:
- Normal: CDR records, routine alarms, performance metrics, provisioning events
- Incidents: outages, degradation, capacity breaches, security events
- Compliance: lawful intercept, data retention, roaming violations
- Customer: complaints, churn indicators, SLA breaches
"""

import random
from dataclasses import dataclass, asdict
from typing import List


@dataclass
class SyntheticTelecomEvent:
    id: int
    network_element: str
    event_type: str
    region: str
    technology: str
    severity_level: str
    metric_name: str
    metric_value: float
    threshold: float
    affected_subscribers: int
    description: str
    label: str = "normal"
    label_detail: str = ""


REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West", "Central"]
NETWORK_ELEMENTS = [f"NE-{r}-{i:03d}" for r in ["RAN", "CORE", "EDGE", "AGG", "PE"] for i in range(20)]
TECHNOLOGIES = ["5G-NR", "4G-LTE", "GPON", "DWDM", "MPLS", "SD-WAN"]


def generate(count: int = 100_000, seed: int = 42) -> List[SyntheticTelecomEvent]:
    rng = random.Random(seed)
    events = []
    eid = 0

    # Real-world ratios: 3GPP TR 32 (>80% alarm noise), FCC NORS (<1% reportable)
    normal_count = int(count * 0.9700)
    incident_count = max(1, int(count * 0.0150))
    compliance_count = max(1, int(count * 0.0050))
    customer_count = max(1, count - normal_count - incident_count - compliance_count)

    for _ in range(normal_count):
        eid += 1
        etype = rng.choice(["cdr_voice", "cdr_data", "cdr_sms",
                            "perf_metric", "perf_metric", "alarm_clear",
                            "provisioning", "config_backup", "heartbeat"])
        ne = rng.choice(NETWORK_ELEMENTS)
        region = rng.choice(REGIONS)
        tech = rng.choice(TECHNOLOGIES)

        if etype.startswith("cdr"):
            metric, val, thresh = "call_duration" if "voice" in etype else "data_mb", rng.uniform(0.5, 120), 0
            desc = f"Normal {etype.replace('_', ' ')} record"
        elif etype == "perf_metric":
            metric = rng.choice(["cpu_util", "memory_util", "link_util", "latency_ms", "jitter_ms", "packet_loss_pct"])
            if metric in ("cpu_util", "memory_util", "link_util"):
                val, thresh = rng.uniform(15, 65), 80
            elif metric == "latency_ms":
                val, thresh = rng.uniform(5, 25), 50
            elif metric == "jitter_ms":
                val, thresh = rng.uniform(1, 8), 15
            else:
                val, thresh = rng.uniform(0, 0.05), 1.0
            desc = f"{metric}={val:.1f} (threshold={thresh})"
        else:
            metric, val, thresh = etype, 0, 0
            desc = f"Routine {etype.replace('_', ' ')}"

        events.append(SyntheticTelecomEvent(
            id=eid, network_element=ne, event_type=etype,
            region=region, technology=tech, severity_level="normal",
            metric_name=metric, metric_value=round(val, 2),
            threshold=thresh, affected_subscribers=0,
            description=desc, label="normal",
        ))

    for _ in range(incident_count):
        eid += 1
        inc_type = rng.choice(["outage", "degradation", "capacity_breach",
                                "security_event", "fiber_cut", "power_failure"])
        ne = rng.choice(NETWORK_ELEMENTS)
        region = rng.choice(REGIONS)
        subs = rng.randint(100, 50000)

        if inc_type == "outage":
            metric, val, thresh = "availability", 0, 99.99
            desc = f"Complete outage on {ne}, {subs:,} subscribers affected"
            detail = "complete_outage"
        elif inc_type == "degradation":
            metric = rng.choice(["latency_ms", "packet_loss_pct", "throughput_mbps"])
            if metric == "latency_ms":
                val, thresh = rng.uniform(100, 500), 50
            elif metric == "packet_loss_pct":
                val, thresh = rng.uniform(5, 25), 1.0
            else:
                val, thresh = rng.uniform(1, 10), 50
            desc = f"Service degradation: {metric}={val:.1f} (threshold={thresh})"
            detail = f"degradation_{metric}"
            subs = rng.randint(50, 5000)
        elif inc_type == "capacity_breach":
            metric = rng.choice(["cpu_util", "memory_util", "link_util"])
            val, thresh = rng.uniform(92, 100), 80
            desc = f"Capacity breach: {metric}={val:.1f}%"
            detail = f"capacity_{metric}"
            subs = rng.randint(500, 20000)
        elif inc_type == "security_event":
            metric = rng.choice(["ddos_detected", "unauthorized_access", "config_tampering", "anomalous_traffic"])
            val, thresh = 1, 0
            desc = f"Security: {metric} on {ne}"
            detail = f"security_{metric}"
            subs = rng.randint(0, 1000)
        elif inc_type == "fiber_cut":
            metric, val, thresh = "optical_power_dbm", -30, -20
            desc = f"Fiber cut detected on {ne}, optical power {val}dBm"
            detail = "fiber_cut"
            subs = rng.randint(1000, 30000)
        else:
            metric, val, thresh = "power_status", 0, 1
            desc = f"Power failure on {ne}, running on battery backup"
            detail = "power_failure"
            subs = rng.randint(500, 15000)

        events.append(SyntheticTelecomEvent(
            id=eid, network_element=ne, event_type=inc_type,
            region=region, technology=rng.choice(TECHNOLOGIES),
            severity_level="critical" if subs > 10000 else "major",
            metric_name=metric, metric_value=round(val, 2),
            threshold=thresh, affected_subscribers=subs,
            description=desc, label="incident", label_detail=detail,
        ))

    for _ in range(compliance_count):
        eid += 1
        comp_type = rng.choice(["lawful_intercept", "data_retention", "roaming_violation",
                                 "spectrum_violation", "emergency_service_failure"])

        if comp_type == "lawful_intercept":
            desc = rng.choice(["LI provisioning delay >2hr", "Warrant coverage gap detected",
                               "LI handover interface degraded"])
            detail = "lawful_intercept"
        elif comp_type == "data_retention":
            desc = rng.choice(["CDR retention below 18-month requirement",
                               "Data purge executed before retention period",
                               "Backup verification failed for retained records"])
            detail = "data_retention"
        elif comp_type == "roaming_violation":
            desc = "Subscriber roaming in restricted region without proper IREG agreement"
            detail = "roaming_violation"
        elif comp_type == "spectrum_violation":
            desc = "Transmission power exceeds licensed spectrum allocation"
            detail = "spectrum_violation"
        else:
            desc = "911/E911 call routing failure, backup path activated"
            detail = "emergency_service"

        ne = rng.choice(NETWORK_ELEMENTS)
        region = rng.choice(REGIONS)
        events.append(SyntheticTelecomEvent(
            id=eid, network_element=ne,
            event_type=comp_type, region=region,
            technology=rng.choice(TECHNOLOGIES), severity_level="compliance",
            metric_name=comp_type, metric_value=0, threshold=0,
            affected_subscribers=rng.randint(0, 5000),
            description=f"{desc} on {ne} in {region}",
            label="compliance", label_detail=detail,
        ))

    for _ in range(customer_count):
        eid += 1
        cust_type = rng.choice(["complaint", "churn_indicator", "sla_breach",
                                 "billing_dispute", "escalation"])

        if cust_type == "complaint":
            desc = rng.choice(["Dropped calls in coverage area", "Slow data speeds",
                               "Billing overcharge reported", "No service at home address",
                               "Repeated call to support (3rd time)"])
            detail = "customer_complaint"
        elif cust_type == "churn_indicator":
            desc = rng.choice(["Contract end in 30 days, no renewal interest",
                               "Competitor pricing inquiry logged",
                               "Downgrade request submitted",
                               "Port-out request initiated"])
            detail = "churn_risk"
        elif cust_type == "sla_breach":
            desc = f"SLA availability {rng.uniform(98, 99.9):.2f}% vs 99.99% target"
            detail = "sla_breach"
        elif cust_type == "billing_dispute":
            desc = f"Disputed charge ${rng.uniform(20, 500):.2f}, roaming/overage"
            detail = "billing_dispute"
        else:
            desc = "Customer escalation to executive office, repeat issue"
            detail = "executive_escalation"

        events.append(SyntheticTelecomEvent(
            id=eid, network_element="", event_type=cust_type,
            region=rng.choice(REGIONS), technology="",
            severity_level="customer", metric_name="",
            metric_value=0, threshold=0,
            affected_subscribers=1,
            description=desc, label="customer", label_detail=detail,
        ))

    rng.shuffle(events)
    for i, e in enumerate(events):
        e.id = i + 1
    return events


def label_summary(events):
    counts = {}
    for e in events:
        counts[e.label] = counts.get(e.label, 0) + 1
    return {"total": len(events), "by_label": counts}


if __name__ == "__main__":
    evts = generate(100_000)
    s = label_summary(evts)
    print(f"Generated {s['total']} telecom events:")
    for label, count in sorted(s["by_label"].items()):
        print(f"  {label}: {count}")
