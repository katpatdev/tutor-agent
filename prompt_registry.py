"""Version-controlled prompt registry (local files only; no network)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional


class PromptRegistryError(Exception):
    """Invalid registry, missing file, hash mismatch, or unsafe path."""


_VERSION_RE = re.compile(r"^v\d+$")
_SAFE_REL_PATH = re.compile(r"^[a-zA-Z0-9_./-]+$")


@dataclass(frozen=True)
class PromptRecord:
    prompt_id: str
    version: str
    status: str
    relative_path: str
    sha256: str
    created_at: str
    approved: bool
    parent_version: Optional[str]
    change_summary: str


@dataclass(frozen=True)
class LoadedPrompt:
    record: PromptRecord
    text: str
    content_hash: str


def default_prompts_root() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_rel_path(rel: str) -> str:
    rel = rel.replace("\\", "/").strip()
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise PromptRegistryError("path traversal rejected")
    if not _SAFE_REL_PATH.match(rel):
        raise PromptRegistryError("relative_path contains illegal characters")
    return rel


def load_registry(prompts_root: Optional[Path] = None) -> List[PromptRecord]:
    root = Path(prompts_root) if prompts_root is not None else default_prompts_root()
    registry_path = root / "registry.json"
    if not registry_path.is_file():
        raise PromptRegistryError("prompts/registry.json is missing")
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptRegistryError("malformed registry data") from exc
    if not isinstance(raw, dict) or "prompts" not in raw:
        raise PromptRegistryError("malformed registry data")
    prompts = raw["prompts"]
    if not isinstance(prompts, list) or not prompts:
        raise PromptRegistryError("malformed registry data")
    records: List[PromptRecord] = []
    for item in prompts:
        if not isinstance(item, dict):
            raise PromptRegistryError("malformed registry data")
        try:
            records.append(
                PromptRecord(
                    prompt_id=str(item["prompt_id"]),
                    version=str(item["version"]),
                    status=str(item["status"]),
                    relative_path=_normalize_rel_path(str(item["relative_path"])),
                    sha256=str(item["sha256"]).lower(),
                    created_at=str(item["created_at"]),
                    approved=bool(item["approved"]),
                    parent_version=item.get("parent_version"),
                    change_summary=str(item.get("change_summary") or ""),
                )
            )
        except PromptRegistryError:
            raise
        except (KeyError, TypeError) as exc:
            raise PromptRegistryError("malformed registry data") from exc
        if len(records[-1].sha256) != 64:
            raise PromptRegistryError("malformed registry data")
    return records


def find_record(
    prompt_id: str,
    version: str,
    *,
    prompts_root: Optional[Path] = None,
) -> PromptRecord:
    for rec in load_registry(prompts_root):
        if rec.prompt_id == prompt_id and rec.version == version:
            return rec
    raise PromptRegistryError(f"prompt {prompt_id}/{version} not found in registry")


def load_prompt(
    prompt_id: str,
    version: str,
    *,
    prompts_root: Optional[Path] = None,
    require_approved: bool = True,
) -> LoadedPrompt:
    """Load a registered prompt. Network-free. Rejects unapproved when required."""
    root = Path(prompts_root) if prompts_root is not None else default_prompts_root()
    root_resolved = root.resolve()
    rec = find_record(prompt_id, version, prompts_root=root)
    if require_approved and (not rec.approved or rec.status != "approved"):
        raise PromptRegistryError(
            f"prompt {prompt_id}/{version} is not approved for runtime load"
        )
    rel = _normalize_rel_path(rec.relative_path)
    path = (root / rel).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise PromptRegistryError("path traversal rejected") from exc
    if not path.is_file():
        raise PromptRegistryError(f"prompt file missing: {rel}")
    try:
        text = path.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise PromptRegistryError(f"prompt file missing: {rel}") from exc
    digest = sha256_text(text)
    digest_with_nl = sha256_text(text + "\n")
    if digest != rec.sha256 and digest_with_nl != rec.sha256:
        raise PromptRegistryError(f"hash mismatch for {prompt_id}/{version}")
    return LoadedPrompt(record=rec, text=text, content_hash=digest)


def load_active_tutor_prompt(
    *,
    environ: Optional[Mapping[str, str]] = None,
    prompts_root: Optional[Path] = None,
) -> LoadedPrompt:
    env = environ if environ is not None else os.environ
    version = (env.get("TUTOR_PROMPT_VERSION") or "v1").strip()
    if not _VERSION_RE.match(version):
        raise PromptRegistryError(f"invalid TUTOR_PROMPT_VERSION: {version!r}")
    return load_prompt("tutor", version, prompts_root=prompts_root, require_approved=True)


def candidate_dir_is_ignored_by_runtime(
    candidate_path: Path, prompts_root: Optional[Path] = None
) -> bool:
    """Runtime only loads from approved registry under prompts/; candidates elsewhere ignored."""
    root = (Path(prompts_root) if prompts_root is not None else default_prompts_root()).resolve()
    try:
        candidate_path.resolve().relative_to(root)
        return False
    except ValueError:
        return True
