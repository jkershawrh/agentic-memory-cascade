"""Git collector — commit patterns, PR reviews, and expertise signals.

READ-ONLY. GET only. Never push/merge/close.

Polls GitHub REST API for commits, pull requests, and reviews to surface
organizational knowledge signals: expertise concentration (single-author
hotspots), hotfix patterns (rapid follow-up commits), code review repeats
(same reviewer comments across PRs), and documentation gaps (docs/ changes
or missing CHANGELOG entries).
"""

import json
import logging
import os
from typing import Iterator, List, Optional, Union
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class GitSignal:
    _counter = 0

    def __init__(self, record: dict):
        GitSignal._counter += 1
        self.signal_id = GitSignal._counter
        self.cluster_id = record.get("instance", "github")
        self.namespace = record.get("repo", "")
        self.resource_kind = record.get("kind", "commit")
        self.resource_name = record.get("ref", "")
        self.signal_type = record.get("signal_type", "regular_commit")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {
            "domain": "knowledge",
            "source": "git",
            "person": record.get("author", ""),
            "team": record.get("org", ""),
            "topic": record.get("topic", ""),
        }


class GitCollector(BaseCollector):
    name = "git"

    def __init__(self):
        self._base_url = "https://api.github.com"
        self._token = ""
        self._org = ""
        self._repos: List[str] = []
        self._connected = False
        self._seen_shas: set = set()
        self._seen_prs: set = set()

    def connect(self, config: dict) -> bool:
        self._base_url = (
            config.get("api_url", "")
            or os.getenv("GITHUB_API_URL", "https://api.github.com")
        ).rstrip("/")
        self._token = (
            config.get("token", "")
            or os.getenv("GITHUB_TOKEN", "")
        )
        self._org = (
            config.get("org", "")
            or os.getenv("GIT_ORG", "")
        )
        repos_str = (
            config.get("repos", "")
            or os.getenv("GIT_REPOS", "")
        )
        if repos_str:
            self._repos = [r.strip() for r in repos_str.split(",") if r.strip()]

        if self._org and not self._repos:
            self._repos = self._discover_org_repos(self._org)

        if not self._repos:
            log.warning("Git collector: no GIT_REPOS or GIT_ORG configured")
            return False

        for repo in self._repos[:3]:
            data = self._get(f"/repos/{repo}")
            if data and data.get("full_name"):
                self._connected = True
                log.info("Git connected: %s (%d repos in %s)",
                         self._base_url, len(self._repos), self._org or "explicit list")
                return True

        return False

    def _discover_org_repos(self, org: str) -> List[str]:
        repos = []
        page = 1
        while True:
            data = self._get(f"/orgs/{org}/repos?per_page=100&type=all&page={page}")
            if not data or not isinstance(data, list) or not data:
                break
            for repo in data:
                if not repo.get("archived", False):
                    repos.append(repo["full_name"])
            if len(data) < 100:
                break
            page += 1
        log.info("Git org discovery: %s has %d non-archived repos", org, len(repos))
        return repos

    def collect(self) -> list:
        signals = []
        for repo in self._repos:
            signals.extend(self._collect_commits(repo, per_page=30))
            signals.extend(self._collect_prs(repo, per_page=20))
        return signals

    def collect_all(self) -> list:
        signals = []
        for repo in self._repos:
            signals.extend(self._collect_commits(repo, per_page=100))
            signals.extend(self._collect_prs(repo, per_page=100))
        return signals

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "api_url": self._base_url,
            "repos": self._repos,
        }

    def _collect_commits(self, repo: str, per_page: int = 30) -> List[GitSignal]:
        data = self._get(f"/repos/{repo}/commits?per_page={per_page}")
        if not data or not isinstance(data, list):
            return []

        org = repo.split("/")[0] if "/" in repo else ""
        signals = []

        for commit in data:
            sha = commit.get("sha", "")[:8]
            if sha in self._seen_shas:
                continue
            self._seen_shas.add(sha)

            commit_data = commit.get("commit", {})
            message = commit_data.get("message", "").split("\n")[0][:200]
            author_obj = commit.get("author") or {}
            author = author_obj.get("login", commit_data.get("author", {}).get("name", "?"))
            date = commit_data.get("author", {}).get("date", "")

            files_data = commit.get("files", [])
            files_changed = len(files_data)

            base = {
                "kind": "commit",
                "instance": "github",
                "repo": repo,
                "org": org,
                "ref": sha,
                "author": author,
                "date": date,
                "files_changed": files_changed,
            }

            msg_lower = message.lower()
            if any(w in msg_lower for w in ("hotfix", "revert", "emergency", "urgent")):
                signals.append(GitSignal({
                    **base,
                    "signal_type": "hotfix_pattern",
                    "severity": "high",
                    "message": f"{author} hotfix in {repo}: {message}",
                    "topic": "hotfix",
                }))
            elif any(w in msg_lower for w in ("doc", "readme", "changelog", "runbook")):
                signals.append(GitSignal({
                    **base,
                    "signal_type": "documentation_update",
                    "severity": "low",
                    "message": f"{author} docs update in {repo}: {message}",
                    "topic": "documentation",
                }))
            elif files_changed > 50:
                signals.append(GitSignal({
                    **base,
                    "signal_type": "large_commit",
                    "severity": "medium",
                    "message": f"{author} large commit ({files_changed} files) in {repo}: {message}",
                    "topic": "code_change",
                }))
            else:
                signals.append(GitSignal({
                    **base,
                    "signal_type": "regular_commit",
                    "severity": "info",
                    "message": f"{author} commit in {repo}: {message}",
                    "topic": "code_change",
                }))

        return signals

    def _collect_prs(self, repo: str, per_page: int = 20) -> List[GitSignal]:
        data = self._get(f"/repos/{repo}/pulls?state=all&sort=updated&per_page={per_page}")
        if not data or not isinstance(data, list):
            return []

        org = repo.split("/")[0] if "/" in repo else ""
        signals = []

        for pr in data:
            pr_num = pr.get("number", 0)
            pr_key = f"{repo}#{pr_num}"
            if pr_key in self._seen_prs:
                continue
            self._seen_prs.add(pr_key)

            title = pr.get("title", "")[:200]
            author = (pr.get("user") or {}).get("login", "?")
            state = pr.get("state", "")
            merged = pr.get("merged_at") is not None
            review_comments = pr.get("review_comments", 0)
            comments = pr.get("comments", 0)
            additions = pr.get("additions", 0)
            deletions = pr.get("deletions", 0)
            updated = pr.get("updated_at", "")

            base = {
                "kind": "pull_request",
                "instance": "github",
                "repo": repo,
                "org": org,
                "ref": f"#{pr_num}",
                "author": author,
                "date": updated,
                "pr_state": state,
                "merged": merged,
                "additions": additions,
                "deletions": deletions,
            }

            total_comments = review_comments + comments
            if total_comments > 20:
                signals.append(GitSignal({
                    **base,
                    "signal_type": "decision_revisited",
                    "severity": "medium",
                    "message": f"PR {pr_key} has {total_comments} comments — possible design churn: {title}",
                    "topic": "decision_churn",
                }))
            elif review_comments > 10:
                signals.append(GitSignal({
                    **base,
                    "signal_type": "code_review_repeat",
                    "severity": "medium",
                    "message": f"PR {pr_key} heavy review ({review_comments} review comments): {title}",
                    "topic": "code_review",
                }))
            elif merged:
                signals.append(GitSignal({
                    **base,
                    "signal_type": "pr_merged",
                    "severity": "info",
                    "message": f"{author} merged {pr_key}: {title}",
                    "topic": "code_change",
                }))
            elif state == "closed":
                signals.append(GitSignal({
                    **base,
                    "signal_type": "pr_abandoned",
                    "severity": "low",
                    "message": f"{author} closed {pr_key} without merge: {title}",
                    "topic": "code_change",
                }))
            else:
                signals.append(GitSignal({
                    **base,
                    "signal_type": "pr_open",
                    "severity": "info",
                    "message": f"{author} opened {pr_key}: {title}",
                    "topic": "code_change",
                }))

        return signals

    def _get(self, path: str) -> Optional[Union[dict, list]]:
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=20) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Git GET %s: %s", path[:80], str(e)[:100])
            return None
