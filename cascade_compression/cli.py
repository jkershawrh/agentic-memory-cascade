"""CLI entrypoint for cascade-compression.

Usage:
    cascade-run --domain aap --llm-url https://maas/v1 --llm-key sk-... --db-url postgresql://...
    cascade-replay --domain finance --data transactions.csv --llm-url https://maas/v1
"""

import argparse
import importlib
import logging
import sys
import time


def _collector_config(db_url: str = "", data_path: str = "") -> dict:
    """Build the collector configuration shared by replay inputs."""
    config = {}
    if db_url:
        config["db_url"] = db_url
    if data_path:
        config["data_path"] = data_path
    return config


def _load_domain(name: str):
    try:
        return importlib.import_module(f"cascade_compression.domains.{name}")
    except ImportError:
        print(f"Unknown domain: {name}")
        print("Available: kubernetes, aap, finance")
        sys.exit(1)


def run():
    parser = argparse.ArgumentParser(description="Run cascade compression pipeline")
    parser.add_argument("--domain", required=True, help="Domain pack name (kubernetes, aap, finance)")
    parser.add_argument("--llm-url", default="", help="LLM API base URL")
    parser.add_argument("--llm-key", default="", help="LLM API key")
    parser.add_argument("--llm-model", default="", help="LLM model name")
    parser.add_argument("--db-url", default="", help="Database URL for collector")
    parser.add_argument("--ledger-url", default="", help="Immutable ledger URL (optional)")
    parser.add_argument("--ledger-token", default="", help="Ledger bearer token")
    parser.add_argument("--poll-interval", type=int, default=30, help="Poll interval in seconds")
    parser.add_argument("--state-file", default="", help="Path to save/restore cascade state")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    domain = _load_domain(args.domain)
    from cascade_compression.bridge import CascadeBridge

    bridge = CascadeBridge(
        llm_url=args.llm_url,
        llm_key=args.llm_key,
        llm_model=args.llm_model or getattr(domain, "LLM_MODEL", ""),
        system_prompt=domain.SYSTEM_PROMPT,
        domain=domain.DOMAIN,
        ledger_url=args.ledger_url,
        ledger_token=args.ledger_token,
    )

    collector_cls = domain.COLLECTOR_CLASS
    if not collector_cls:
        print(f"Domain '{args.domain}' has no collector configured")
        sys.exit(1)

    collector = collector_cls()
    config = _collector_config(args.db_url)

    if hasattr(collector, "connect"):
        connected = collector.connect(config)
        if connected is False and args.db_url:
            print("Collector failed to connect to the requested data source")
            sys.exit(1)

    log = logging.getLogger("cascade")
    log.info("Starting cascade for domain=%s, polling every %ds", args.domain, args.poll_interval)

    try:
        while True:
            signals = collector.collect()
            if signals:
                bridge.process(signals)
                stats = bridge.get_stats()
                log.info(
                    "Processed %d signals | compression=%.1f%% | classified=%d | activated=%d",
                    stats["signals_processed"],
                    stats.get("compression_ratio", 0) * 100,
                    bridge.stats.llm_classified,
                    len(bridge._activated_types),
                )
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        log.info("Stopped. Total: %d signals, %.1f%% compression",
                 bridge.stats.signals_processed,
                 bridge.stats.compression_ratio * 100)


def replay():
    parser = argparse.ArgumentParser(description="Replay historical data through cascade")
    parser.add_argument("--domain", required=True, help="Domain pack name")
    parser.add_argument("--llm-url", default="", help="LLM API base URL")
    parser.add_argument("--llm-key", default="", help="LLM API key")
    parser.add_argument("--llm-model", default="", help="LLM model name")
    parser.add_argument("--db-url", default="", help="Database URL for collector")
    parser.add_argument("--data", default="", help="Path to data file (CSV/JSON)")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--state-file", default="", help="Path to save/restore cascade state")
    parser.add_argument("--export-memories", default="", help="Export memories to JSON file on completion")
    parser.add_argument("--consolidate-every", type=int, default=0,
                        help="Run consolidation every N batches (0=disabled)")
    parser.add_argument("--ledger-url", default="")
    parser.add_argument("--ledger-token", default="")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    domain = _load_domain(args.domain)
    from cascade_compression.bridge import CascadeBridge

    import os as _os
    if args.state_file:
        _os.environ["CASCADE_STATE_FILE"] = args.state_file

    bridge = CascadeBridge(
        llm_url=args.llm_url,
        llm_key=args.llm_key,
        llm_model=args.llm_model or getattr(domain, "LLM_MODEL", ""),
        system_prompt=domain.SYSTEM_PROMPT,
        domain=domain.DOMAIN,
        ledger_url=args.ledger_url,
        ledger_token=args.ledger_token,
    )

    collector_cls = domain.COLLECTOR_CLASS
    if not collector_cls:
        print(f"Domain '{args.domain}' has no collector configured")
        sys.exit(1)

    collector = collector_cls()
    config = _collector_config(args.db_url, args.data)

    if hasattr(collector, "connect"):
        connected = collector.connect(config)
        if connected is False and (args.data or args.db_url):
            print("Collector failed to connect to the requested data source")
            sys.exit(1)

    log = logging.getLogger("cascade")
    log.info("Replaying all historical data for domain=%s", args.domain)

    all_signals = collector.collect_all()
    log.info("Loaded %d historical signals", len(all_signals))

    consolidate_every = args.consolidate_every
    batch_num = 0
    for i in range(0, len(all_signals), args.batch_size):
        batch = all_signals[i : i + args.batch_size]
        bridge.process(batch)
        batch_num += 1
        if batch_num % 10 == 0:
            log.info(
                "Progress: %d/%d | compression=%.1f%%",
                min(i + args.batch_size, len(all_signals)),
                len(all_signals),
                bridge.stats.compression_ratio * 100,
            )
        if consolidate_every and batch_num % consolidate_every == 0 and bridge.memory_archive:
            from .cascade.pipeline import CascadePipeline
            from .cascade.agents import default_agents
            bridge.memory_archive.consolidate(
                lambda: CascadePipeline(default_agents()),
                batch_size=1000,
            )
            log.info("Consolidation at batch %d: %d memories, avg strength %.2f",
                     batch_num, bridge.memory_archive.size,
                     bridge.memory_archive.avg_strength)

    stats = bridge.get_stats()
    summary = bridge.get_llm_summary()
    log.info("Replay complete:")
    log.info("  Signals:     %d", stats["signals_processed"])
    log.info("  Compression: %.1f%%", stats["compression_ratio"] * 100)
    log.info("  Classified:  %d (noise: %d, important: %d)",
             summary["classified"], summary["noise"], summary["important"])
    log.info("  Activated:   %d agents", len(summary["activated_types"]))
    if stats["fn_status"] == "measured":
        log.info("  FN:          %d", stats["fn_count"])
    else:
        log.info("  FN:          not measured (no ground-truth feedback)")

    if bridge.memory_archive:
        mem_stats = bridge.memory_archive.stats()
        log.info("  Memories:    %d (avg strength %.3f, evictions %d)",
                 mem_stats["size"], mem_stats["avg_strength"], mem_stats["evictions_total"])

    if args.export_memories and bridge.memory_archive:
        import json as _json
        memories = bridge.memory_archive.export_memories(min_strength=0.1)
        with open(args.export_memories, "w") as f:
            _json.dump(memories, f)
        log.info("Exported %d memories to %s", len(memories.get("memories", [])), args.export_memories)
