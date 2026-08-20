"""AgnosticV/AgnosticD collector — catalog and deployment role changes via GitHub.

READ-ONLY. API reads only. Never push, merge, or modify repositories.

Polls GitHub API for recent commits, PRs, and releases in the agnosticv
(catalog definitions) and agnosticd (deployment roles) repositories.
Config changes here propagate through the entire platform.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)

DEFAULT_REPOS = [
    "rhpds/agnosticv",
    "redhat-cop/agnosticd",
]


class AgnosticVSignal:
    _counter = 0

    def __init__(self, record: dict):
        AgnosticVSignal._counter += 1
        self.signal_id = AgnosticVSignal._counter
        self.cluster_id = "github"
        self.namespace = record.get("repo", "agnosticv")
        self.resource_kind = record.get("kind", "commit")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "agnosticv", "repo": record.get("repo", "")}


class AgnosticVCollector(BaseCollector):
    name = "agnosticv"

    def __init__(self):
        self._token = ""
        self._repos: List[str] = []
        self._connected = False
        self._last_event_ids: dict = {}

    def connect(self, config: dict) -> bool:
        self._token = (
            config.get("github_token", "")
            or os.getenv("GITHUB_TOKEN", "")
        )
        repos_str = config.get("repos", "") or os.getenv("AGNOSTICV_REPOS", "")
        if repos_str:
            self._repos = [r.strip() for r in repos_str.split(",") if r.strip()]
        else:
            self._repos = list(DEFAULT_REPOS)

        for repo in self._repos:
            data = self._gh_get(f"/repos/{repo}")
            if data and data.get("id"):
                self._connected = True
                log.info("AgnosticV connected: %s (%d repos)", repo, len(self._repos))
                return True

        log.warning("AgnosticV: could not connect to any repo")
        return False

    def collect(self) -> list:
        signals = []
        for repo in self._repos:
            signals.extend(self._collect_events(repo))
            signals.extend(self._collect_prs(repo))
        return signals

    def collect_all(self) -> list:
        signals = []
        for repo in self._repos:
            signals.extend(self._collect_commits_history(repo))
        return signals

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "repos": self._repos}

    def _collect_events(self, repo: str) -> List[AgnosticVSignal]:
        signals = []
        data = self._gh_get(f"/repos/{repo}/events?per_page=30")
        if not data:
            return signals

        repo_short = repo.split("/")[-1]
        seen = self._last_event_ids.get(repo, set())
        new_seen = set()

        for event in data:
            eid = event.get("id", "")
            new_seen.add(eid)
            if eid in seen:
                continue

            etype = event.get("type", "")
            payload = event.get("payload", {})
            actor = event.get("actor", {}).get("login", "")

            if etype == "PushEvent":
                commits = payload.get("commits", [])
                if commits:
                    msg = commits[-1].get("message", "")[:100]
                    ref = payload.get("ref", "").replace("refs/heads/", "")
                    signals.append(AgnosticVSignal({
                        "signal_type": "catalog_push" if "agnosticv" in repo else "deploy_role_push",
                        "severity": "low" if ref == "development" else "medium",
                        "kind": "push", "repo": repo_short, "name": f"{actor}/{ref}",
                        "message": f"{len(commits)} commits to {repo_short}/{ref}: {msg}",
                        "branch": ref, "commit_count": len(commits), "author": actor,
                    }))
            elif etype == "PullRequestEvent":
                action = payload.get("action", "")
                pr = payload.get("pull_request", {})
                merged = pr.get("merged", False)
                title = pr.get("title", "")[:100]
                if action == "closed" and merged:
                    signals.append(AgnosticVSignal({
                        "signal_type": "catalog_pr_merged" if "agnosticv" in repo else "deploy_role_pr_merged",
                        "severity": "medium",
                        "kind": "pr", "repo": repo_short, "name": f"PR#{pr.get('number', '')}",
                        "message": f"PR merged in {repo_short}: {title}",
                        "pr_number": pr.get("number"), "author": actor, "title": title,
                    }))
                elif action == "opened":
                    signals.append(AgnosticVSignal({
                        "signal_type": "catalog_pr_opened" if "agnosticv" in repo else "deploy_role_pr_opened",
                        "severity": "info",
                        "kind": "pr", "repo": repo_short, "name": f"PR#{pr.get('number', '')}",
                        "message": f"PR opened in {repo_short}: {title}",
                        "pr_number": pr.get("number"), "author": actor, "title": title,
                    }))
            elif etype == "ReleaseEvent":
                release = payload.get("release", {})
                tag = release.get("tag_name", "")
                signals.append(AgnosticVSignal({
                    "signal_type": "catalog_release" if "agnosticv" in repo else "deploy_role_release",
                    "severity": "medium",
                    "kind": "release", "repo": repo_short, "name": tag,
                    "message": f"Release {tag} published in {repo_short}",
                    "tag": tag, "author": actor,
                }))

        self._last_event_ids[repo] = new_seen
        return signals

    def _collect_prs(self, repo: str) -> List[AgnosticVSignal]:
        signals = []
        data = self._gh_get(f"/repos/{repo}/pulls?state=open&per_page=10")
        if not data:
            return signals

        repo_short = repo.split("/")[-1]
        for pr in data:
            title = pr.get("title", "")[:100]
            number = pr.get("number", 0)
            author = pr.get("user", {}).get("login", "")
            signals.append(AgnosticVSignal({
                "signal_type": "catalog_pr_open" if "agnosticv" in repo else "deploy_role_pr_open",
                "severity": "info",
                "kind": "pr", "repo": repo_short, "name": f"PR#{number}",
                "message": f"Open PR in {repo_short}: #{number} {title}",
                "pr_number": number, "author": author, "title": title,
            }))

        return signals

    def _collect_commits_history(self, repo: str) -> List[AgnosticVSignal]:
        signals = []
        data = self._gh_get(f"/repos/{repo}/commits?per_page=100")
        if not data:
            return signals

        repo_short = repo.split("/")[-1]
        for commit in data:
            sha = commit.get("sha", "")[:8]
            msg = commit.get("commit", {}).get("message", "").split("\n")[0][:100]
            author = commit.get("commit", {}).get("author", {}).get("name", "")
            date = commit.get("commit", {}).get("author", {}).get("date", "")
            signals.append(AgnosticVSignal({
                "signal_type": "catalog_commit" if "agnosticv" in repo else "deploy_role_commit",
                "severity": "info",
                "kind": "commit", "repo": repo_short, "name": sha,
                "message": f"{sha} {msg}",
                "author": author, "date": date,
            }))

        return signals

    def _gh_get(self, path: str) -> Optional:
        url = f"https://api.github.com{path}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        try:
            req = Request(url, headers=headers)
            ctx = ssl.create_default_context()
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("GitHub GET %s: %s", path, str(e)[:100])
            return None
