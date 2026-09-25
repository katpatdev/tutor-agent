#!/usr/bin/env python3
"""Offline-only preflight for a later human-authorized live test."""

from __future__ import annotations

import importlib
import json
import os
import socket
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 11, 14)
EXPECTED_NODE_MAJOR = 22
EXPECTED_YARN = "1.22.22"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def result(label: str, ok: bool, detail: str) -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def command_version(command: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    output = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, output


def port_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def main() -> int:
    checks: list[bool] = []

    actual_python = sys.version_info[:3]
    checks.append(
        result(
            "Python runtime",
            actual_python == EXPECTED_PYTHON,
            ".".join(map(str, actual_python)),
        )
    )

    python_hint = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    checks.append(
        result("Python version hint", python_hint == "3.11.14", python_hint)
    )

    node_ok, node_version = command_version(["node", "--version"])
    parsed_node_major = None
    if node_ok:
        try:
            parsed_node_major = int(node_version.lstrip("v").split(".", 1)[0])
        except ValueError:
            node_ok = False
    checks.append(
        result(
            "Node runtime",
            node_ok and parsed_node_major == EXPECTED_NODE_MAJOR,
            node_version or "unavailable",
        )
    )

    node_hint = (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    checks.append(result("Node version hint", node_hint == "22", node_hint))

    package = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    package_manager = package.get("packageManager", "")
    checks.append(
        result(
            "Yarn package hint",
            package_manager == f"yarn@{EXPECTED_YARN}",
            package_manager or "missing",
        )
    )

    yarn_ok, yarn_version = command_version(["yarn", "--version"])
    checks.append(
        result(
            "Yarn runtime",
            yarn_ok and yarn_version == EXPECTED_YARN,
            yarn_version or "unavailable",
        )
    )

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python_requirement = project.get("project", {}).get("requires-python")
    checks.append(
        result(
            "Python package hint",
            python_requirement == "==3.11.14",
            str(python_requirement),
        )
    )

    # Deliberately check metadata only: never open or print .env.
    env_exists = (ROOT / ".env").is_file()
    checks.append(result(".env exists", env_exists, str(env_exists)))
    key_present = bool(os.environ.get("OPENAI_API_KEY"))
    print(f"[INFO] OPENAI_API_KEY present in process environment: {key_present}")
    print("[INFO] .env contents were not opened or printed")

    for port in (7860, 5173):
        available = port_available(port)
        checks.append(
            result(
                f"Port {port}",
                available,
                "available" if available else "already in use",
            )
        )

    modules = (
        "narration_plan",
        "presentation_runtime",
        "lesson_protocol",
        "session_metrics",
    )
    try:
        for module in modules:
            importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001
        checks.append(result("Backend module imports", False, type(exc).__name__))
    else:
        checks.append(result("Backend module imports", True, ", ".join(modules)))

    try:
        from prompt_registry import load_active_tutor_prompt

        prompt = load_active_tutor_prompt()
        prompt_detail = (
            f"{prompt.record.prompt_id}/{prompt.record.version}, approved="
            f"{prompt.record.approved}, hash_verified=True"
        )
        prompt_ok = prompt.record.approved and prompt.record.status == "approved"
    except Exception as exc:  # noqa: BLE001
        prompt_ok = False
        prompt_detail = type(exc).__name__
    checks.append(result("Prompt registry", prompt_ok, prompt_detail))

    print("No OpenAI request was made")
    if all(checks):
        print("Preflight succeeded. Live validation remains human-authorized and not run.")
        return 0
    print("Preflight failed. Resolve failed checks before live validation.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
