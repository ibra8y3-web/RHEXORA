#!/usr/bin/env python3
"""Create a privacy-conscious ZIP bundle for a failed GitHub Actions run.

Uses only the Python standard library. Requires GITHUB_TOKEN with actions:read
when collecting remote run/job logs. Never copies obvious secret files.
"""
from __future__ import annotations

import json
import io
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "build-failure-report"
SECRET_PARTS = {".git", ".venv", "venv", "node_modules", "secrets", ".gradle", ".idea"}
SECRET_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "credentials.json", "secrets.json", "token.json", ".npmrc", ".pypirc", ".netrc"}
SECRET_SUFFIXES = {".pem", ".p12", ".pfx", ".keystore", ".jks", ".key"}
PATH_PATTERNS = [
    re.compile(r"(?P<path>(?:[A-Za-z0-9_.@+-]+[/\\])+[A-Za-z0-9_.@+-]+\.(?:py|kt|kts|java|xml|gradle|toml|ya?ml|json|properties|sh|md))(?::(?P<line>\d+))?"),
    re.compile(r"(?P<path>[A-Za-z0-9_.@+-]+\.(?:py|kt|kts|java|xml|gradle|toml|ya?ml|json|properties|sh)):(?P<line>\d+)"),
]


def safe_repo_path(raw: str) -> Path | None:
    raw = raw.strip().strip("`'\"()[]{}:,;") .replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    p = PurePosixPath(raw)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    if any(part.lower() in SECRET_PARTS for part in p.parts):
        return None
    name = p.name.lower()
    if name in SECRET_NAMES or name.startswith(".env."):
        return None
    if Path(name).suffix.lower() in SECRET_SUFFIXES:
        return None
    candidate = ROOT.joinpath(*p.parts)
    try:
        candidate.resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def extract_referenced_files(log_text: str) -> dict[str, list[int]]:
    found: dict[str, set[int]] = {}
    for pattern in PATH_PATTERNS:
        for match in pattern.finditer(log_text):
            candidate = safe_repo_path(match.group("path"))
            if candidate:
                rel = candidate.relative_to(ROOT).as_posix()
                found.setdefault(rel, set())
                if match.groupdict().get("line"):
                    found[rel].add(int(match.group("line")))
    return {k: sorted(v) for k, v in sorted(found.items())}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not auto-follow redirects, so the token is never sent to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_get(url: str, token: str) -> bytes:
    """GET from the GitHub API.

    The job-logs endpoint answers 302 to a pre-signed blob URL. Python's default
    redirect handler forwards the Authorization header to that host, which then
    rejects it with HTTP 401. So follow redirects manually, without the token.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RHEXORA-Build-Failure-Collector",
    }
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(5):
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(req, timeout=45) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location")
            if exc.code in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                headers = {"User-Agent": headers["User-Agent"]}  # no token off-host
                continue
            raise
    raise RuntimeError("too many redirects")


def git_changed_files() -> list[str]:
    try:
        result = subprocess.run(["git", "diff", "--name-only", "HEAD^", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=15, check=False)
        return [x.strip() for x in result.stdout.splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def collect() -> Path:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
    server = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    run_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    job_hint = os.environ.get("GITHUB_JOB", "unknown")
    logs: dict[str, bytes] = {}
    jobs: list[dict[str, Any]] = []
    api_error = None

    if repo and run_id and token:
        try:
            data = json.loads(api_get(f"{server}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", token))
            for job in data.get("jobs", []):
                jobs.append({k: job.get(k) for k in ("id", "name", "status", "conclusion", "started_at", "completed_at", "html_url")})
                if job.get("conclusion") in {"failure", "cancelled", "timed_out", "action_required"}:
                    try:
                        logs[f"job-{job['id']}.log.zip"] = api_get(
                            f"{server}/repos/{repo}/actions/jobs/{job['id']}/logs", token)
                    except Exception as exc:  # keep the rest of the bundle useful
                        logs[f"job-{job['id']}-logs-error.txt"] = str(exc).encode()
        except Exception as exc:
            api_error = f"Could not retrieve GitHub job metadata/logs: {type(exc).__name__}: {exc}"
    else:
        api_error = "GitHub API token/context missing; source snapshot and local metadata were still collected."

    OUT_DIR.mkdir(exist_ok=True)
    for old in OUT_DIR.iterdir():
        if old.is_file():
            old.unlink()
        elif old.is_dir():
            import shutil
            shutil.rmtree(old)

    combined_logs = "\n".join(v.decode("utf-8", "replace") for k, v in logs.items() if k.endswith(".txt"))
    referenced = extract_referenced_files(combined_logs)
    changed = git_changed_files()
    selected = set(referenced) | {p for p in changed if safe_repo_path(p)}

    manifest = {
        "schema": "rhexora.build-failure-report.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "run_id": run_id,
        "run_url": f"{run_url}/{repo}/actions/runs/{run_id}" if repo and run_id else None,
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "job_identifier": job_hint,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "event": os.environ.get("GITHUB_EVENT_NAME"),
        "ref": os.environ.get("GITHUB_REF"),
        "sha": os.environ.get("GITHUB_SHA"),
        "jobs": jobs,
        "files_referenced_in_logs": referenced,
        "changed_files_in_commit": changed,
        "included_source_files": sorted(selected),
        "api_collection_note": api_error,
        "privacy": "Obvious secret files and secret directories are excluded. Review the bundle before sharing it publicly.",
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "README_AR.txt").write_text(
        "تقرير فشل بناء RHEXORA\n\n"
        "- manifest.json: بيانات التشغيل والوظائف والملفات التي ظهرت في السجلات.\n"
        "- logs/: سجلات الوظائف الفاشلة التي أمكن تنزيلها من GitHub Actions.\n"
        "- source/: نسخ كاملة من الملفات التي ظهرت مساراتها في السجلات أو تغيرت في آخر commit.\n\n"
        "مهم: الملفات المذكورة في السجل مؤكدة الظهور في السجل، لكنها ليست بالضرورة سبب الفشل.\n"
        "راجع محتويات الحزمة قبل مشاركتها؛ تم استبعاد الملفات والأدلة السرية الشائعة تلقائيًا.\n",
        encoding="utf-8")

    for rel in selected:
        src = safe_repo_path(rel)
        if src:
            dest = OUT_DIR / "source" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    if api_error:
        (OUT_DIR / "logs_collection_status.txt").write_text(api_error, encoding="utf-8")
    for name, content in logs.items():
        if name.endswith(".log.zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as nested:
                    for member in nested.infolist():
                        if member.is_dir():
                            continue
                        # GitHub job-log archives normally contain one text file.
                        safe_name = PurePosixPath(member.filename).name or "job.log"
                        dest = OUT_DIR / "logs" / name.removesuffix(".zip") / safe_name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(nested.read(member))
            except zipfile.BadZipFile:
                dest = OUT_DIR / "logs" / name.removesuffix(".zip") / "response.bin"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
        else:
            dest = OUT_DIR / "logs" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

    archive = ROOT / "rhexora-build-failure-report.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(OUT_DIR.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(ROOT).as_posix())
    return archive


if __name__ == "__main__":
    print(collect())
