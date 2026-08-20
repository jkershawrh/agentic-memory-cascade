"""Synthetic signal feeder — generates domain signals and POSTs to a cascade service.

Usage:
    python -m cascade_compression.synthetic_feeder \
        --domain finance \
        --target http://cascade-finance:8090 \
        --count 10000 \
        --batch-size 50 \
        --interval 1
"""

import argparse
import json
import logging
import time
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def _to_signal(domain: str, obj) -> dict:
    if domain == "finance":
        return {
            "signal_type": f"txn_{obj.transaction_type}",
            "severity": _finance_severity(obj),
            "source": obj.channel,
            "namespace": obj.merchant_category,
            "content": {
                "amount": obj.amount,
                "merchant": obj.merchant,
                "country": obj.country,
                "location": obj.location,
                "velocity_1h": obj.velocity_1h,
                "velocity_24h": obj.velocity_24h,
                "is_recurring": obj.is_recurring,
                "is_first_time_merchant": obj.is_first_time_merchant,
                "is_first_time_country": obj.is_first_time_country,
            },
            "labels": {
                "account_id": obj.account_id,
                "label": obj.label,
                "label_detail": obj.label_detail,
            },
        }
    elif domain == "healthcare":
        return {
            "signal_type": getattr(obj, "signal_type", f"hl7_{getattr(obj, 'event_type', 'unknown')}"),
            "severity": getattr(obj, "severity", "info"),
            "source": getattr(obj, "source", "hl7_feed"),
            "namespace": getattr(obj, "department", "general"),
            "content": {k: v for k, v in vars(obj).items()
                        if k not in ("id", "signal_type", "severity", "source", "department")},
            "labels": {"label": getattr(obj, "label", ""), "label_detail": getattr(obj, "label_detail", "")},
        }
    elif domain == "telecom":
        return {
            "signal_type": getattr(obj, "signal_type", f"tmf_{getattr(obj, 'event_type', 'unknown')}"),
            "severity": getattr(obj, "severity", "info"),
            "source": getattr(obj, "source", "network"),
            "namespace": getattr(obj, "region", "default"),
            "content": {k: v for k, v in vars(obj).items()
                        if k not in ("id", "signal_type", "severity", "source", "region")},
            "labels": {"label": getattr(obj, "label", ""), "label_detail": getattr(obj, "label_detail", "")},
        }
    elif domain == "knowledge":
        return {
            "signal_type": obj.signal_type,
            "severity": obj.severity,
            "source": obj.source,
            "namespace": obj.team,
            "content": {"message": obj.content, "topic": obj.topic},
            "labels": {
                "person": obj.person,
                "team": obj.team,
                "topic": obj.topic,
                "label": obj.label,
                "label_detail": obj.label_detail,
            },
        }
    else:
        d = vars(obj) if hasattr(obj, "__dict__") else {"raw": str(obj)}
        return {
            "signal_type": d.pop("signal_type", f"{domain}_signal"),
            "severity": d.pop("severity", "info"),
            "source": d.pop("source", domain),
            "namespace": d.pop("namespace", "default"),
            "content": d,
            "labels": {"domain": domain},
        }


def _finance_severity(txn) -> str:
    if txn.label in ("fraud", "money_laundering"):
        return "critical"
    if txn.label == "compliance":
        return "high"
    if txn.label == "suspicious":
        return "medium"
    if txn.amount > 50000:
        return "medium"
    return "info"


def _generate(domain: str, count: int, seed: int = 42):
    if domain == "finance":
        from cascade_compression.benchmarks.synthetic_finance import generate
    elif domain == "healthcare":
        from cascade_compression.benchmarks.synthetic_healthcare import generate
    elif domain == "telecom":
        from cascade_compression.benchmarks.synthetic_telecom import generate
    elif domain == "insurance":
        from cascade_compression.benchmarks.synthetic_insurance import generate
    elif domain == "retail":
        from cascade_compression.benchmarks.synthetic_retail import generate
    elif domain == "knowledge":
        from cascade_compression.benchmarks.synthetic_knowledge import generate
    else:
        raise ValueError(f"Unknown domain: {domain}")
    return generate(count, seed=seed)


def _post_batch(target: str, signals: list) -> dict:
    data = json.dumps({"signals": signals}).encode()
    req = Request(
        f"{target}/cascade",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser(description="Synthetic signal feeder")
    parser.add_argument("--domain", required=True, help="Domain pack name")
    parser.add_argument("--target", required=True, help="Cascade service URL")
    parser.add_argument("--count", type=int, default=10000, help="Total signals to generate")
    parser.add_argument("--batch-size", type=int, default=50, help="Signals per POST")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between batches")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--loops", type=int, default=1, help="Number of times to loop through data (0=infinite)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    loop = 0
    total_sent = 0
    while args.loops == 0 or loop < args.loops:
        loop += 1
        seed = args.seed + loop - 1
        log.info("Loop %d: generating %d %s signals (seed=%d)...", loop, args.count, args.domain, seed)
        raw = _generate(args.domain, args.count, seed)
        signals = [_to_signal(args.domain, obj) for obj in raw]
        log.info("Generated %d signals. Feeding to %s", len(signals), args.target)
        for i in range(0, len(signals), args.batch_size):
            batch = signals[i : i + args.batch_size]
            try:
                result = _post_batch(args.target, batch)
                total_sent += len(batch)
                handled = result.get("handled", 0)
                forwarded = result.get("forwarded", 0)
                if total_sent % 500 == 0 or i == 0:
                    log.info(
                        "Loop %d | Sent %d/%d (total %d) | batch: %d handled, %d forwarded",
                        loop, i + len(batch), len(signals), total_sent, handled, forwarded,
                    )
            except Exception as e:
                log.warning("POST failed: %s — retrying in 5s", e)
                time.sleep(5)
                continue
            time.sleep(args.interval)
        log.info("Loop %d complete. Total sent: %d", loop, total_sent)

    log.info("Done. %d signals sent across %d loops.", total_sent, loop)


if __name__ == "__main__":
    main()
