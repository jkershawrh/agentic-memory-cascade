"""Agentic Memory Cascade — Demo Dashboard.

Gradio UI that visualizes memory formation in real time. Polls the
FastAPI service at SCORER_URL (default http://localhost:8090) and
displays signal stream, active agents, compression stats, and audit trail.

    SCORER_URL=http://localhost:8090 python src/ui.py
"""

import os
from datetime import datetime, timezone

import gradio as gr
import httpx

SERVICE_URL = os.environ.get("SCORER_URL", "http://localhost:8090")
CLIENT = httpx.Client(base_url=SERVICE_URL, timeout=5)

SEVERITY_EMOJI = {
    "info": "\U0001f7e2",
    "low": "\U0001f7e2",
    "medium": "\U0001f7e1",
    "high": "\U0001f7e0",
    "critical": "\U0001f534",
}

AI_DISCLAIMER = (
    "⚠️ Classification decisions shown here are AI-assisted. "
    "Verify critical outcomes independently."
)


def _get(path):
    try:
        r = CLIENT.get(path)
        return r.json()
    except Exception:
        return None


def _fetch_signal_stream():
    stats = _get("/stats")
    if not stats or stats.get("status") == "not_ready":
        return "Waiting for service...", []

    s = stats.get("stats", {})
    processed = s.get("signals_processed", 0)
    compressed = s.get("cascade_handled", 0)
    ratio = s.get("compression_ratio", 0)
    summary = f"**{processed:,}** signals processed, **{compressed:,}** compressed (**{ratio:.1%}**)"

    promo = stats.get("promotion_log", [])
    rows = []
    for entry in reversed(promo[-30:]):
        ts = entry.get("timestamp", "")[:19]
        event = entry.get("event", entry.get("event_type", ""))
        agent = entry.get("agent", entry.get("agent_name", ""))
        tier = entry.get("tier", "")
        sev = entry.get("severity", "info")
        emoji = SEVERITY_EMOJI.get(sev, "⚪")
        verdict = event
        if tier:
            verdict = f"{event} -> {tier}"
        rows.append([ts, agent[:80], emoji, verdict, tier])

    return summary, rows


def _fetch_memory_map():
    agents_data = _get("/agents")
    if not agents_data:
        return [], "Waiting for service..."

    activated = agents_data.get("activated", [])
    discovered = agents_data.get("discovered", [])
    promo_log = agents_data.get("promotion_log", [])

    rows = []
    for agent_name in activated:
        entry = next(
            (e for e in reversed(promo_log) if e.get("agent") == agent_name),
            {},
        )
        rows.append([
            agent_name,
            entry.get("pattern", entry.get("pattern_type", "")),
            "nano (active)",
            str(entry.get("samples", "")),
            "0%",
            entry.get("timestamp", "")[:19],
            entry.get("ttl_remaining", ""),
        ])

    for d in discovered:
        name = d.get("agent_name", d.get("name", ""))
        if name not in activated:
            rows.append([
                name,
                d.get("pattern", d.get("pattern_type", "")),
                d.get("tier", "draft"),
                str(d.get("samples", d.get("sample_count", ""))),
                "",
                "",
                "",
            ])

    log_lines = []
    for entry in reversed(promo_log[-20:]):
        ts = entry.get("timestamp", "")[:19]
        event = entry.get("event", entry.get("event_type", ""))
        agent = entry.get("agent", entry.get("agent_name", ""))
        tier = entry.get("tier", "")
        reason = entry.get("reason", "")
        line = f"[{ts}] {event}: {agent}"
        if tier:
            line += f" (tier={tier})"
        if reason:
            line += f" reason={reason}"
        log_lines.append(line)

    return rows, "\n".join(log_lines) if log_lines else "No promotion events yet."


def _fetch_compression():
    stats = _get("/stats")
    if not stats or stats.get("status") == "not_ready":
        return 0, 0, 0, 0.0, 0, 0

    s = stats.get("stats", {})
    llm = stats.get("llm", {})

    processed = s.get("signals_processed", 0)
    compressed = s.get("cascade_handled", 0)
    forwarded = s.get("cascade_forwarded", 0)
    ratio = s.get("compression_ratio", 0.0)
    llm_calls = llm.get("classified", 0)
    agents_active = len(stats.get("activated_agents", []))

    return processed, compressed, forwarded, ratio, llm_calls, agents_active


