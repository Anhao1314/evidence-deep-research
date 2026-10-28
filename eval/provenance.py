"""运行溯源：Git SHA、时间戳与文件哈希。

评测报告必须能回答“这份结果对应哪次代码”。拿不到 Git 信息时返回 None，
不伪造提交号。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SOURCE_SUFFIXES = {
    ".py", ".json", ".jsonl", ".md", ".yml", ".yaml", ".toml",
    ".txt", ".cfg", ".ini", ".example",
}
_EXCLUDE_PREFIXES = (
    "docs/artifacts/", "docs/demo.md", "eval/runs/", "eval/.run/",
    "data/memory", "data/trace",
)


def _git_bytes(*args: str, cwd: Path) -> bytes | None:
    if shutil.which("git") is None:
        return None
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, timeout=30, check=True
        )
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git(*args: str, cwd: Path) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None


def git_commit(cwd: Path) -> str | None:
    return _git("rev-parse", "HEAD", cwd=cwd)


def git_dirty(cwd: Path) -> bool | None:
    status = _git("status", "--porcelain", cwd=cwd)
    if status is None:
        return None
    return bool(status)


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_inventory(directory: Path, *, max_files: int = 5000) -> dict:
    """记录语料路径、文件清单、大小与 SHA256；用于真实 KB 的可审计性。"""
    if not directory.exists() or not directory.is_dir():
        return {"exists": False, "path": str(directory), "file_count": 0, "files": []}
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    truncated = len(files) > max_files
    files = files[:max_files]
    entries = []
    for path in files:
        entries.append({
            "path": str(path.relative_to(directory)),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return {
        "exists": True,
        "path": str(directory),
        "file_count": len(files),
        "total_bytes": sum(e["size"] for e in entries),
        "truncated": truncated,
        "files": entries,
    }


def _is_source_path(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in _EXCLUDE_PREFIXES):
        return False
    return Path(path).suffix in SOURCE_SUFFIXES


def source_manifest(repo_root: Path) -> dict:
    """记录未提交源码树的指纹：tracked diff、未跟踪源码文件与哈希。

    不 commit 的前提下，这份 manifest 让产物能对应到同一份源码状态。
    """
    excludes = [
        "--", ".",
        ":(exclude)docs/artifacts",
        ":(exclude)docs/demo.md",
        ":(exclude)eval/runs",
        ":(exclude)eval/.run",
    ]
    diff_bytes = _git_bytes("diff", "--binary", "HEAD", *excludes, cwd=repo_root) or b""
    diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    changed_raw = _git_bytes(
        "diff", "--name-only", "HEAD", *excludes, cwd=repo_root
    ) or b""
    changed_paths = sorted(
        p for p in changed_raw.decode("utf-8", errors="replace").splitlines() if p
    )
    untracked_raw = _git_bytes(
        "ls-files", "--others", "--exclude-standard", cwd=repo_root
    ) or b""
    untracked_paths = sorted(
        p for p in untracked_raw.decode("utf-8", errors="replace").splitlines()
        if p and _is_source_path(p)
    )
    untracked = [
        {"path": p, "sha256": sha256_file(repo_root / p)}
        for p in untracked_paths
        if (repo_root / p).is_file()
    ]
    fingerprint_payload = {
        "git_commit": git_commit(repo_root),
        "tracked_diff_sha256": diff_sha,
        "tracked_diff_bytes": len(diff_bytes),
        "untracked": untracked,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "1.0",
        "git_commit": fingerprint_payload["git_commit"],
        "git_dirty": git_dirty(repo_root),
        "tracked_diff_sha256": diff_sha,
        "tracked_diff_bytes": len(diff_bytes),
        "changed_paths": changed_paths,
        "untracked_source_files": untracked,
        "untracked_source_count": len(untracked),
        "source_tree_fingerprint": fingerprint,
        "note": "不 commit 场景下的源码指纹；diff 与未跟踪源码文件哈希共同决定。",
    }