"""AAP (Ansible Automation Platform) signal collector.

Queries the AAP controller database for signals across multiple tables:
- main_jobevent: task-level events within playbook runs (2.4M+ rows)
- main_unifiedjob: job-level status (18K rows)
- main_activitystream: inventory/credential/config changes (14K rows)

Maps everything to the cascade Signal protocol. Runs as a polling collector.
"""

import logging
import os
from typing import List

log = logging.getLogger(__name__)

AAP_DB_HOST = os.getenv("AAP_DB_HOST", "")
AAP_DB_PORT = os.getenv("AAP_DB_PORT", "5432")
AAP_DB_USER = os.getenv("AAP_DB_USER", "")
AAP_DB_PASS = os.getenv("AAP_DB_PASS", "")
AAP_DB_NAME = os.getenv("AAP_DB_NAME", "automationcontroller")
AAP_DB_URL = f"postgresql://{AAP_DB_USER}:{AAP_DB_PASS}@{AAP_DB_HOST}:{AAP_DB_PORT}/{AAP_DB_NAME}"


class AAPSignal:
    """Maps any AAP record to the cascade Signal interface."""
    def __init__(self, record: dict, source: str = "job"):
        self.signal_id = record["id"]
        self.cluster_id = "aap-infra01"
        self.resource_kind = source
        self.labels = {"domain": "aap", "source": source}

        if source == "job":
            self._from_job(record)
        elif source == "task":
            self._from_task_event(record)
        elif source == "activity":
            self._from_activity(record)

    def _from_job(self, job):
        self.namespace = self._extract_namespace(job.get("name", ""))
        self.resource_name = job.get("name", "")
        status = job.get("status", "")
        self.signal_type = f"job_{status}" if status else "job_unknown"
        self.severity = self._job_severity(job)
        self.evidence = {
            "message": f"{job.get('name', '')} {status}",
            "job_status": status,
            "failed": job.get("failed", False),
            "elapsed": job.get("elapsed", 0),
            "launch_type": job.get("launch_type", ""),
        }
        self.labels["launch_type"] = job.get("launch_type", "")

    def _from_task_event(self, evt):
        self.namespace = self._extract_namespace(evt.get("job_name", ""))
        self.resource_name = evt.get("task", "") or evt.get("play", "") or ""
        event = evt.get("event", "")
        self.signal_type = f"task_{event}"
        self.severity = self._task_severity(evt)
        self.evidence = {
            "message": f"{evt.get('task', '')} on {evt.get('host_name', '')}",
            "event": event,
            "host": evt.get("host_name", ""),
            "play": evt.get("play", ""),
            "task": evt.get("task", ""),
            "role": evt.get("role", ""),
            "playbook": evt.get("playbook", ""),
            "failed": evt.get("failed", False),
            "changed": evt.get("changed", False),
            "job_id": evt.get("job_id"),
        }
        stdout = evt.get("stdout", "")
        if stdout and evt.get("failed"):
            self.evidence["stdout"] = stdout[:500]

    def _from_activity(self, act):
        self.namespace = "aap-config"
        obj1 = act.get("object1", "")
        obj2 = act.get("object2", "")
        op = act.get("operation", "")
        self.resource_name = f"{obj1} {obj2}".strip()
        self.signal_type = f"config_{op}_{obj1}".replace(" ", "_").lower()
        self.severity = self._activity_severity(act)
        self.evidence = {
            "message": f"{op} {obj1} {obj2}".strip(),
            "operation": op,
            "object1": obj1,
            "object2": obj2,
        }
        changes = act.get("changes", "")
        if changes and op in ("update", "delete"):
            self.evidence["changes"] = changes[:500]

    def _job_severity(self, job):
        if job.get("failed"):
            name = (job.get("name") or "").lower()
            if "prod" in name or "crit" in name:
                return "critical"
            return "high"
        status = job.get("status", "")
        if status == "error":
            return "high"
        if status == "canceled":
            return "medium"
        return "info"

    def _task_severity(self, evt):
        if evt.get("failed"):
            return "high"
        event = evt.get("event", "")
        if event == "warning":
            return "medium"
        if event == "runner_on_skipped":
            return "low"
        return "info"

    def _activity_severity(self, act):
        op = act.get("operation", "")
        obj = act.get("object1", "")
        if op == "delete" and obj in ("credential", "inventory", "project"):
            return "high"
        if op == "update" and obj in ("credential", "inventory"):
            return "medium"
        return "info"

    def _extract_namespace(self, name):
        if not name:
            return "aap-general"
        if name.startswith("["):
            bracket_end = name.find("]")
            if bracket_end > 0:
                return name[1:bracket_end].lower().replace(" ", "-")
        return "aap-general"