def _fetch_audit():
    agents_data = _get("/agents")
    if not agents_data:
        return "Waiting for service..."

    promo_log = agents_data.get("promotion_log", [])

    lines = []
    for entry in reversed(promo_log[-50:]):
        ts = entry.get("timestamp", "")[:19]
        event = entry.get("event", entry.get("event_type", ""))
        agent = entry.get("agent", entry.get("agent_name", ""))
        tier = entry.get("tier", "")
        reason = entry.get("reason", "")
        samples = entry.get("samples", "")
        fn_rate = entry.get("fn_rate", "")

        line = f"[{ts}] {event.upper():>12}  {agent}"
        details = []
        if tier:
            details.append(f"tier={tier}")
        if samples:
            details.append(f"samples={samples}")
        if fn_rate:
            details.append(f"fn_rate={fn_rate}")
        if reason:
            details.append(f"reason={reason}")
        if details:
            line += f"  ({', '.join(details)})"
        lines.append(line)

    return "\n".join(lines) if lines else "No audit events yet."


with gr.Blocks(
    title="Agentic Memory Cascade",
    theme=gr.themes.Soft(),
) as demo:
    gr.Markdown("# Agentic Memory Cascade")
    gr.Markdown("Watch AI agents form governed institutional memory in real time.")

    with gr.Tabs():
        # -- Tab 1: Signal Stream --
        with gr.TabItem("Signal Stream"):
            stream_summary = gr.Markdown("Waiting for service...")
            stream_table = gr.Dataframe(
                headers=["Time", "Signal", "Sev", "Verdict", "Tier"],
                datatype=["str", "str", "str", "str", "str"],
                label="Recent signals",
                interactive=False,
                wrap=True,
            )

            def refresh_stream():
                summary, rows = _fetch_signal_stream()
                return summary, rows

            stream_timer = gr.Timer(2)
            stream_timer.tick(refresh_stream, outputs=[stream_summary, stream_table])

        # -- Tab 2: Memory Map --
        with gr.TabItem("Memory Map"):
            gr.Markdown("### Active memory agents")
            gr.Markdown("Agents appear here as the cascade discovers patterns and promotes them.")
            agent_table = gr.Dataframe(
                headers=["Agent", "Pattern", "Tier", "Samples", "FN Rate", "Activated", "TTL"],
                datatype=["str", "str", "str", "str", "str", "str", "str"],
                label="Memory agents",
                interactive=False,
                wrap=True,
            )
            promo_log_box = gr.Textbox(
                label="Promotion log (last 20 events)",
                lines=12,
                interactive=False,
            )

            def refresh_memory():
                rows, log = _fetch_memory_map()
                return rows, log

            mem_timer = gr.Timer(3)
            mem_timer.tick(refresh_memory, outputs=[agent_table, promo_log_box])

        # -- Tab 3: Compression Dashboard --
        with gr.TabItem("Compression"):
            gr.Markdown("### Compression metrics")
            with gr.Row():
                n_processed = gr.Number(label="Signals processed", value=0, interactive=False)
                n_compressed = gr.Number(label="Compressed (noise)", value=0, interactive=False)
                n_forwarded = gr.Number(label="Survived (attention)", value=0, interactive=False)
            with gr.Row():
                n_ratio = gr.Number(label="Compression ratio", value=0, precision=3, interactive=False)
                n_llm = gr.Number(label="LLM classifications", value=0, interactive=False)
                n_agents = gr.Number(label="Active agents", value=0, interactive=False)

            def refresh_compression():
                p, c, f, r, l, a = _fetch_compression()
                return p, c, f, r, l, a

            comp_timer = gr.Timer(2)
            comp_timer.tick(
                refresh_compression,
                outputs=[n_processed, n_compressed, n_forwarded, n_ratio, n_llm, n_agents],
            )

        # -- Tab 4: Audit Trail --
        with gr.TabItem("Audit Trail"):
            gr.Markdown(AI_DISCLAIMER)
            gr.Markdown("### Memory decision log")
            audit_box = gr.Textbox(
                label="Audit trail (most recent first)",
                lines=25,
                interactive=False,
                show_copy_button=True,
            )

            def refresh_audit():
                return _fetch_audit()

            audit_timer = gr.Timer(5)
            audit_timer.tick(refresh_audit, outputs=[audit_box])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
