"""Format-specific parsers for agent memory systems.

Each parser converts a raw file into a list of record dicts with fields:
  path, name, description, memory_type, project, source_system, body

The ClaimExtractor in memory.py handles the rest (splitting, classifying,
dedup, topic extraction). Adding a new agent framework = adding a parser here.
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_SESSION_PREFIXES = {
    "handoff-": "handoff",
    "park-": "park",
    "standup-": "standup",
    "session-": "transcript",
}

_EPHEMERAL_SECTIONS = {
    "what's running", "current state", "key config", "infrastructure",
    "stash", "to resume", "git status",
}


# ---------------------------------------------------------------------------
# Claude Code memories (~/.claude/projects/*/memory/*.md)
# ---------------------------------------------------------------------------

def parse_claude_memory(filepath: Path, project_name: str) -> Optional[dict]:
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    frontmatter = {}
    body = text
    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            pass
        body = text[fm_match.end():]

    metadata = frontmatter.get("metadata", {})
    return {
        "path": str(filepath),
        "name": frontmatter.get("name", filepath.stem),
        "description": frontmatter.get("description", ""),
        "memory_type": metadata.get("type", "unknown"),
        "project": project_name,
        "source_system": "claude-code",
        "body": body.strip(),
    }


def scan_claude_memories(projects_dir: str, since: float = 0) -> Iterator[dict]:
    claude_path = Path(projects_dir)
    if not claude_path.is_dir():
        return

    for project_dir in sorted(claude_path.iterdir()):
        if not project_dir.is_dir():
            continue
        memory_dir = project_dir / "memory"
        if not memory_dir.is_dir():
            continue
        project_name = project_dir.name
        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            if since and md_file.stat().st_mtime < since:
                continue
            record = parse_claude_memory(md_file, project_name)
            if record:
                yield record


# ---------------------------------------------------------------------------
# Session files (~/Desktop/sessions/*.md)
# ---------------------------------------------------------------------------

def parse_session_file(filepath: Path) -> List[dict]:
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    filename = filepath.name
    session_type = "unknown"
    project = "unknown"
    for prefix, stype in _SESSION_PREFIXES.items():
        if filename.startswith(prefix):
            session_type = stype
            rest = filename[len(prefix):].rsplit(".", 1)[0]
            parts = rest.rsplit("-", 4)
            if len(parts) >= 5:
                project = "-".join(parts[:-4])
            elif parts:
                project = parts[0]
            break

    if session_type == "transcript":
        return []

    sections = re.split(r"\n## ", text)
    records = []
    for i, section in enumerate(sections):
        if i == 0:
            continue
        lines = section.split("\n", 1)
        section_name = lines[0].strip()
        section_body = lines[1].strip() if len(lines) > 1 else ""
        if not section_body or len(section_body) < 20:
            continue
        if section_name.lower() in _EPHEMERAL_SECTIONS:
            continue

        records.append({
            "path": str(filepath),
            "name": f"{filepath.stem}/{section_name}",
            "description": f"{session_type}: {section_name}",
            "memory_type": session_type,
            "project": project,
            "source_system": "session",
            "body": section_body,
        })

    return records


def scan_session_files(sessions_dir: str, since: float = 0) -> Iterator[dict]:
    sessions_path = Path(sessions_dir)
    if not sessions_path.is_dir():
        return

    for md_file in sorted(sessions_path.glob("*.md")):
        if since and md_file.stat().st_mtime < since:
            continue
        for record in parse_session_file(md_file):
            yield record


# ---------------------------------------------------------------------------
# Cursor rules (.cursorrules, .cursor/rules/*.mdc)
# ---------------------------------------------------------------------------

def scan_cursor_rules(repo_dir: str, since: float = 0) -> Iterator[dict]:
    """Parse Cursor AI rule files into memory records."""
    repo_path = Path(repo_dir)

    cursorrules = repo_path / ".cursorrules"
    if cursorrules.is_file():
        if not since or cursorrules.stat().st_mtime >= since:
            try:
                text = cursorrules.read_text(encoding="utf-8")
                yield {
                    "path": str(cursorrules),
                    "name": ".cursorrules",
                    "description": "Cursor AI project rules",
                    "memory_type": "rule",
                    "project": repo_path.name,
                    "source_system": "cursor",
                    "body": text.strip(),
                }
            except (OSError, UnicodeDecodeError):
                pass

    rules_dir = repo_path / ".cursor" / "rules"
    if rules_dir.is_dir():
        for rule_file in sorted(rules_dir.glob("*.mdc")):
            if since and rule_file.stat().st_mtime < since:
                continue
            try:
                text = rule_file.read_text(encoding="utf-8")
                body = text
                fm_match = _FRONTMATTER_RE.match(text)
                if fm_match:
                    body = text[fm_match.end():]
                yield {
                    "path": str(rule_file),
                    "name": rule_file.stem,
                    "description": f"Cursor rule: {rule_file.stem}",
                    "memory_type": "rule",
                    "project": repo_path.name,
                    "source_system": "cursor",
                    "body": body.strip(),
                }
            except (OSError, UnicodeDecodeError):
                pass


# ---------------------------------------------------------------------------
# CLAUDE.md / AGENTS.md project instructions
# ---------------------------------------------------------------------------

def scan_claude_md_files(search_dir: str, since: float = 0) -> Iterator[dict]:
    """Parse CLAUDE.md files from repos — project-level agent instructions."""
    search_path = Path(search_dir)
    if not search_path.is_dir():
        return

    for repo_dir in sorted(search_path.iterdir()):
        if not repo_dir.is_dir():
            continue
        for name in ("CLAUDE.md", "AGENTS.md", "CONVENTIONS.md"):
            md_file = repo_dir / name
            if not md_file.is_file():
                continue
            if since and md_file.stat().st_mtime < since:
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                sections = re.split(r"\n## ", text)
                for i, section in enumerate(sections):
                    if i == 0:
                        body = section.strip()
                        if body.startswith("#"):
                            body = body.split("\n", 1)[1].strip() if "\n" in body else ""
                        section_name = "overview"
                    else:
                        lines = section.split("\n", 1)
                        section_name = lines[0].strip()
                        body = lines[1].strip() if len(lines) > 1 else ""

                    if not body or len(body) < 20:
                        continue

                    yield {
                        "path": str(md_file),
                        "name": f"{repo_dir.name}/{name}/{section_name}",
                        "description": f"{name}: {section_name}",
                        "memory_type": "instruction",
                        "project": repo_dir.name,
                        "source_system": "claude-md",
                        "body": body,
                    }
            except (OSError, UnicodeDecodeError):
                pass


# ---------------------------------------------------------------------------
# Generic markdown knowledge bases (any directory of .md files)
# ---------------------------------------------------------------------------

def scan_markdown_dir(docs_dir: str, project: str = "docs",
                      source_system: str = "markdown",
                      since: float = 0) -> Iterator[dict]:
    """Parse any directory of markdown files into memory records."""
    docs_path = Path(docs_dir)
    if not docs_path.is_dir():
        return

    for md_file in sorted(docs_path.rglob("*.md")):
        if since and md_file.stat().st_mtime < since:
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        frontmatter = {}
        body = text
        fm_match = _FRONTMATTER_RE.match(text)
        if fm_match:
            try:
                frontmatter = yaml.safe_load(fm_match.group(1)) or {}
            except yaml.YAMLError:
                pass
            body = text[fm_match.end():]

        rel_path = md_file.relative_to(docs_path)
        yield {
            "path": str(md_file),
            "name": frontmatter.get("name", str(rel_path)),
            "description": frontmatter.get("description", ""),
            "memory_type": frontmatter.get("type", "document"),
            "project": frontmatter.get("project", project),
            "source_system": source_system,
            "body": body.strip(),
        }