class AAPCollector:
    """Polls AAP controller database across jobs, task events, and activity stream."""

    def __init__(self, poll_interval: int = 30):
        self.poll_interval = poll_interval
        self._last_job_id = 0
        self._last_event_id = 0
        self._last_activity_id = 0
        self._conn = None
        self._db_url = AAP_DB_URL

    def connect(self, config: dict) -> bool:
        """Apply an explicit CLI database URL and verify connectivity."""
        self._db_url = config.get("db_url", self._db_url)
        return self._get_conn() is not None

    def _get_conn(self):
        if self._conn:
            try:
                self._conn.cursor().execute("SELECT 1")
                return self._conn
            except Exception:
                self._conn = None
        try:
            import psycopg2
            self._conn = psycopg2.connect(self._db_url)
            self._conn.set_session(readonly=True)
            log.info("AAP collector connected to %s", AAP_DB_HOST)
            return self._conn
        except Exception as e:
            log.warning("AAP collector: can't connect to %s: %s", AAP_DB_HOST, str(e)[:60])
            return None

    def collect(self) -> List[AAPSignal]:
        """Fetch new signals from all sources since last poll."""
        signals = []
        signals.extend(self._collect_jobs())
        signals.extend(self._collect_task_events())
        signals.extend(self._collect_activity())
        if signals:
            log.info("AAP collector: %d new signals (jobs=%d, tasks=%d, activity=%d)",
                     len(signals),
                     sum(1 for s in signals if s.labels.get("source") == "job"),
                     sum(1 for s in signals if s.labels.get("source") == "task"),
                     sum(1 for s in signals if s.labels.get("source") == "activity"))
        elif not self._get_conn():
            log.warning("AAP collector: no DB connection — all sub-collectors returned empty")
        return signals

    def _collect_jobs(self) -> List[AAPSignal]:
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, status, failed, elapsed, launch_type
                FROM main_unifiedjob
                WHERE id > %s ORDER BY id LIMIT 500
            """, (self._last_job_id,))
            signals = []
            for row in cur.fetchall():
                job = dict(zip(("id", "name", "status", "failed", "elapsed", "launch_type"), row))
                job = {k: (v or ("" if k != "failed" else False)) for k, v in job.items()}
                signals.append(AAPSignal(job, source="job"))
                self._last_job_id = max(self._last_job_id, row[0])
            cur.close()
            return signals
        except Exception as e:
            log.warning("AAP jobs error: %s", str(e)[:60])
            return []

    def _collect_task_events(self) -> List[AAPSignal]:
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT e.id, e.event, e.failed, e.changed, e.host_name,
                       e.play, e.role, e.task, e.playbook, e.job_id, e.stdout,
                       j.name as job_name
                FROM main_jobevent e
                LEFT JOIN main_unifiedjob j ON e.job_id = j.id
                WHERE e.id > %s
                AND e.event NOT IN ('playbook_on_start', 'playbook_on_play_start',
                                    'playbook_on_task_start', 'runner_on_start',
                                    'playbook_on_include')
                ORDER BY e.id LIMIT 2000
            """, (self._last_event_id,))
            signals = []
            for row in cur.fetchall():
                evt = dict(zip(("id", "event", "failed", "changed", "host_name",
                                "play", "role", "task", "playbook", "job_id",
                                "stdout", "job_name"), row))
                evt = {k: (v if v is not None else "") for k, v in evt.items()}
                signals.append(AAPSignal(evt, source="task"))
                self._last_event_id = max(self._last_event_id, row[0])
            cur.close()
            return signals
        except Exception as e:
            log.warning("AAP task events error: %s", str(e)[:60])
            return []

    def _collect_activity(self) -> List[AAPSignal]:
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, operation, object1, object2, changes
                FROM main_activitystream
                WHERE id > %s
                AND object1 NOT IN ('o_auth2_access_token')
                ORDER BY id LIMIT 500
            """, (self._last_activity_id,))
            signals = []
            for row in cur.fetchall():
                act = dict(zip(("id", "operation", "object1", "object2", "changes"), row))
                act = {k: (v if v is not None else "") for k, v in act.items()}
                signals.append(AAPSignal(act, source="activity"))
                self._last_activity_id = max(self._last_activity_id, row[0])
            cur.close()
            return signals
        except Exception as e:
            log.warning("AAP activity error: %s", str(e)[:60])
            return []

    def collect_all(self) -> List[AAPSignal]:
        """Fetch ALL historical signals for replay."""
        signals = []
        signals.extend(self._collect_all_jobs())
        signals.extend(self._collect_all_task_events())
        signals.extend(self._collect_all_activity())
        log.info("AAP collector: loaded %d total signals (jobs=%d, tasks=%d, activity=%d)",
                 len(signals),
                 sum(1 for s in signals if s.labels.get("source") == "job"),
                 sum(1 for s in signals if s.labels.get("source") == "task"),
                 sum(1 for s in signals if s.labels.get("source") == "activity"))
        return signals

    def _collect_all_jobs(self) -> List[AAPSignal]:
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, status, failed, elapsed, launch_type
                FROM main_unifiedjob ORDER BY id
            """)
            signals = []
            for row in cur.fetchall():
                job = dict(zip(("id", "name", "status", "failed", "elapsed", "launch_type"), row))
                job = {k: (v or ("" if k != "failed" else False)) for k, v in job.items()}
                signals.append(AAPSignal(job, source="job"))
            cur.close()
            return signals
        except Exception as e:
            log.warning("AAP all jobs error: %s", str(e)[:60])
            return []

    def _collect_all_task_events(self) -> List[AAPSignal]:
        conn = self._get_conn()
        if not conn:
            return []
        try:
            signals = []
            last_id = 0
            page_size = 10000
            while True:
                cur = conn.cursor()
                cur.execute("""
                    SELECT e.id, e.event, e.failed, e.changed, e.host_name,
                           e.play, e.role, e.task, e.playbook, e.job_id, e.stdout,
                           j.name as job_name
                    FROM main_jobevent e
                    LEFT JOIN main_unifiedjob j ON e.job_id = j.id
                    WHERE e.event NOT IN ('playbook_on_start', 'playbook_on_play_start',
                                          'playbook_on_task_start', 'runner_on_start',
                                          'playbook_on_include')
                      AND e.id > %s
                    ORDER BY e.id
                    LIMIT %s
                """, (last_id, page_size))
                rows = cur.fetchall()
                cur.close()
                if not rows:
                    break
                for row in rows:
                    evt = dict(zip(("id", "event", "failed", "changed", "host_name",
                                    "play", "role", "task", "playbook", "job_id",
                                    "stdout", "job_name"), row))
                    evt = {k: (v if v is not None else "") for k, v in evt.items()}
                    signals.append(AAPSignal(evt, source="task"))
                last_id = rows[-1][0]
                if len(rows) < page_size:
                    break
                log.info("AAP task events: loaded %d so far (last_id=%d)", len(signals), last_id)
            return signals
        except Exception as e:
            log.warning("AAP all task events error: %s", str(e)[:60])
            return []

    def _collect_all_activity(self) -> List[AAPSignal]:
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, operation, object1, object2, changes
                FROM main_activitystream
                WHERE object1 NOT IN ('o_auth2_access_token')
                ORDER BY id
            """)
            signals = []
            for row in cur.fetchall():
                act = dict(zip(("id", "operation", "object1", "object2", "changes"), row))
                act = {k: (v if v is not None else "") for k, v in act.items()}
                signals.append(AAPSignal(act, source="activity"))
            cur.close()
            return signals
        except Exception as e:
            log.warning("AAP all activity error: %s", str(e)[:60])
            return []
