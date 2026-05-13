#!/usr/bin/env python3
"""ferret — daily GitHub repo status report delivered to Discord."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
REPOS_FILE = ROOT / "repos.yaml"
DISCORD_API = "https://discord.com/api/v10"
MAX_MESSAGE_CHARS = 1900  # leave headroom below Discord's 2000 cap


# ---------- config loading ----------

def load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE} — copy .env.example and fill it in")
    env: dict[str, str] = {}
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    for required in ("DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"):
        if not env.get(required):
            sys.exit(f"{required} missing in {ENV_FILE}")
    return env


def load_repos() -> list[dict]:
    if not REPOS_FILE.exists():
        sys.exit(f"missing {REPOS_FILE} — copy repos.yaml.example and fill it in")
    data = yaml.safe_load(REPOS_FILE.read_text()) or {}
    repos = data.get("repos", [])
    if not repos:
        sys.exit("repos.yaml has no entries under `repos:`")
    return repos


# ---------- repo inspection ----------

@dataclass
class RepoReport:
    name: str
    path: Path
    exists: bool = True
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    dirty: int = 0
    fetch_ok: bool = True
    pr_open: int = 0
    pr_titles: list[str] = field(default_factory=list)
    issue_open: int = 0
    ci_status: str = ""   # "passed" / "failed" / "running" / ""
    errors: list[str] = field(default_factory=list)


def run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def inspect_repo(entry: dict) -> RepoReport:
    name = entry["name"]
    path = Path(entry["path"]).expanduser()
    skip = set(entry.get("skip") or [])
    report = RepoReport(name=name, path=path)

    if not (path / ".git").exists():
        report.exists = False
        report.errors.append("not a git repo")
        return report

    if "local" not in skip:
        rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
        if rc == 0:
            report.branch = branch

        fetch_rc, _, fetch_err = run(["git", "fetch", "--quiet"], cwd=path, timeout=45)
        if fetch_rc != 0:
            report.fetch_ok = False
            report.errors.append(f"fetch failed: {fetch_err[:60]}")

        upstream_rc, _, _ = run(
            ["git", "rev-parse", "--abbrev-ref", "@{upstream}"], cwd=path
        )
        if upstream_rc == 0:
            rc, out, _ = run(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                cwd=path,
            )
            if rc == 0 and out:
                parts = out.split()
                if len(parts) == 2:
                    report.ahead, report.behind = int(parts[0]), int(parts[1])

        rc, out, _ = run(["git", "status", "--porcelain"], cwd=path)
        if rc == 0:
            report.dirty = len([l for l in out.splitlines() if l.strip()])

    # gh-driven checks — silently skipped if `gh` missing or repo has no GitHub remote
    if "pr" not in skip:
        rc, out, _ = run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,title", "--limit", "20"],
            cwd=path, timeout=20,
        )
        if rc == 0 and out:
            try:
                prs = json.loads(out)
                report.pr_open = len(prs)
                report.pr_titles = [f"#{p['number']} {p['title']}" for p in prs[:3]]
            except json.JSONDecodeError:
                pass

    if "issue" not in skip:
        rc, out, _ = run(
            ["gh", "issue", "list", "--state", "open", "--json", "number", "--limit", "100"],
            cwd=path, timeout=20,
        )
        if rc == 0 and out:
            try:
                issues = json.loads(out)
                report.issue_open = len(issues)
            except json.JSONDecodeError:
                pass

    if "ci" not in skip:
        rc, out, _ = run(
            ["gh", "run", "list", "--limit", "1", "--json", "conclusion,status"],
            cwd=path, timeout=20,
        )
        if rc == 0 and out:
            try:
                runs = json.loads(out)
                if runs:
                    status = runs[0].get("status") or ""
                    conclusion = runs[0].get("conclusion") or ""
                    if status == "in_progress":
                        report.ci_status = "running"
                    elif conclusion == "success":
                        report.ci_status = "passed"
                    elif conclusion in ("failure", "timed_out", "cancelled"):
                        report.ci_status = conclusion
            except json.JSONDecodeError:
                pass

    return report


# ---------- formatting ----------

def format_repo_line(r: RepoReport) -> str:
    if not r.exists:
        return f"**{r.name}**\n  ❓ {', '.join(r.errors) or 'missing'}"

    parts: list[str] = []

    if r.dirty == 0 and r.ahead == 0 and r.behind == 0 and r.fetch_ok:
        parts.append("✅ clean & in sync")
    else:
        bits = []
        if r.dirty:
            bits.append(f"⚠️ {r.dirty} uncommitted")
        if r.ahead:
            bits.append(f"⬆ {r.ahead} ahead")
        if r.behind:
            bits.append(f"⬇ {r.behind} behind")
        if not r.fetch_ok:
            bits.append("⚠️ fetch failed")
        parts.append(" · ".join(bits) if bits else "✅ clean")

    branch_suffix = f" ({r.branch})" if r.branch and r.branch not in ("main", "master") else ""

    if r.pr_open:
        sample = r.pr_titles[0] if r.pr_titles else ""
        suffix = f" · top: {sample[:60]}" if sample else ""
        parts.append(f"🔀 {r.pr_open} open PR{'s' if r.pr_open != 1 else ''}{suffix}")

    if r.issue_open:
        parts.append(f"🐛 {r.issue_open} open issue{'s' if r.issue_open != 1 else ''}")

    if r.ci_status == "passed":
        parts.append("✅ CI passed")
    elif r.ci_status == "running":
        parts.append("⏳ CI running")
    elif r.ci_status in ("failure", "timed_out", "cancelled"):
        parts.append(f"❌ CI {r.ci_status}")

    body = "\n  ".join(parts)
    return f"**{r.name}**{branch_suffix}\n  {body}"


def build_message(reports: list[RepoReport]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"📊 **ferret · GitHub daily report · {now}**"
    summary_bits = []
    dirty_total = sum(r.dirty for r in reports)
    if dirty_total:
        summary_bits.append(f"{dirty_total} uncommitted")
    pr_total = sum(r.pr_open for r in reports)
    if pr_total:
        summary_bits.append(f"{pr_total} open PRs")
    issue_total = sum(r.issue_open for r in reports)
    if issue_total:
        summary_bits.append(f"{issue_total} open issues")
    summary = f"_{' · '.join(summary_bits)}_" if summary_bits else "_all clean_"

    lines = [header, summary, ""]
    for r in reports:
        lines.append(format_repo_line(r))
        lines.append("")
    return "\n".join(lines).strip()


# ---------- Discord delivery ----------

def post_to_discord(content: str, token: str, channel_id: str) -> None:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    for chunk in split_for_discord(content):
        body = json.dumps({"content": chunk}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "ferret (https://github.com/alexli-77/ferret, v1)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            sys.exit(f"Discord API {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            sys.exit(f"Discord network error: {exc}")


def split_for_discord(content: str) -> list[str]:
    if len(content) <= MAX_MESSAGE_CHARS:
        return [content]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in content.splitlines(keepends=True):
        if size + len(line) > MAX_MESSAGE_CHARS and buf:
            chunks.append("".join(buf).rstrip())
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        chunks.append("".join(buf).rstrip())
    return chunks


# ---------- main ----------

def main() -> int:
    env = load_env()
    repos = load_repos()
    reports = [inspect_repo(r) for r in repos]
    message = build_message(reports)

    if os.environ.get("FERRET_DRY_RUN"):
        print(message)
        return 0

    post_to_discord(message, env["DISCORD_BOT_TOKEN"], env["DISCORD_CHANNEL_ID"])
    print(f"sent report to channel {env['DISCORD_CHANNEL_ID']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
