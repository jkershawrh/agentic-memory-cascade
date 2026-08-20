"""Confluence collector — page edits, stale docs, and knowledge signals.

READ-ONLY. GET only. Never create/update/delete pages.

Polls Confluence REST API for recently modified pages to surface
organizational knowledge signals: runbook decay (frequently edited pages),
documentation gaps (stale pages in critical spaces), expertise concentration
(single-author page ownership), and knowledge capture (new pages created
after incidents).
"""

import json
import logging
import os
from base64 import b64encode
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class ConfluenceSignal:
    _counter = 0

    def __init__(self, record: dict):
        ConfluenceSignal._counter += 1
        self.signal_id = ConfluenceSignal._counter
        self.cluster_id = record.get("instance", "confluence")
        self.namespace = record.get("space", "")
        self.resource_kind = "page"
        self.resource_name = record.get("title", "")
        self.signal_type = record.get("signal_type", "page_updated")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {
            "domain": "knowledge",
            "source": "confluence",
            "person": record.get("author", ""),
            "team": record.get("space", ""),
            "topic": record.get("topic", ""),
        }


class ConfluenceCollector(BaseCollector):
    name = "confluence"

    def __init__(self):
        self._base_url = ""
        self._auth_header = ""
        self._spaces: List[str] = []
        self._connected = False
        self._seen_ids: set = set()

    def connect(self, config: dict) -> bool:
        base = (
            config.get("base_url", "")
            or os.getenv("CONFLUENCE_URL", "")
        )
        if not base:
            atlassian = os.getenv("ATLASSIAN_BASE_URL", "")
            if atlassian:
                base = f"{atlassian.rstrip('/')}/wiki"
        self._base_url = base.rstrip("/")
        token = (
            config.get("token", "")
            or os.getenv("CONFLUENCE_TOKEN", "")
            or os.getenv("ATLASSIAN_API_TOKEN", "")
        )
        user = (
            config.get("user", "")
            or os.getenv("CONFLUENCE_USER", "")
            or os.getenv("ATLASSIAN_EMAIL", "")
        )
        password = config.get("password", "") or os.getenv("CONFLUENCE_PASSWORD", "")
        spaces_str = config.get("spaces", "") or os.getenv("CONFLUENCE_SPACES", "")

        if spaces_str:
            self._spaces = [s.strip() for s in spaces_str.split(",") if s.strip()]

        if not self._base_url:
            log.warning("Confluence collector: no CONFLUENCE_URL configured")
            return False

        if token and user:
            creds = b64encode(f"{user}:{token}".encode()).decode()
            self._auth_header = f"Basic {creds}"
        elif token:
            self._auth_header = f"Bearer {token}"
        elif user and password:
            creds = b64encode(f"{user}:{password}".encode()).decode()
            self._auth_header = f"Basic {creds}"

        data = self._get("/api/v2/pages?limit=1")
        if data and "results" in data:
            self._connected = True
            log.info("Confluence connected: %s (%d spaces configured)",
                     self._base_url, len(self._spaces) or -1)
            return True
        return False

    def collect(self) -> list:
        signals = []
        if self._spaces:
            for space in self._spaces:
                signals.extend(self._collect_space(space, limit=50))
        else:
            signals.extend(self._collect_recent(limit=100))
        return signals

    def collect_all(self) -> list:
        signals = []
        if self._spaces:
            for space in self._spaces:
                signals.extend(self._collect_space(space, limit=500))
        else:
            signals.extend(self._collect_recent(limit=1000))
        return signals

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "base_url": self._base_url,
            "spaces": self._spaces or ["all"],
        }

    def _collect_recent(self, limit: int = 100) -> List[ConfluenceSignal]:
        return self._fetch_pages_v2(limit=limit)

    def _collect_space(self, space_key: str, limit: int = 50) -> List[ConfluenceSignal]:
        space_id = self._resolve_space_id(space_key)
        if not space_id:
            return self._fetch_pages_v2(limit=limit)
        return self._fetch_pages_v2(space_id=space_id, limit=limit)

    def _resolve_space_id(self, space_key: str) -> str:
        data = self._get(f"/wiki/rest/api/space?spaceKey={space_key}&limit=1")
        if data:
            results = data.get("results", [])
            if results:
                return str(results[0].get("id", ""))
        return ""

    def _fetch_pages_v2(self, space_id: str = "", limit: int = 100) -> List[ConfluenceSignal]:
        signals = []
        cursor = None
        fetched = 0
        while fetched < limit:
            path = f"/api/v2/pages?limit={min(limit - fetched, 100)}&sort=-modified-date"
            if space_id:
                path += f"&space-id={space_id}"
            if cursor:
                path += f"&cursor={cursor}"
            data = self._get(path)
            if not data:
                break
            results = data.get("results", [])
            if not results:
                break
            for page in results:
                signals.extend(self._page_to_signals(page))
            fetched += len(results)
            links = data.get("_links", {})
            next_link = links.get("next", "")
            if next_link and "cursor=" in next_link:
                cursor = next_link.split("cursor=")[-1].split("&")[0]
            else:
                break
        return signals

    def _page_to_signals(self, page: dict) -> List[ConfluenceSignal]:
        page_id = str(page.get("id", ""))
        if page_id in self._seen_ids:
            return []
        self._seen_ids.add(page_id)

        title = page.get("title", "")
        space = page.get("spaceId", "")
        version = page.get("version", {})
        version_num = version.get("number", 1) if isinstance(version, dict) else 1
        author_id = page.get("authorId", "")
        updated_when = version.get("createdAt", "") if isinstance(version, dict) else ""
        created_date = page.get("createdAt", "")
        updated_by = author_id

        base = {
            "instance": "confluence",
            "page_id": page_id,
            "title": title,
            "space": str(space),
            "author": updated_by,
            "created_by": author_id,
            "version": version_num,
            "updated": updated_when,
            "created": created_date,
        }

        signals = []
        title_lower = title.lower()

        if version_num > 20:
            signals.append(ConfluenceSignal({
                **base,
                "signal_type": "runbook_decay",
                "severity": "medium",
                "message": f"'{title}' in {space} has {version_num} versions — frequent revision suggests instability",
                "topic": "runbook",
            }))
        elif any(w in title_lower for w in ("runbook", "playbook", "sop", "procedure", "troubleshoot")):
            if version_num == 1:
                signals.append(ConfluenceSignal({
                    **base,
                    "signal_type": "documentation_gap",
                    "severity": "medium",
                    "message": f"Runbook '{title}' in {space} created but never updated (v1)",
                    "topic": "documentation",
                }))
            else:
                signals.append(ConfluenceSignal({
                    **base,
                    "signal_type": "runbook_update",
                    "severity": "low",
                    "message": f"{updated_by} updated runbook '{title}' in {space} (v{version_num})",
                    "topic": "runbook",
                }))
        elif any(w in title_lower for w in ("postmortem", "post-mortem", "incident", "rca", "root cause")):
            signals.append(ConfluenceSignal({
                **base,
                "signal_type": "incident_learning",
                "severity": "medium",
                "message": f"Incident doc '{title}' in {space} by {updated_by}",
                "topic": "incident_learning",
            }))
        elif any(w in title_lower for w in ("onboarding", "getting started", "new hire", "setup guide")):
            signals.append(ConfluenceSignal({
                **base,
                "signal_type": "onboarding_doc",
                "severity": "low",
                "message": f"Onboarding doc '{title}' in {space} updated by {updated_by} (v{version_num})",
                "topic": "onboarding",
            }))
        elif any(w in title_lower for w in ("architecture", "design", "adr", "decision")):
            if version_num > 5:
                signals.append(ConfluenceSignal({
                    **base,
                    "signal_type": "decision_revisited",
                    "severity": "medium",
                    "message": f"Architecture doc '{title}' revised {version_num} times — possible design churn",
                    "topic": "architecture",
                }))
            else:
                signals.append(ConfluenceSignal({
                    **base,
                    "signal_type": "architecture_update",
                    "severity": "low",
                    "message": f"{updated_by} updated architecture doc '{title}' in {space}",
                    "topic": "architecture",
                }))
        else:
            signals.append(ConfluenceSignal({
                **base,
                "signal_type": "page_updated",
                "severity": "info",
                "message": f"{updated_by} updated '{title}' in {space} (v{version_num})",
                "topic": "documentation",
            }))

        return signals

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
            log.debug("Confluence GET %s: %s", path[:80], str(e)[:100])
            return None
