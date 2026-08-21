"""Agent memory collector — claim-level extraction.

Extracts individual claims from Claude Code project memories and session files,
maps each claim to the cascade Signal protocol. Designed to surface institutional
knowledge through cross-project deduplication.

Two data sources:
- Claude Code project memories (~/.claude/projects/*/memory/*.md)
- Session files (~/Desktop/sessions/*.md)
"""

import hashlib
import logging
import os
import re
import time
from typing import Any, Dict, List, Tuple

from .base import BaseCollector

log = logging.getLogger(__name__)

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_BOLD_SECTION_RE = re.compile(r"\*\*([^*]+):\*\*\s*(.*?)(?=\n\*\*[^*]+:\*\*|\Z)", re.DOTALL)
_BULLET_RE = re.compile(r"^[\s]*[-*]\s+(.+)", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$", re.MULTILINE)
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{10,}|sha256~[A-Za-z0-9_-]{10,}|"
    r"Bearer\s+[A-Za-z0-9_.-]{20,}|password\s*[:=]\s*\S+)",
    re.IGNORECASE,
)

_RULE_RE = re.compile(
    r"\b(must|never|always|do not|don't|stop doing|explicitly rejected|"
    r"only within|do NOT|requires?|forbidden|prohibited)\b",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(
    r"\b(prefers?|wants?|user explicitly|user's? (?:established|preferred)|"
    r"the user (?:wants|likes|prefers))\b",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(pivot|reframed as|consolidating|strategic decision|"
    r"switched to|migrated? to|chose|selected|adopted)\b",
    re.IGNORECASE,
)
_CAVEAT_RE = re.compile(
    r"\b(needs work|plateaued|waiting for|NOT available|not deployed|"
    r"placeholder|estimated|TODO|FIXME|blocked|pending)\b",
    re.IGNORECASE,
)



def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()[:16]


_TOPIC_PATTERNS = {
    "methodology": re.compile(r"\b(TDD|BDD|CDD|EDD|CBT|rubric|red.green|validation.matrix|contracts?.first|tests?.first)\b", re.IGNORECASE),
    "granite_xeon": re.compile(r"\b(granite|xeon|cpu.infer|intel.xeon|racmaas)\b", re.IGNORECASE),
    "self_contained": re.compile(r"\b(self.contained|no.docker|no.external|no.separate.frontend|sqlite.not.postgres)\b", re.IGNORECASE),
    "terse_prompts": re.compile(r"\b(terse|one.word.only|short.prompt|less.is.more|prompt.tuning)\b", re.IGNORECASE),
    "fail_safe": re.compile(r"\b(over.escalat|safe.failure|never.dismiss|fail.open|dangerous.miss)\b", re.IGNORECASE),
    "no_auto_remediate": re.compile(r"\b(observe.everything|suggest.for|act.only|no.auto.remediat|never.auto)\b", re.IGNORECASE),
    "deterministic": re.compile(r"\b(temperature.0|temp.0|deterministic|reproducib|same.input.same.output)\b", re.IGNORECASE),
    "cascade_framework": re.compile(r"\b(domain.pack|framework.unchanged|zero.framework|no.engine.change|domain.agnostic)\b", re.IGNORECASE),
    "production_gates": re.compile(r"\b(earn.the.right|measurable.gate|production.gate|red.to.gold|green.light)\b", re.IGNORECASE),
    "target_cluster_only": re.compile(r"\b(target.cluster|wrong.cluster|context.drift|use.context)\b", re.IGNORECASE),
    "openshift_deploy": re.compile(r"\b(openshift|ocp|deploy.to|deployed.on|namespace|oc\s+(?:get|exec|apply|login))\b", re.IGNORECASE),
    "immutable_ledger": re.compile(r"\b(immutable.ledger|hash.chain|append.only|decision.record|audit.verdict)\b", re.IGNORECASE),
    "demo_platform": re.compile(r"\b(demo.platform|showroom|catalog.item)\b", re.IGNORECASE),
    "summit_connect": re.compile(r"\b(summit.connect|summit.2026|event.lab|quickstart)\b", re.IGNORECASE),
    "pipeline_routing": re.compile(r"\b(routing.engine|inference.routing|model.select|lane.routing|corpora)\b", re.IGNORECASE),
    "agent_architecture": re.compile(r"\b(multi.agent|agent.promot|agent.discover|nano.agent|micro.agent|macro.agent)\b", re.IGNORECASE),
    "testing_coverage": re.compile(r"\b(\d+\s*tests?|test.suite|test.pass|make\s+test|pytest)\b", re.IGNORECASE),
    "maas_inference": re.compile(r"\b(maas|litellm|vllm|llama.cpp|model.serv)\b", re.IGNORECASE),
    "governance": re.compile(r"\b(gcl|governed.cognitive|falsif|audit.loop|governance)\b", re.IGNORECASE),
    "intel_partnership": re.compile(r"\b(intel.partner|fsi|financial.service|card.issuer)\b", re.IGNORECASE),
    "oauth_security": re.compile(r"\b(oauth.proxy|ose.oauth|bearer.token|sso.token|security.compliance|tls.verif)\b", re.IGNORECASE),
    "branding": re.compile(r"\b(powered.by.intel|intel.xeon.6|no.gaudi|brand|logo.svg|red.hat.logo)\b", re.IGNORECASE),
    "story_first": re.compile(r"\b(business.solution|story.experience|act.based|hero.s.journey|progressive.disclosure|projection.scale)\b", re.IGNORECASE),
    "no_auto_action": re.compile(r"\b(no.auto.remediat|pending.action|never.approved|observe.everything|suggest.for|read.only.diagnostic)\b", re.IGNORECASE),
    "claim_registry": re.compile(r"\b(claim.registr|validation.matrix|every.factual.claim|benchmark.proof)\b", re.IGNORECASE),
    "self_serve_quickstart": re.compile(r"\b(quickstart|self.serve|hands.on|build.understanding|user.constructs|liftoff|onboard)\b", re.IGNORECASE),
    "svg_rendering": re.compile(r"\b(svg|inline.fill|css.class|viewbox|defs.style)\b", re.IGNORECASE),
}


def _extract_topics(text: str) -> frozenset:
    topics = set()
    for topic, pattern in _TOPIC_PATTERNS.items():
        if pattern.search(text):
            topics.add(topic)
    return frozenset(topics)


def _trigram_hash(text: str) -> str:
    """Near-dedup via sorted trigram set — catches reordered acronyms."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < 3:
        return _content_hash(text)
    trigrams = set()
    for i in range(len(words) - 2):
        tri = tuple(sorted(words[i:i + 3]))
        trigrams.add(tri)
    return hashlib.sha256(str(sorted(trigrams)).encode()).hexdigest()[:16]


def _is_sensitive(text: str) -> bool:
    return bool(_SECRET_RE.search(text))


def _classify_claim(text: str, memory_type: str, section: str) -> str:
    if memory_type == "feedback" and _RULE_RE.search(text):
        return "rule"
    if _PREFERENCE_RE.search(text):
        return "preference"
    if _DECISION_RE.search(text):
        return "decision"
    if _CAVEAT_RE.search(text):
        return "caveat"
    if memory_type == "feedback":
        return "rule"
    if memory_type == "reference":
        return "fact"
    if re.search(r"\d+[%KMGkmg]|\d+\.\d+|[\$€£]\d+|\d+/\d+", text):
        return "fact"
    if _RULE_RE.search(text):
        return "rule"
    return "fact"


def _claim_severity(claim_type: str) -> str:
    return {
        "rule": "critical",
        "preference": "high",
        "decision": "high",
        "fact": "medium",
        "caveat": "low",
        "session_noise": "info",
        "config_ref": "info",
    }.get(claim_type, "info")


class MemorySignal:
    """One extracted claim, mapped to the cascade Signal interface."""

    def __init__(self, claim: Dict[str, Any]):
        text = claim["text"]
        self.signal_id = int(_content_hash(text), 16)
        self.cluster_id = claim.get("source_system", "claude-memory")
        self.namespace = claim.get("project", "general")
        self.resource_kind = claim.get("claim_type", "fact")
        self.resource_name = claim.get("source_memory", "")
        self.signal_type = claim.get("claim_type", "fact")
        self.severity = _claim_severity(self.signal_type)
        self.evidence = {
            "message": text,
            "claim_type": claim.get("claim_type", ""),
            "source_memory": claim.get("source_memory", ""),
            "section": claim.get("section", ""),
            "memory_type": claim.get("memory_type", ""),
        }
        self.labels = {
            "domain": "memory",
            "source": claim.get("source_system", ""),
            "memory_type": claim.get("memory_type", ""),
            "project": claim.get("project", ""),
        }


class ClaimExtractor:
    """Extracts individual claims from a parsed memory record."""

    def __init__(self):
        self._exact_hashes: Dict[str, List[str]] = {}
        self._near_hashes: Dict[str, List[str]] = {}
        self._topic_projects: Dict[str, set] = {}

    def extract(self, record: dict) -> List[dict]:
        body = record.get("body", "")
        memory_type = record.get("memory_type", "")
        source_memory = record.get("name", "")
        project = record.get("project", "")
        source_system = record.get("source_system", "claude-memory")

        if not body or len(body.strip()) < 20:
            return []

        raw_claims = self._split_into_claims(body, memory_type)

        claims = []
        for text, section in raw_claims:
            text = text.strip()
            if len(text) < 15:
                continue
            if _is_sensitive(text):
                log.debug("Skipping sensitive claim from %s", source_memory)
                continue

            claim_type = _classify_claim(text, memory_type, section)
            exact_h = _content_hash(text)
            near_h = _trigram_hash(text)

            self._exact_hashes.setdefault(exact_h, []).append(project)
            self._near_hashes.setdefault(near_h, []).append(project)

            topics = _extract_topics(text)
            for topic in topics:
                self._topic_projects.setdefault(topic, set()).add(project)

            claims.append({
                "text": text,
                "claim_type": claim_type,
                "section": section,
                "source_memory": source_memory,
                "memory_type": memory_type,
                "project": project,
                "source_system": source_system,
                "exact_hash": exact_h,
                "near_hash": near_h,
                "topics": list(topics),
            })

        return claims

    def get_cross_project_claims(self) -> Dict[str, List[str]]:
        """Returns near-hashes that appear in 2+ distinct projects."""
        cross = {}
        for h, projects in self._near_hashes.items():
            unique = list(set(projects))
            if len(unique) >= 2:
                cross[h] = unique
        return cross

    def get_institutional_topics(self) -> Dict[str, List[str]]:
        """Returns topics that appear across 3+ distinct projects — institutional knowledge."""
        return {
            topic: sorted(projects)
            for topic, projects in self._topic_projects.items()
            if len(projects) >= 3
        }

    def _split_into_claims(self, body: str, memory_type: str) -> List[Tuple[str, str]]:
        claims = []

        bold_sections = dict(_BOLD_SECTION_RE.findall(body))
        plain_body = _BOLD_SECTION_RE.sub("", body).strip()

        if plain_body:
            paragraphs = [p.strip() for p in plain_body.split("\n\n") if p.strip()]
            for p in paragraphs:
                if p.startswith("|") and "|" in p[1:]:
                    rows = _TABLE_ROW_RE.findall(p)
                    for row in rows:
                        cells = [c.strip() for c in row.split("|") if c.strip()]
                        if cells and not all(c.startswith("-") for c in cells):
                            claims.append((" | ".join(cells), "table"))
                elif p.startswith("```"):
                    continue
                elif p.startswith("- ") or p.startswith("* "):
                    for bullet in _BULLET_RE.findall(p):
                        claims.append((bullet.strip(), "body"))
                else:
                    wikilinks = _WIKILINK_RE.findall(p)
                    cleaned = _WIKILINK_RE.sub("", p).strip()
                    if cleaned and len(cleaned) > 15:
                        claims.append((cleaned, "opening"))

        for section_name, section_body in bold_sections.items():
            section_key = section_name.strip().lower()
            section_body = section_body.strip()
            if not section_body:
                continue

            bullets = _BULLET_RE.findall(section_body)
            if bullets:
                for b in bullets:
                    claims.append((b.strip(), section_key))
            else:
                claims.append((section_body, section_key))

        return claims


class MemoryCollector(BaseCollector):
    """Collects claim-level signals from any agent memory system.

    Pluggable architecture: configure sources in connect(config).
    Each source is a (scanner_function, kwargs) pair. Built-in sources:
    - claude_projects_dir: Claude Code project memories
    - sessions_dir: session handoff/park/standup files
    - cursor_dirs: Cursor AI .cursorrules and .cursor/rules/
    - claude_md_dir: CLAUDE.md / AGENTS.md from repos
    - markdown_dirs: any directory of .md files

    All sources produce the same record format. The ClaimExtractor
    handles splitting, classifying, dedup, and topic extraction.
    """

    name = "memory"

    def __init__(self):
        self._sources: List[Dict[str, Any]] = []
        self._connected = False
        self._last_scan_time = 0.0
        self._extractor = ClaimExtractor()

    def connect(self, config: dict) -> bool:
        from . import memory_parsers as parsers

        self._sources = []

        claude_dir = config.get(
            "claude_projects_dir",
            os.path.expanduser("~/.claude/projects"),
        )
        if os.path.isdir(claude_dir):
            self._sources.append({
                "name": f"claude-code({claude_dir})",
                "scanner": parsers.scan_claude_memories,
                "kwargs": {"projects_dir": claude_dir},
            })

        sessions_dir = config.get(
            "sessions_dir",
            os.path.expanduser("~/Desktop/sessions"),
        )
        if os.path.isdir(sessions_dir):
            self._sources.append({
                "name": f"sessions({sessions_dir})",
                "scanner": parsers.scan_session_files,
                "kwargs": {"sessions_dir": sessions_dir},
            })

        for cursor_dir in config.get("cursor_dirs", []):
            if os.path.isdir(cursor_dir):
                self._sources.append({
                    "name": f"cursor({cursor_dir})",
                    "scanner": parsers.scan_cursor_rules,
                    "kwargs": {"repo_dir": cursor_dir},
                })

        claude_md_dir = config.get("claude_md_dir", "")
        if claude_md_dir and os.path.isdir(claude_md_dir):
            self._sources.append({
                "name": f"claude-md({claude_md_dir})",
                "scanner": parsers.scan_claude_md_files,
                "kwargs": {"search_dir": claude_md_dir},
            })

        for md_entry in config.get("markdown_dirs", []):
            if isinstance(md_entry, str):
                md_entry = {"path": md_entry}
            path = md_entry.get("path", "")
            if path and os.path.isdir(path):
                self._sources.append({
                    "name": f"markdown({path})",
                    "scanner": parsers.scan_markdown_dir,
                    "kwargs": {
                        "docs_dir": path,
                        "project": md_entry.get("project", os.path.basename(path)),
                        "source_system": md_entry.get("source_system", "markdown"),
                    },
                })

        # Workload sources — agentic system decision logs
        ledger_url = config.get("ledger_url", "")
        if ledger_url:
            from . import memory_parsers_workload as wparsers
            self._sources.append({
                "name": f"ledger({ledger_url})",
                "scanner": wparsers.scan_ledger_decisions,
                "kwargs": {
                    "ledger_url": ledger_url,
                    "bearer_token": config.get("ledger_token", ""),
                    "max_pages": config.get("ledger_max_pages", 10),
                },
            })

        for log_path in config.get("jsonl_logs", []):
            from . import memory_parsers_workload as wparsers
            self._sources.append({
                "name": f"jsonl({log_path})",
                "scanner": wparsers.scan_jsonl_logs,
                "kwargs": {"log_path": log_path},
            })

        self._connected = len(self._sources) > 0
        if self._connected:
            log.info("Memory collector connected: %s",
                     ", ".join(s["name"] for s in self._sources))
        else:
            log.warning("No memory sources found")
        return self._connected

    def collect(self) -> list:
        if not self._connected:
            return []
        cutoff = self._last_scan_time
        self._last_scan_time = time.time()
        signals = []
        for record in self._scan_records(since=cutoff):
            for claim in self._extractor.extract(record):
                signals.append(MemorySignal(claim))
        return signals

    def collect_all(self) -> list:
        if not self._connected:
            return []
        self._extractor = ClaimExtractor()
        signals = []
        records = list(self._scan_records())
        for record in records:
            for claim in self._extractor.extract(record):
                signals.append(MemorySignal(claim))

        cross = self._extractor.get_cross_project_claims()
        log.info(
            "Memory collector: %d claims from %d files, %d cross-project patterns",
            len(signals), len(records), len(cross),
        )
        return signals

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "claude_dir": self._claude_dir,
            "sessions_dir": self._sessions_dir,
        }

    def _scan_records(self, since: float = 0):
        for source in self._sources:
            kwargs = dict(source["kwargs"])
            kwargs["since"] = since
            yield from source["scanner"](**kwargs)
