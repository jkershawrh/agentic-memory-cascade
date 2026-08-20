"""macOS personal activity collector — monitors your work patterns.

Captures: app focus, git activity, file changes, terminal history,
network connections, CPU load. Maps everything to the cascade Signal
protocol. Runs locally, all data stays on your machine.

Usage:
    python -m cascade_compression.collector_sidecar \
        --mode macos \
        --target http://localhost:8090 \
        --interval 30
"""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)


class MacOSSignal:
    """Maps macOS activity observations to the cascade Signal interface."""
    _counter = 0

    def __init__(self, record: dict):
        MacOSSignal._counter += 1
        self.signal_id = MacOSSignal._counter
        self.cluster_id = "local"
        self.namespace = record.get("category", "macos")
        self.resource_kind = record.get("type", "observation")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = record.get("evidence", {})
        self.labels = {"domain": "macos", "category": record.get("category", "")}


class MacOSCollector:
    """Polls macOS system state and produces activity signals."""

    name = "macos"

    def __init__(self, work_dirs: List[str] = None):
        self._last_git_hash = ""
        self._last_app = ""
        self._app_start_time = time.monotonic()
        self._switch_count = 0
        self._last_switch_reset = time.monotonic()
        self._work_dirs = work_dirs or self._find_work_dirs()

    def connect(self, config: dict) -> bool:
        return True

    def collect(self) -> List[MacOSSignal]:
        signals = []
        signals.extend(self._collect_active_app())
        signals.extend(self._collect_git_activity())
        signals.extend(self._collect_file_changes())
        signals.extend(self._collect_system_load())
        signals.extend(self._collect_network())
        signals.extend(self._collect_process_activity())
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def _run(self, cmd: str, timeout: int = 5) -> str:
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _find_work_dirs() -> List[str]:
        home = str(Path.home())
        docs = os.path.join(home, "Documents")
        dirs = []
        if os.path.isdir(docs):
            for d in os.listdir(docs):
                full = os.path.join(docs, d)
                if os.path.isdir(os.path.join(full, ".git")):
                    dirs.append(full)
        return dirs[:10]

    # ── Active Application ──────────────────────────────────────────

    def _collect_active_app(self) -> List[MacOSSignal]:
        signals = []
        app = self._run(
            """osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null"""
        )
        if not app:
            return signals

        now = time.monotonic()

        if app != self._last_app and self._last_app:
            duration = round(now - self._app_start_time)
            self._switch_count += 1

            if duration > 0:
                signals.append(MacOSSignal({
                    "signal_type": "app_focus_end",
                    "category": "focus",
                    "name": self._last_app,
                    "severity": "info",
                    "evidence": {
                        "message": f"Left {self._last_app} after {duration}s, switched to {app}",
                        "app": self._last_app,
                        "duration_seconds": duration,
                        "switched_to": app,
                    },
                }))

            self._app_start_time = now

        if now - self._last_switch_reset > 300:
            if self._switch_count > 20:
                signals.append(MacOSSignal({
                    "signal_type": "high_context_switching",
                    "category": "focus",
                    "name": "context_switches",
                    "severity": "medium",
                    "evidence": {
                        "message": f"{self._switch_count} app switches in 5 minutes",
                        "switches": self._switch_count,
                        "window_seconds": 300,
                        "rate_per_minute": round(self._switch_count / 5, 1),
                    },
                }))
            self._switch_count = 0
            self._last_switch_reset = now

        self._last_app = app

        signals.append(MacOSSignal({
            "signal_type": f"app_active_{app.lower().replace(' ', '_')}",
            "category": "focus",
            "name": app,
            "severity": "info",
            "evidence": {
                "message": f"Active app: {app}",
                "app": app,
            },
        }))

        return signals

    # ── Git Activity ────────────────────────────────────────────────

    def _collect_git_activity(self) -> List[MacOSSignal]:
        signals = []
        for work_dir in self._work_dirs:
            repo_name = os.path.basename(work_dir)
            head = self._run(f"git -C '{work_dir}' rev-parse HEAD 2>/dev/null")
            if not head:
                continue

            recent = self._run(
                f"git -C '{work_dir}' log --oneline --since='5 minutes ago' 2>/dev/null"
            )
            if recent:
                commits = [l for l in recent.split("\n") if l.strip()]
                if commits:
                    diff_stat = self._run(
                        f"git -C '{work_dir}' diff --shortstat HEAD~{len(commits)}..HEAD 2>/dev/null"
                    )
                    signals.append(MacOSSignal({
                        "signal_type": "git_commits",
                        "category": "code",
                        "name": repo_name,
                        "severity": "low",
                        "evidence": {
                            "message": f"{len(commits)} commits in {repo_name}",
                            "repo": repo_name,
                            "commit_count": len(commits),
                            "diff_stat": diff_stat,
                        },
                    }))

            status = self._run(f"git -C '{work_dir}' status --porcelain 2>/dev/null")
            if status:
                changed = len([l for l in status.split("\n") if l.strip()])
                if changed > 20:
                    signals.append(MacOSSignal({
                        "signal_type": "git_large_uncommitted",
                        "category": "code",
                        "name": repo_name,
                        "severity": "medium",
                        "evidence": {
                            "message": f"{changed} uncommitted changes in {repo_name}",
                            "repo": repo_name,
                            "uncommitted_files": changed,
                        },
                    }))

        return signals

    # ── File Changes ────────────────────────────────────────────────

    def _collect_file_changes(self) -> List[MacOSSignal]:
        signals = []
        for work_dir in self._work_dirs:
            repo_name = os.path.basename(work_dir)
            recent = self._run(
                f"find '{work_dir}' -name '*.py' -newer /tmp -maxdepth 4 2>/dev/null | wc -l"
            )
            try:
                count = int(recent.strip())
            except (ValueError, AttributeError):
                count = 0

            if count > 50:
                signals.append(MacOSSignal({
                    "signal_type": "high_file_churn",
                    "category": "code",
                    "name": repo_name,
                    "severity": "medium",
                    "evidence": {
                        "message": f"{count} Python files modified in {repo_name}",
                        "repo": repo_name,
                        "files_modified": count,
                    },
                }))

        return signals

    # ── System Load ─────────────────────────────────────────────────

    def _collect_system_load(self) -> List[MacOSSignal]:
        signals = []

        load = self._run("sysctl -n vm.loadavg 2>/dev/null")
        if load:
            parts = load.replace("{", "").replace("}", "").split()
            try:
                load_1m = float(parts[0])
                cpu_count = int(self._run("sysctl -n hw.ncpu 2>/dev/null") or "8")
                load_pct = round(load_1m / cpu_count * 100)

                severity = "info"
                if load_pct > 90:
                    severity = "high"
                elif load_pct > 70:
                    severity = "medium"

                signals.append(MacOSSignal({
                    "signal_type": "system_load",
                    "category": "system",
                    "name": "cpu",
                    "severity": severity,
                    "evidence": {
                        "message": f"System load: {load_1m:.1f} ({load_pct}% of {cpu_count} cores)",
                        "load_1m": load_1m,
                        "load_percent": load_pct,
                        "cpu_count": cpu_count,
                    },
                }))
            except (IndexError, ValueError):
                pass

        mem = self._run("vm_stat 2>/dev/null")
        if mem:
            try:
                pages_free = int(re.search(r'Pages free:\s+(\d+)', mem).group(1))
                pages_active = int(re.search(r'Pages active:\s+(\d+)', mem).group(1))
                pages_wired = int(re.search(r'Pages wired down:\s+(\d+)', mem).group(1))
                total = pages_free + pages_active + pages_wired
                used_pct = round((pages_active + pages_wired) / total * 100) if total else 0

                severity = "info"
                if used_pct > 90:
                    severity = "high"
                elif used_pct > 80:
                    severity = "medium"

                signals.append(MacOSSignal({
                    "signal_type": "memory_usage",
                    "category": "system",
                    "name": "memory",
                    "severity": severity,
                    "evidence": {
                        "message": f"Memory: {used_pct}% used",
                        "memory_percent": used_pct,
                    },
                }))
            except (AttributeError, ValueError):
                pass

        return signals

    # ── Network ─────────────────────────────────────────────────────

    def _collect_network(self) -> List[MacOSSignal]:
        signals = []
        conns = self._run("netstat -an 2>/dev/null | grep ESTABLISHED | wc -l")
        try:
            count = int(conns.strip())
            severity = "info"
            if count > 100:
                severity = "medium"
            signals.append(MacOSSignal({
                "signal_type": "network_connections",
                "category": "network",
                "name": "established",
                "severity": severity,
                "evidence": {
                    "message": f"{count} established connections",
                    "connection_count": count,
                },
            }))
        except (ValueError, AttributeError):
            pass

        return signals

    # ── Process Activity ────────────────────────────────────────────

    def _collect_process_activity(self) -> List[MacOSSignal]:
        signals = []
        top_procs = self._run(
            "ps aux --sort=-%cpu 2>/dev/null | head -5 | awk '{print $11, $3}'"
        )
        if top_procs:
            for line in top_procs.split("\n"):
                parts = line.strip().split()
                if len(parts) >= 2:
                    proc = os.path.basename(parts[0])
                    try:
                        cpu = float(parts[1])
                    except ValueError:
                        continue
                    if cpu > 50:
                        signals.append(MacOSSignal({
                            "signal_type": "high_cpu_process",
                            "category": "system",
                            "name": proc,
                            "severity": "medium",
                            "evidence": {
                                "message": f"{proc} using {cpu}% CPU",
                                "process": proc,
                                "cpu_percent": cpu,
                            },
                        }))

        return signals
