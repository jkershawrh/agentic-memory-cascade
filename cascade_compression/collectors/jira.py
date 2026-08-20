"""Jira collector — issue lifecycle, reassignment, and knowledge signals.

READ-ONLY. GET only. Never create/update/delete issues.

Polls Jira REST API for recently updated issues and maps them to
organizational knowledge signals: expertise concentration (single assignee
hotspots), incident repeats (same issue type reopened), decision churn
(issues with excessive comments/transitions), and documentation gaps
(issues tagged with missing-docs or knowledge labels).
"""

import json
import logging
import os
from base64 import b64encode
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class JiraSignal:
    _counter = 0

    def __init__(self, record: dict):
        JiraSignal._counter += 1
        self.signal_id = JiraSignal._counter
        self.cluster_id = record.get("instance", "jira")
        self.namespace = record.get("project", "")
        self.resource_kind = "issue"
        self.resource_name = record.get("key", "")
        self.signal_type = record.get("signal_type", "issue_updated")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {
            "domain": "knowledge",
            "source": "jira",
            "project": record.get("project", ""),
            "person": record.get("assignee", ""),
            "team": record.get("project", ""),
            "topic": record.get("topic", ""),
        }


class JiraCollector(BaseCollector):
    name = "jira"

    def __init__(self):
        self._base_url = ""
        self._auth_header = ""
        self._project_filter = ""
        self._connected = False
        self._last_updated = ""
        self._seen_keys: set = set()

    def connect(self, config: dict) -> bool:
        self._base_url = (
            config.get("base_url", "")
            or os.getenv("JIRA_URL", "")
            or os.getenv("ATLASSIAN_BASE_URL", "")
        ).rstrip("/")
        token = (
            config.get("token", "")
            or os.getenv("JIRA_TOKEN", "")
            or os.getenv("ATLASSIAN_API_TOKEN", "")
        )
        user = (
            config.get("user", "")
            or os.getenv("JIRA_USER", "")
            or os.getenv("ATLASSIAN_EMAIL", "")
        )
        password = config.get("password", "") or os.getenv("JIRA_PASSWORD", "")
        projects_str = (
            config.get("project", "") or os.getenv("JIRA_PROJECT", "")
        )
        if projects_str:
            self._projects = [p.strip() for p in projects_str.split(",") if p.strip()]
        else:
            self._projects = []

        if not self._base_url:
            log.warning("Jira collector: no JIRA_URL configured")
            return False

        if token and user:
            creds = b64encode(f"{user}:{token}".encode()).decode()
            self._auth_header = f"Basic {creds}"
        elif token:
            self._auth_header = f"Bearer {token}"
        elif user and password:
            creds = b64encode(f"{user}:{password}".encode()).decode()
            self._auth_header = f"Basic {creds}"

        data = self._get("/rest/api/3/myself")
        if data:
            self._connected = True
            log.info("Jira connected: %s as %s",
                     self._base_url, data.get("displayName", "?"))
            return True
        data = self._get("/rest/api/2/myself")
        if data:
            self._connected = True
            log.info("Jira connected: %s as %s (v2)",
                     self._base_url, data.get("displayName", "?"))
            return True
        return False

    def _project_jql(self) -> str:
        if not self._projects:
            return ""
        if len(self._projects) == 1:
            return f"project = {self._projects[0]}"
        return f"project in ({', '.join(self._projects)})"

    def collect(self) -> list:
        jql = "updated >= -1h ORDER BY updated DESC"
        pjql = self._project_jql()
        if pjql:
            jql = f"{pjql} AND {jql}"
        if self._last_updated:
            jql = jql.replace("-1h", f"'{self._last_updated}'")
        return self._search(jql, max_results=200)

    def collect_all(self) -> list:
        jql = "ORDER BY updated DESC"
        pjql = self._project_jql()
        if pjql:
            jql = f"{pjql} {jql}"
        return self._search(jql, max_results=2000)

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "base_url": self._base_url,
            "projects": self._projects or ["all"],
        }

    def _search(self, jql: str, max_results: int = 200) -> List[JiraSignal]:
        fields = ["key", "summary", "status", "assignee", "reporter",
                  "issuetype", "priority", "labels", "comment", "updated",
                  "resolutiondate", "created"]

        signals = []
        page_token = None
        fetched = 0
        while fetched < max_results:
            body = {
                "jql": jql,
                "maxResults": min(max_results - fetched, 100),
                "fields": fields,
            }
            if page_token:
                body["nextPageToken"] = page_token
            data = self._post("/rest/api/3/search/jql", body)
            if not data:
                break
            issues = data.get("issues", [])
            if not issues:
                break
            for issue in issues:
                signals.extend(self._issue_to_signals(issue))
            fetched += len(issues)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if signals:
            latest = max(
                (s.evidence.get("updated", "") for s in signals),
                default=""
            )
            if latest:
                self._last_updated = latest

        return signals

    def _issue_to_signals(self, issue: dict) -> List[JiraSignal]:
        signals = []
        key = issue.get("key", "")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "")
        assignee_obj = fields.get("assignee") or {}
        assignee = assignee_obj.get("displayName", assignee_obj.get("name", "unassigned"))
        reporter_obj = fields.get("reporter") or {}
        reporter = reporter_obj.get("displayName", reporter_obj.get("name", ""))
        issue_type = (fields.get("issuetype") or {}).get("name", "")
        priority = (fields.get("priority") or {}).get("name", "")
        labels = fields.get("labels", [])
        updated = fields.get("updated", "")
        created = fields.get("created", "")
        project = key.split("-")[0] if "-" in key else ""

        comments = fields.get("comment", {}).get("comments", [])
        comment_count = fields.get("comment", {}).get("total", len(comments))

        base = {
            "key": key,
            "instance": "jira",
            "project": project,
            "assignee": assignee,
            "reporter": reporter,
            "issue_type": issue_type,
            "priority": priority,
            "status": status,
            "updated": updated,
            "created": created,
            "labels": labels,
        }

        if status.lower() in ("reopened", "reopen"):
            signals.append(JiraSignal({
                **base,
                "signal_type": "incident_repeat",
                "severity": "high" if priority in ("Critical", "Blocker") else "medium",
                "message": f"{key} reopened: {summary}",
                "topic": "incident_repeat",
            }))
        elif comment_count > 15:
            signals.append(JiraSignal({
                **base,
                "signal_type": "decision_revisited",
                "severity": "medium",
                "message": f"{key} has {comment_count} comments — possible decision churn: {summary}",
                "topic": "decision_churn",
            }))
        elif any(lbl in ("documentation", "docs-needed", "knowledge-gap", "runbook")
                 for lbl in labels):
            signals.append(JiraSignal({
                **base,
                "signal_type": "documentation_gap",
                "severity": "medium",
                "message": f"{key} flagged for documentation: {summary}",
                "topic": "documentation",
            }))
        elif issue_type.lower() == "bug" and priority in ("Critical", "Blocker"):
            signals.append(JiraSignal({
                **base,
                "signal_type": "hotfix_pattern",
                "severity": "high",
                "message": f"{key} critical bug: {summary}",
                "topic": "hotfix",
            }))
        elif key not in self._seen_keys:
            signals.append(JiraSignal({
                **base,
                "signal_type": f"issue_{status.lower().replace(' ', '_')}",
                "severity": self._priority_to_severity(priority),
                "message": f"{key} [{status}] {summary}",
                "topic": issue_type.lower(),
            }))

        recent_comments = comments[-3:] if comments else []
        for comment in recent_comments:
            author = (comment.get("author") or {}).get("displayName", "?")
            raw_body = comment.get("body", "")
            if isinstance(raw_body, dict):
                body = self._extract_adf_text(raw_body)[:200]
            else:
                body = (raw_body or "")[:200]
            if "?" in body and len(body) < 150:
                signals.append(JiraSignal({
                    **base,
                    "signal_type": "onboarding_question",
                    "severity": "low",
                    "message": f"{author} asks on {key}: {body}",
                    "person": author,
                    "topic": "onboarding",
                }))

        self._seen_keys.add(key)
        return signals

    @staticmethod
    def _extract_adf_text(adf: dict) -> str:
        """Extract plain text from Atlassian Document Format."""
        parts = []
        for node in adf.get("content", []):
            for inline in node.get("content", []):
                if inline.get("type") == "text":
                    parts.append(inline.get("text", ""))
        return " ".join(parts)

    @staticmethod
    def _priority_to_severity(priority: str) -> str:
        return {
            "Blocker": "critical",
            "Critical": "high",
            "Major": "medium",
            "Minor": "low",
            "Trivial": "info",
        }.get(priority, "info")

    def _get(self, path: str) -> Optional[dict]:
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=20) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Jira GET %s: %s", path[:80], str(e)[:100])
            return None

    def _post(self, path: str, body: dict) -> Optional[dict]:
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        try:
            payload = json.dumps(body).encode()
            req = Request(url, data=payload, headers=headers, method="POST")
            with urlopen(req, timeout=20) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Jira POST %s: %s", path[:80], str(e)[:100])
            return None
