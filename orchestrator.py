#!/usr/bin/env python3
"""Deterministic multi-run Codex orchestrator.

Implements Design A requirements from the integrated spec and optional Design B features
when --design-b is enabled.

This script intentionally relies on filesystem state, exit codes, and deterministic validators
for all gating decisions.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parent
ORCH_DIR = REPO_ROOT / ".orchestrator"
RUNS_DIR = ORCH_DIR / "runs"
EVALS_DIR = ORCH_DIR / "evals"
POLICY_PATH = ORCH_DIR / "policy.json"
PROMPT_TEMPLATE_DIR = ORCH_DIR / "prompt_templates"
PROMPTS_DIR = REPO_ROOT / "prompts"
SKILLS_DIR = REPO_ROOT / ".codex" / "skills"
AGENTS_MD = REPO_ROOT / "AGENTS.md"

PROJECT_BRIEF_MD = REPO_ROOT / "PROJECT_BRIEF.md"
PROJECT_BRIEF_YAML = REPO_ROOT / "PROJECT_BRIEF.yaml"

REQUIRED_FILES_A = ["REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md"]
REQUIRED_DIRS_A = ["design", "frontend", "backend", "tests"]
REQUIRED_FILES_B = ["AGENTS.md"]
REQUIRED_DIRS_B = ["prompts", ".codex/skills"]

ERROR_PREFIX = "E_"

CODE_SUCCESS = 0
CODE_INVALID_ARGS = 2
CODE_PRECONDITION = 3
CODE_INVARIANT = 4
CODE_ALLOWLIST = 5
CODE_VALIDATION = 6
CODE_TEST_FAIL = 7
CODE_INTERNAL = 8

DEFAULT_POLICY = {
    "version": 1,
    "selection_strategy": "ucb1",
    "bootstrap_min_trials_per_variant": 3,
    "ucb_c": 1.0,
    "commit_window_runs": 10,
    "elim_min_trials": 6,
    "elim_min_mean_clean": 0.1,
    "elim_max_failure_rate": 0.9,
    "step_limits_overrides": {},
    "constraint_patches": {},
    "stats": {},
}

FORBIDDEN_SUBSTRINGS = [
    "ignore validators",
    "bypass allowlists",
    "write outside allowed paths",
    "mark step as done even if tests fail",
    "modify .orchestrator",
]


class OrchestratorError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclasses.dataclass(frozen=True)
class StepSpec:
    name: str
    role: str
    allowlist: Tuple[str, ...]
    prompt_agent: str
    optional: bool = False
    can_modify_agents_md: bool = False
    can_modify_prompts: bool = False
    can_modify_brief: bool = False
    can_modify_brief_yaml: bool = False
    max_changed_files: int = 60
    max_total_bytes_changed: int = 500_000
    max_deleted_files: int = 0


@dataclasses.dataclass
class RunWindowResult:
    exit_code: int
    stdout: str
    stderr: str
    changed_paths: List[str]
    deleted_paths: List[str]
    new_paths: List[str]
    bytes_changed: int
    invariant_errors: List[str]
    allowlist_errors: List[str]
    cap_errors: List[str]
    tracked_restore_paths: List[str]
    removed_new_paths: List[str]


@dataclasses.dataclass
class ValidatorResult:
    ok: bool
    error_codes: List[str]
    messages: List[str]


@dataclasses.dataclass
class CodexFeatureSupport:
    experimental_json: bool
    output_schema: bool


@dataclasses.dataclass
class BriefConfig:
    exists: bool
    parsed: Dict[str, object]


def run_cmd(
    args: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    check: bool = False,
    stdin_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(args),
        cwd=str(cwd),
        text=True,
        input=stdin_text,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise OrchestratorError(
            f"{ERROR_PREFIX}CMD_FAILED",
            f"Command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}",
        )
    return proc


def ensure_git_repo() -> None:
    proc = run_cmd(["git", "rev-parse", "--is-inside-work-tree"])
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise OrchestratorError(f"{ERROR_PREFIX}NOT_GIT", "Must run inside a Git repository.")


def ensure_python_version() -> None:
    if sys.version_info < (3, 11):
        raise OrchestratorError(f"{ERROR_PREFIX}PYTHON", "Python 3.11+ required.")


def ensure_codex_available() -> None:
    proc = run_cmd(["codex", "exec", "--help"])
    if proc.returncode != 0:
        raise OrchestratorError(f"{ERROR_PREFIX}CODEX", "codex exec not available/authenticated.")


def detect_codex_features() -> CodexFeatureSupport:
    proc = run_cmd(["codex", "exec", "--help"], check=True)
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return CodexFeatureSupport(
        experimental_json=("--experimental-json" in text),
        output_schema=("--output-schema" in text),
    )


def to_posix(path: Path) -> str:
    return path.as_posix()


def is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def iter_repo_files(include_untracked: bool = True) -> List[Path]:
    files: Set[Path] = set()
    tracked = run_cmd(["git", "ls-files"], check=True)
    for line in tracked.stdout.splitlines():
        if line.strip():
            files.add(REPO_ROOT / line.strip())
    if include_untracked:
        untracked = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], check=True)
        for line in untracked.stdout.splitlines():
            if line.strip():
                files.add(REPO_ROOT / line.strip())
    files = {p for p in files if p.exists() and p.is_file()}
    return sorted(files)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def snapshot_state() -> Dict[str, object]:
    files = iter_repo_files(include_untracked=True)
    hashes: Dict[str, str] = {}
    for f in files:
        rel = to_posix(f.relative_to(REPO_ROOT))
        hashes[rel] = file_sha256(f)

    head = run_cmd(["git", "rev-parse", "HEAD"], check=True).stdout.strip()
    staged = [x.strip() for x in run_cmd(["git", "diff", "--cached", "--name-only"], check=True).stdout.splitlines() if x.strip()]
    untracked = [x.strip() for x in run_cmd(["git", "ls-files", "--others", "--exclude-standard"], check=True).stdout.splitlines() if x.strip()]

    return {
        "hashes": hashes,
        "head": head,
        "staged": staged,
        "untracked": sorted(untracked),
    }


def changed_paths_from_snapshots(pre: Dict[str, object], post: Dict[str, object]) -> Tuple[List[str], List[str], List[str]]:
    pre_h: Dict[str, str] = pre["hashes"]  # type: ignore[assignment]
    post_h: Dict[str, str] = post["hashes"]  # type: ignore[assignment]

    all_paths = sorted(set(pre_h.keys()) | set(post_h.keys()))
    changed: List[str] = []
    deleted: List[str] = []
    new: List[str] = []

    for p in all_paths:
        if p not in pre_h and p in post_h:
            changed.append(p)
            new.append(p)
        elif p in pre_h and p not in post_h:
            changed.append(p)
            deleted.append(p)
        elif p in pre_h and p in post_h and pre_h[p] != post_h[p]:
            changed.append(p)

    return changed, deleted, new


def normalize_rel_path(rel: str) -> str:
    candidate = Path(rel)
    if candidate.is_absolute():
        raise OrchestratorError(f"{ERROR_PREFIX}PATH_ABS", f"Absolute path not allowed: {rel}")
    normalized = Path(os.path.normpath(rel))
    parts = normalized.parts
    if any(part == ".." for part in parts):
        raise OrchestratorError(f"{ERROR_PREFIX}PATH_TRAVERSAL", f"Path traversal rejected: {rel}")
    if str(normalized) == ".":
        raise OrchestratorError(f"{ERROR_PREFIX}PATH_DOT", f"Invalid path: {rel}")
    return normalized.as_posix()


def path_matches_glob(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return path == base or path.startswith(base + "/")
    return fnmatch.fnmatch(path, pattern)


def check_forbidden_changes(changed: Iterable[str]) -> List[str]:
    errs = []
    for p in changed:
        if p == ".git" or p.startswith(".git/"):
            errs.append(f"Forbidden .git modification: {p}")
        if p == ".orchestrator" or p.startswith(".orchestrator/"):
            errs.append(f"Forbidden .orchestrator modification during run window: {p}")
    return errs


def check_allowlist(step: StepSpec, changed: Iterable[str]) -> List[str]:
    errs: List[str] = []
    for raw in changed:
        try:
            p = normalize_rel_path(raw)
        except OrchestratorError as exc:
            errs.append(str(exc))
            continue

        full = REPO_ROOT / p
        if full.exists() and full.is_symlink():
            errs.append(f"Symlink path is not allowed: {p}")
            continue
        if full.exists() and not is_subpath(full.resolve(), REPO_ROOT):
            errs.append(f"Path escapes repository root: {p}")
            continue

        allowed = any(path_matches_glob(p, pat) for pat in step.allowlist)
        if not allowed:
            errs.append(f"Path not allowlisted for {step.name}: {p}")
    return errs


def bytes_changed_for_paths(paths: Iterable[str], pre: Dict[str, object], post: Dict[str, object]) -> int:
    pre_h: Dict[str, str] = pre["hashes"]  # type: ignore[assignment]
    post_h: Dict[str, str] = post["hashes"]  # type: ignore[assignment]
    total = 0
    for p in paths:
        pre_p = REPO_ROOT / p
        post_p = REPO_ROOT / p
        if p in pre_h and p in post_h:
            if post_p.exists():
                total += post_p.stat().st_size
            elif pre_p.exists():
                total += pre_p.stat().st_size
        elif p in post_h:
            if post_p.exists():
                total += post_p.stat().st_size
        elif p in pre_h:
            if pre_p.exists():
                total += pre_p.stat().st_size
    return total


def deterministic_revert(changed_paths: List[str], new_paths: List[str], allowed_new_paths: Optional[Set[str]] = None) -> Tuple[List[str], List[str]]:
    allowed_new_paths = allowed_new_paths or set()
    restore_paths = sorted([p for p in changed_paths if p not in new_paths])
    remove_paths = sorted([p for p in new_paths if p not in allowed_new_paths])

    if restore_paths:
        run_cmd(["git", "restore", "--worktree", "--"] + restore_paths, check=True)

    removed_actual: List[str] = []
    for rel in remove_paths:
        abs_path = REPO_ROOT / rel
        if abs_path.exists() or abs_path.is_symlink():
            if abs_path.is_dir() and not abs_path.is_symlink():
                shutil.rmtree(abs_path)
            else:
                abs_path.unlink(missing_ok=True)
            removed_actual.append(rel)

    # Remove empty parent dirs for removed paths, but never above repo root.
    for rel in removed_actual:
        parent = (REPO_ROOT / rel).parent
        while parent != REPO_ROOT and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    return restore_paths, removed_actual


def brief_exists() -> bool:
    return PROJECT_BRIEF_MD.exists()


def load_brief_config() -> BriefConfig:
    if not PROJECT_BRIEF_YAML.exists():
        return BriefConfig(exists=False, parsed={})
    raw = PROJECT_BRIEF_YAML.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrchestratorError("BRIEF_YAML_INVALID", f"PROJECT_BRIEF.yaml is not valid JSON subset YAML: {exc}")
    if not isinstance(parsed, dict):
        raise OrchestratorError("BRIEF_YAML_INVALID", "PROJECT_BRIEF.yaml must parse to an object")
    project_type = parsed.get("project_type")
    if not isinstance(project_type, str) or not project_type.strip():
        raise OrchestratorError("BRIEF_YAML_INVALID", "PROJECT_BRIEF.yaml must contain key 'project_type' as non-empty string")
    return BriefConfig(exists=True, parsed=parsed)


def required_brief_headings() -> List[str]:
    return [
        "# Layer 0",
        "# Layer 1",
        "# Layer 2",
    ]


def validate_required_heading(md_text: str, heading: str) -> bool:
    return heading in md_text


def parse_test_commands_from_test_md(test_md: Path) -> List[str]:
    txt = test_md.read_text(encoding="utf-8")
    heading_idx = txt.find("# How to run tests")
    if heading_idx < 0:
        raise OrchestratorError("TEST_CMD_MISSING", "TEST.md missing # How to run tests")

    tail = txt[heading_idx:]
    m = re.search(r"```[a-zA-Z0-9_-]*\n(.*?)\n```", tail, re.DOTALL)
    if not m:
        raise OrchestratorError("TEST_CMD_MISSING", "TEST.md missing fenced test command block")

    lines = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)

    if not lines:
        raise OrchestratorError("TEST_CMD_MISSING", "No executable test commands found")
    return lines


def parse_profile_test_commands(brief_cfg: BriefConfig, test_md_text: str) -> Optional[List[str]]:
    if not brief_cfg.exists:
        return None
    tests_cfg = brief_cfg.parsed.get("tests")
    if not isinstance(tests_cfg, dict):
        return None
    if tests_cfg.get("command_source") != "profile":
        return None
    commands = tests_cfg.get("commands")
    if not isinstance(commands, list) or not commands or any(not isinstance(x, str) or not x.strip() for x in commands):
        raise OrchestratorError("TEST_CMD_PROFILE_INVALID", "PROJECT_BRIEF.yaml tests.commands must be a non-empty string list")
    for cmd in commands:
        if cmd not in test_md_text:
            raise OrchestratorError("TEST_CMD_PROFILE_DOC_MISMATCH", f"TEST.md must document profiled command: {cmd}")
    return list(commands)


def run_test_commands(brief_cfg: BriefConfig) -> Tuple[bool, List[Dict[str, object]], Optional[str]]:
    test_md = REPO_ROOT / "TEST.md"
    if not test_md.exists():
        return False, [], "TEST_MD_MISSING"

    txt = test_md.read_text(encoding="utf-8")
    profiled = parse_profile_test_commands(brief_cfg, txt)
    if profiled is not None:
        commands = profiled
    else:
        try:
            commands = parse_test_commands_from_test_md(test_md)
        except OrchestratorError as exc:
            return False, [], exc.code

    results = []
    for cmd in commands:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            shell=True,
            text=True,
            capture_output=True,
        )
        results.append(
            {
                "command": cmd,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        )
        if proc.returncode != 0:
            return False, results, "TEST_EXIT_NONZERO"

    return True, results, None


def count_bullets(section_text: str) -> int:
    return sum(1 for line in section_text.splitlines() if line.strip().startswith("- "))


def section_slice(text: str, heading: str, next_headings: Sequence[str]) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    end = len(text)
    for h in next_headings:
        idx = text.find(h, start + len(heading))
        if idx >= 0:
            end = min(end, idx)
    return text[start:end]


def validate_base_files_and_structure(design_b: bool) -> ValidatorResult:
    codes: List[str] = []
    msgs: List[str] = []

    for f in REQUIRED_FILES_A:
        if not (REPO_ROOT / f).exists():
            codes.append("REQUIRED_FILE_MISSING")
            msgs.append(f"Missing required file: {f}")
    for d in REQUIRED_DIRS_A:
        if not (REPO_ROOT / d).is_dir():
            codes.append("REQUIRED_DIR_MISSING")
            msgs.append(f"Missing required dir: {d}")

    if design_b:
        for f in REQUIRED_FILES_B:
            if not (REPO_ROOT / f).exists():
                codes.append("REQUIRED_FILE_MISSING_B")
                msgs.append(f"Missing Design B required file: {f}")
        for d in REQUIRED_DIRS_B:
            if not (REPO_ROOT / d).is_dir():
                codes.append("REQUIRED_DIR_MISSING_B")
                msgs.append(f"Missing Design B required dir: {d}")

    if codes:
        return ValidatorResult(False, codes, msgs)
    return ValidatorResult(True, [], [])


def validate_requirements_md() -> ValidatorResult:
    path = REPO_ROOT / "REQUIREMENTS.md"
    if not path.exists():
        return ValidatorResult(False, ["REQ_MD_MISSING"], ["REQUIREMENTS.md missing"]) 

    txt = path.read_text(encoding="utf-8")
    required = ["# Overview", "# Scope", "# Non-Goals", "# Acceptance Criteria", "# Risks"]
    codes: List[str] = []
    msgs: List[str] = []
    for h in required:
        if h not in txt:
            codes.append("REQ_HEADING_MISSING")
            msgs.append(f"REQUIREMENTS.md missing heading: {h}")
    return ValidatorResult(not codes, codes, msgs)


def validate_test_md() -> ValidatorResult:
    path = REPO_ROOT / "TEST.md"
    if not path.exists():
        return ValidatorResult(False, ["TEST_MD_MISSING"], ["TEST.md missing"])

    txt = path.read_text(encoding="utf-8")
    codes: List[str] = []
    msgs: List[str] = []
    if "# How to run tests" not in txt:
        codes.append("TEST_HEADING_MISSING")
        msgs.append("Missing # How to run tests")
    if "# Environments" not in txt:
        codes.append("TEST_HEADING_MISSING")
        msgs.append("Missing # Environments")
    if not re.search(r"```[a-zA-Z0-9_-]*\n.*?\n```", txt, re.DOTALL):
        codes.append("TEST_CODEBLOCK_MISSING")
        msgs.append("Missing fenced code block with test command")
    return ValidatorResult(not codes, codes, msgs)


def validate_agent_tasks_md() -> ValidatorResult:
    path = REPO_ROOT / "AGENT_TASKS.md"
    if not path.exists():
        return ValidatorResult(False, ["AGENT_TASKS_MISSING"], ["AGENT_TASKS.md missing"])

    txt = path.read_text(encoding="utf-8")
    codes: List[str] = []
    msgs: List[str] = []

    if "# Agent Tasks" not in txt:
        codes.append("AGENT_TASKS_HEADING_MISSING")
        msgs.append("Missing # Agent Tasks")

    sections = ["Requirements", "Designer", "Frontend", "Backend", "QA"]
    for section in sections:
        heading = f"## {section}"
        if heading not in txt:
            codes.append("AGENT_TASKS_SECTION_MISSING")
            msgs.append(f"Missing section: {heading}")
            continue
        next_heads = [f"## {s}" for s in sections if s != section] + ["## Docs", "##"]
        stext = section_slice(txt, heading, next_heads)
        if count_bullets(stext) < 2:
            codes.append("AGENT_TASKS_TOO_FEW_TASKS")
            msgs.append(f"Section {heading} must contain at least 2 bullet tasks")

    if "Project Brief" not in txt:
        codes.append("AGENT_TASKS_BRIEF_REF_MISSING")
        msgs.append("AGENT_TASKS.md must reference Project Brief")

    return ValidatorResult(not codes, codes, msgs)


def validate_infra_files_if_required(brief_text: str, brief_cfg: BriefConfig) -> ValidatorResult:
    codes: List[str] = []
    msgs: List[str] = []
    needs_compose = False

    if "Docker Compose" in brief_text or "docker-compose.yml" in brief_text:
        needs_compose = True

    extra = brief_cfg.parsed.get("validators") if brief_cfg.exists else None
    if isinstance(extra, dict) and extra.get("require_docker_compose") is True:
        needs_compose = True

    if needs_compose:
        if not (REPO_ROOT / "docker-compose.yml").exists():
            codes.append("COMPOSE_MISSING")
            msgs.append("docker-compose.yml required")
        if not (REPO_ROOT / ".env.example").exists():
            codes.append("ENV_EXAMPLE_MISSING")
            msgs.append(".env.example required")
        gi = REPO_ROOT / ".gitignore"
        if not gi.exists() or ".env" not in gi.read_text(encoding="utf-8"):
            codes.append("GITIGNORE_ENV_MISSING")
            msgs.append(".gitignore must include .env")

    return ValidatorResult(not codes, codes, msgs)


def validate_project_brief_presence_and_content() -> ValidatorResult:
    if not PROJECT_BRIEF_MD.exists():
        return ValidatorResult(False, ["BRIEF_MISSING"], ["PROJECT_BRIEF.md missing"])
    txt = PROJECT_BRIEF_MD.read_text(encoding="utf-8")

    codes: List[str] = []
    msgs: List[str] = []
    for h in required_brief_headings():
        if h not in txt:
            codes.append("BRIEF_HEADING_MISSING")
            msgs.append(f"Missing brief heading: {h}")

    # Minimal product identity keyword checks to avoid overfitting.
    for kw in ["Web", "7", "12", "Safety", "MVP", "Acceptance criteria"]:
        if kw not in txt:
            codes.append("BRIEF_KEYWORD_MISSING")
            msgs.append(f"Brief missing required keyword token: {kw}")

    return ValidatorResult(not codes, codes, msgs)


def validate_agents_md(design_b: bool) -> ValidatorResult:
    if not design_b:
        return ValidatorResult(True, [], [])
    path = REPO_ROOT / "AGENTS.md"
    if not path.exists():
        return ValidatorResult(False, ["AGENTS_MISSING"], ["AGENTS.md missing for Design B"])

    txt = path.read_text(encoding="utf-8")
    required = [
        "# Global Rules",
        "# File Boundaries",
        "# How to Run Tests",
        "Do not modify /.orchestrator/**",
    ]
    codes: List[str] = []
    msgs: List[str] = []
    for token in required:
        if token not in txt:
            codes.append("AGENTS_CONTENT_MISSING")
            msgs.append(f"AGENTS.md missing token: {token}")

    return ValidatorResult(not codes, codes, msgs)


def validate_project_brief_yaml_if_present() -> ValidatorResult:
    if not PROJECT_BRIEF_YAML.exists():
        return ValidatorResult(True, [], [])
    try:
        _ = load_brief_config()
    except OrchestratorError as exc:
        return ValidatorResult(False, [exc.code], [str(exc)])
    return ValidatorResult(True, [], [])


def validate_design_b_prompt_skill_guardrails() -> ValidatorResult:
    codes: List[str] = []
    msgs: List[str] = []

    if PROMPTS_DIR.exists():
        for path in PROMPTS_DIR.rglob("*"):
            if not path.is_file():
                continue
            if path.stat().st_size > 64 * 1024:
                codes.append("PROMPT_FILE_TOO_LARGE")
                msgs.append(f"Prompt file exceeds 64KB: {to_posix(path.relative_to(REPO_ROOT))}")
            txt = path.read_text(encoding="utf-8", errors="replace").lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                if bad in txt:
                    codes.append("PROMPT_FORBIDDEN_SUBSTRING")
                    msgs.append(f"Prompt contains forbidden text '{bad}': {to_posix(path.relative_to(REPO_ROOT))}")
            if "disable gating" in txt or "proceed on failure" in txt:
                codes.append("PROMPT_GATING_BYPASS")
                msgs.append(f"Prompt attempts to disable gating: {to_posix(path.relative_to(REPO_ROOT))}")

    if SKILLS_DIR.exists():
        for skill in SKILLS_DIR.rglob("SKILL.md"):
            if skill.stat().st_size > 64 * 1024:
                codes.append("SKILL_TOO_LARGE")
                msgs.append(f"Skill file exceeds 64KB: {to_posix(skill.relative_to(REPO_ROOT))}")
            txt = skill.read_text(encoding="utf-8", errors="replace")
            if not txt.startswith("---\n"):
                codes.append("SKILL_FRONT_MATTER_MISSING")
                msgs.append(f"Skill missing YAML front matter: {to_posix(skill.relative_to(REPO_ROOT))}")
            else:
                end = txt.find("\n---", 4)
                if end < 0:
                    codes.append("SKILL_FRONT_MATTER_MISSING")
                    msgs.append(f"Skill missing closing YAML front matter: {to_posix(skill.relative_to(REPO_ROOT))}")
                else:
                    fm = txt[4:end]
                    if "name:" not in fm or "description:" not in fm:
                        codes.append("SKILL_FRONT_MATTER_KEYS_MISSING")
                        msgs.append(f"Skill front matter missing name/description: {to_posix(skill.relative_to(REPO_ROOT))}")

            lower = txt.lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                if bad in lower:
                    codes.append("SKILL_FORBIDDEN_SUBSTRING")
                    msgs.append(f"Skill contains forbidden text '{bad}': {to_posix(skill.relative_to(REPO_ROOT))}")

    return ValidatorResult(not codes, codes, msgs)


def merge_validator_results(results: Sequence[ValidatorResult]) -> ValidatorResult:
    codes: List[str] = []
    msgs: List[str] = []
    for r in results:
        if not r.ok:
            codes.extend(r.error_codes)
            msgs.extend(r.messages)
    return ValidatorResult(not codes, codes, msgs)


def compute_eval_score(
    design_b: bool,
    hard_invalid: bool,
    validators_ok: bool,
    tests_ok: bool,
    retries_beyond_first: int,
    fixer_runs: int,
    changed_files_total: int,
    required_ok: bool,
) -> int:
    if not design_b:
        return 0
    if hard_invalid:
        return -1

    score = 0
    if required_ok:
        score += 40
    if validators_ok:
        score += 30
    if tests_ok:
        score += 30
    score -= 5 * retries_beyond_first
    score -= 10 * fixer_runs
    score -= max(0, changed_files_total - 20)
    return max(0, score)


def default_steps(design_b: bool, backend_required: bool) -> List[StepSpec]:
    steps: List[StepSpec] = []

    release_allow = [
        "REQUIREMENTS.md",
        "TEST.md",
        "AGENT_TASKS.md",
        "docker-compose.yml",
        ".env.example",
        ".gitignore",
        "design/**",
        "frontend/**",
        "backend/**",
        "tests/**",
        "PROJECT_BRIEF.md",
        "PROJECT_BRIEF.yaml",
    ]
    if design_b:
        release_allow.append("AGENTS.md")

    steps.append(
        StepSpec(
            name="release_engineer",
            role="Release Engineer",
            allowlist=tuple(sorted(release_allow)),
            prompt_agent="release_engineer",
            can_modify_agents_md=design_b,
            can_modify_brief=True,
            can_modify_brief_yaml=True,
        )
    )

    steps.append(
        StepSpec(
            name="requirements",
            role="Requirements Analyst",
            allowlist=("REQUIREMENTS.md", "AGENT_TASKS.md"),
            prompt_agent="requirements",
        )
    )

    steps.append(
        StepSpec(
            name="designer",
            role="UX / Designer",
            allowlist=("design/**", "REQUIREMENTS.md"),
            prompt_agent="designer",
        )
    )

    steps.append(
        StepSpec(
            name="frontend",
            role="Frontend Dev",
            allowlist=("frontend/**", "tests/**", "TEST.md"),
            prompt_agent="frontend",
        )
    )

    if backend_required:
        steps.append(
            StepSpec(
                name="backend",
                role="Backend Dev",
                allowlist=("backend/**", "tests/**", "TEST.md", ".env.example", "docker-compose.yml"),
                prompt_agent="backend",
            )
        )

    steps.append(
        StepSpec(
            name="qa",
            role="QA Tester",
            allowlist=("tests/**", "TEST.md"),
            prompt_agent="qa",
        )
    )

    steps.append(
        StepSpec(
            name="docs",
            role="Docs Writer",
            allowlist=("REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md"),
            prompt_agent="docs",
        )
    )

    return steps


def prompt_variants_for_agent(agent: str, design_b: bool) -> List[Tuple[str, str]]:
    variants: List[Tuple[str, str]] = []

    if design_b:
        agent_dir = PROMPTS_DIR / agent
        if agent_dir.exists() and any(agent_dir.iterdir()):
            files = sorted([p for p in agent_dir.rglob("*.txt") if p.is_file()], key=lambda p: to_posix(p.relative_to(REPO_ROOT)))
            for p in files:
                variants.append((to_posix(p.relative_to(REPO_ROOT)), p.read_text(encoding="utf-8")))
            if variants:
                return variants

    orch_agent_dir = PROMPT_TEMPLATE_DIR / agent
    if orch_agent_dir.exists() and any(orch_agent_dir.iterdir()):
        files = sorted([p for p in orch_agent_dir.rglob("*.txt") if p.is_file()], key=lambda p: to_posix(p.relative_to(REPO_ROOT)))
        for p in files:
            variants.append((to_posix(p.relative_to(REPO_ROOT)), p.read_text(encoding="utf-8")))
        if variants:
            return variants

    # Embedded deterministic fallback variants.
    base = (
        "You are the {role} specialist.\n"
        "Follow only the allowed paths for this step.\n"
        "Do not modify /.orchestrator/** or .git/**.\n"
        "Use deterministic, minimal edits.\n"
        "If a project brief is provided below, do not contradict it.\n"
    )
    variants.append((f"embedded/{agent}/v1", base + "Variant: strict minimal edits."))
    variants.append((f"embedded/{agent}/v2", base + "Variant: produce complete output in one pass."))
    return variants


def hash_prompt_epoch(agent: str, variants: List[Tuple[str, str]], design_b: bool) -> str:
    h = hashlib.sha256()
    for vid, txt in variants:
        h.update(vid.encode("utf-8"))
        h.update(b"\n")
        h.update(txt.encode("utf-8"))
        h.update(b"\n")

    if design_b and SKILLS_DIR.exists():
        skill_files = sorted([p for p in SKILLS_DIR.rglob("*") if p.is_file()], key=lambda p: to_posix(p.relative_to(REPO_ROOT)))
        for p in skill_files:
            h.update(to_posix(p.relative_to(REPO_ROOT)).encode("utf-8"))
            h.update(file_sha256(p).encode("utf-8"))
            h.update(b"\n")

    return h.hexdigest()


def policy_key(agent: str, epoch: str) -> str:
    return f"{agent}::{epoch}"


def ensure_policy_shape(policy: Dict[str, object]) -> Dict[str, object]:
    merged = dict(DEFAULT_POLICY)
    merged.update(policy)
    for key in ["step_limits_overrides", "constraint_patches", "stats"]:
        if not isinstance(merged.get(key), dict):
            merged[key] = {}
    return merged


def get_variant_stats_bucket(policy: Dict[str, object], agent: str, epoch: str) -> Dict[str, object]:
    stats = policy["stats"]  # type: ignore[index]
    assert isinstance(stats, dict)
    key = policy_key(agent, epoch)
    bucket = stats.get(key)
    if not isinstance(bucket, dict):
        bucket = {
            "attempts": {},
            "passes": {},
            "clean_passes": {},
            "last_rr_index": -1,
            "commit": {"active": False, "best": None, "remaining": 0, "consecutive_not_clean_best": 0},
            "eliminated": [],
            "selection_strategy": None,
        }
        stats[key] = bucket
    for sub in ["attempts", "passes", "clean_passes"]:
        if not isinstance(bucket.get(sub), dict):
            bucket[sub] = {}
    if not isinstance(bucket.get("eliminated"), list):
        bucket["eliminated"] = []
    if not isinstance(bucket.get("commit"), dict):
        bucket["commit"] = {"active": False, "best": None, "remaining": 0, "consecutive_not_clean_best": 0}
    return bucket


def select_variant(policy: Dict[str, object], agent: str, epoch: str, variant_ids_sorted: List[str]) -> str:
    bucket = get_variant_stats_bucket(policy, agent, epoch)
    attempts: Dict[str, int] = {k: int(v) for k, v in bucket["attempts"].items()}  # type: ignore[index]
    passes: Dict[str, int] = {k: int(v) for k, v in bucket["passes"].items()}  # type: ignore[index]
    clean_passes: Dict[str, int] = {k: int(v) for k, v in bucket["clean_passes"].items()}  # type: ignore[index]

    for vid in variant_ids_sorted:
        attempts.setdefault(vid, 0)
        passes.setdefault(vid, 0)
        clean_passes.setdefault(vid, 0)

    bucket["attempts"] = attempts
    bucket["passes"] = passes
    bucket["clean_passes"] = clean_passes

    bootstrap_min = int(policy.get("bootstrap_min_trials_per_variant", 3))
    needs_bootstrap = any(attempts[v] < bootstrap_min for v in variant_ids_sorted)

    if needs_bootstrap:
        last_rr = int(bucket.get("last_rr_index", -1))
        rr_index = (last_rr + 1) % len(variant_ids_sorted)
        bucket["last_rr_index"] = rr_index
        return variant_ids_sorted[rr_index]

    strategy = str(policy.get("selection_strategy", "ucb1"))
    if strategy not in {"ucb1", "explore_then_commit", "rr_elimination"}:
        strategy = "ucb1"
    bucket["selection_strategy"] = strategy

    def mean_clean(v: str) -> float:
        return clean_passes.get(v, 0) / max(1, attempts.get(v, 0))

    if strategy == "ucb1":
        total_attempts = sum(attempts.get(v, 0) for v in variant_ids_sorted)
        c = float(policy.get("ucb_c", 1.0))
        scored = []
        for v in variant_ids_sorted:
            score = mean_clean(v) + c * math.sqrt(math.log(max(1, total_attempts)) / max(1, attempts.get(v, 0)))
            scored.append((score, v))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]

    if strategy == "explore_then_commit":
        commit: Dict[str, object] = bucket["commit"]  # type: ignore[assignment]
        if bool(commit.get("active")):
            best = str(commit.get("best"))
            remaining = int(commit.get("remaining", 0))
            if remaining > 0 and best in variant_ids_sorted:
                commit["remaining"] = remaining - 1
                return best

        best = sorted(variant_ids_sorted, key=lambda v: (-mean_clean(v), v))[0]
        commit_window = int(policy.get("commit_window_runs", 10))
        commit["active"] = True
        commit["best"] = best
        commit["remaining"] = commit_window - 1
        commit.setdefault("consecutive_not_clean_best", 0)
        return best

    # rr_elimination
    eliminated = set(str(x) for x in bucket.get("eliminated", []))
    active = [v for v in variant_ids_sorted if v not in eliminated]
    if not active:
        bucket["eliminated"] = []
        last_rr = int(bucket.get("last_rr_index", -1))
        rr_index = (last_rr + 1) % len(variant_ids_sorted)
        bucket["last_rr_index"] = rr_index
        return variant_ids_sorted[rr_index]

    last_rr = int(bucket.get("last_rr_index", -1))
    active_idx = (last_rr + 1) % len(active)
    chosen = active[active_idx]

    elim_min_trials = int(policy.get("elim_min_trials", 6))
    elim_min_mean_clean = float(policy.get("elim_min_mean_clean", 0.1))
    elim_max_failure_rate = float(policy.get("elim_max_failure_rate", 0.9))

    for v in active:
        a = attempts.get(v, 0)
        p = passes.get(v, 0)
        mc = mean_clean(v)
        failure_rate = 1.0 - (p / max(1, a))
        if a >= elim_min_trials and (mc < elim_min_mean_clean or failure_rate > elim_max_failure_rate):
            eliminated.add(v)

    bucket["eliminated"] = sorted(eliminated)
    bucket["last_rr_index"] = variant_ids_sorted.index(chosen)
    return chosen


def update_variant_stats(
    policy: Dict[str, object],
    agent: str,
    epoch: str,
    variant_id: str,
    passed: bool,
    clean_pass: bool,
    retries_used: int,
) -> None:
    bucket = get_variant_stats_bucket(policy, agent, epoch)
    attempts: Dict[str, int] = bucket["attempts"]  # type: ignore[assignment]
    passes: Dict[str, int] = bucket["passes"]  # type: ignore[assignment]
    clean_passes: Dict[str, int] = bucket["clean_passes"]  # type: ignore[assignment]

    attempts[variant_id] = int(attempts.get(variant_id, 0)) + 1
    if passed:
        passes[variant_id] = int(passes.get(variant_id, 0)) + 1
    if clean_pass:
        clean_passes[variant_id] = int(clean_passes.get(variant_id, 0)) + 1

    # deterministic explore_then_commit state transitions based on outcomes
    if str(policy.get("selection_strategy", "ucb1")) == "explore_then_commit":
        commit = bucket.get("commit")
        if isinstance(commit, dict):
            best = commit.get("best")
            if isinstance(best, str) and best == variant_id:
                if clean_pass:
                    commit["consecutive_not_clean_best"] = 0
                else:
                    c = int(commit.get("consecutive_not_clean_best", 0)) + 1
                    commit["consecutive_not_clean_best"] = c
                    a = attempts.get(best, 0)
                    mc = clean_passes.get(best, 0) / max(1, a)
                    if c >= 2 or (a >= 10 and mc < 0.3):
                        commit["active"] = False
                        commit["remaining"] = 0


def hash_file_if_exists(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    return file_sha256(path)


def build_step_prompt(
    step: StepSpec,
    variant_text: str,
    brief_text: str,
    brief_cfg: BriefConfig,
    retry_index: int,
    constraints_patch: str,
) -> str:
    project_type = ""
    if brief_cfg.exists:
        pt = brief_cfg.parsed.get("project_type")
        if isinstance(pt, str):
            project_type = pt

    prompt = []
    prompt.append(variant_text.strip())
    prompt.append("")
    prompt.append(f"Role: {step.role}")
    prompt.append(f"Step: {step.name}")
    prompt.append(f"Retry attempt index: {retry_index}")
    prompt.append("Allowed paths for this step:")
    for p in step.allowlist:
        prompt.append(f"- {p}")
    prompt.append("Hard rules:")
    prompt.append("- Do not modify /.orchestrator/**")
    prompt.append("- Do not modify .git/**")
    prompt.append("- Do not modify files outside the allowlist")
    if project_type:
        prompt.append(f"- project_type from PROJECT_BRIEF.yaml: {project_type}")
    prompt.append("- Do not contradict PROJECT_BRIEF.md")

    if constraints_patch.strip():
        prompt.append("")
        prompt.append("Additional deterministic constraints from prior failures:")
        prompt.append(constraints_patch.strip())

    prompt.append("")
    prompt.append("Project brief (Layer 0-2 reference, do not contradict):")
    prompt.append(brief_text.strip())

    return "\n".join(prompt).strip() + "\n"


def codex_exec_step(prompt_text: str, features: CodexFeatureSupport, temp_dir: Path) -> Tuple[int, str, str, Optional[str]]:
    cmd = ["codex", "exec", "-"]
    jsonl_path = None
    if features.experimental_json:
        jsonl_path = str(temp_dir / "codex_events.jsonl")
        cmd.extend(["--experimental-json", "--output-last-message", str(temp_dir / "last_message.txt")])

    proc = run_cmd(cmd, stdin_text=prompt_text)
    return proc.returncode, proc.stdout, proc.stderr, jsonl_path


def append_jsonl(path: Path, record: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def get_constraint_patch(policy: Dict[str, object], step_name: str) -> str:
    patches = policy.get("constraint_patches", {})
    if not isinstance(patches, dict):
        return ""
    patch = patches.get(step_name, "")
    if not isinstance(patch, str):
        return ""
    lines = patch.splitlines()[:8]
    return "\n".join(lines)


def maybe_update_constraint_patch(policy: Dict[str, object], step_name: str, error_codes: List[str]) -> None:
    patches = policy.get("constraint_patches")
    if not isinstance(patches, dict):
        patches = {}
        policy["constraint_patches"] = patches

    known = sorted(set(error_codes))[:8]
    if not known:
        return
    lines = [f"- Previous deterministic failure code: {c}. Avoid triggering it." for c in known]
    patches[step_name] = "\n".join(lines[:8])


def apply_step_limits_overrides(policy: Dict[str, object], step: StepSpec) -> StepSpec:
    overrides = policy.get("step_limits_overrides", {})
    if not isinstance(overrides, dict):
        return step
    ov = overrides.get(step.name)
    if not isinstance(ov, dict):
        return step

    def tightened(current: int, key: str) -> int:
        if key not in ov:
            return current
        try:
            val = int(ov[key])
        except (TypeError, ValueError):
            return current
        return min(current, val)

    return dataclasses.replace(
        step,
        max_changed_files=tightened(step.max_changed_files, "max_changed_files"),
        max_total_bytes_changed=tightened(step.max_total_bytes_changed, "max_total_bytes_changed"),
        max_deleted_files=tightened(step.max_deleted_files, "max_deleted_files"),
    )


def tighten_limits_on_failure(policy: Dict[str, object], step: StepSpec) -> None:
    overrides = policy.get("step_limits_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
        policy["step_limits_overrides"] = overrides

    curr = overrides.get(step.name)
    if not isinstance(curr, dict):
        curr = {}

    # Tighten only.
    curr["max_changed_files"] = min(step.max_changed_files, max(5, int(step.max_changed_files * 0.8)))
    curr["max_total_bytes_changed"] = min(step.max_total_bytes_changed, max(20_000, int(step.max_total_bytes_changed * 0.8)))
    curr["max_deleted_files"] = min(step.max_deleted_files, 0)
    overrides[step.name] = curr


def prepare_run_artifact_dir(run_id: str) -> Path:
    p = RUNS_DIR / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_step_once(
    step: StepSpec,
    step_attempt: int,
    run_id: str,
    policy: Dict[str, object],
    design_b: bool,
    features: CodexFeatureSupport,
    brief_text: str,
    brief_cfg: BriefConfig,
) -> RunWindowResult:
    step = apply_step_limits_overrides(policy, step)
    variants = prompt_variants_for_agent(step.prompt_agent, design_b)
    variants = sorted(variants, key=lambda x: x[0])
    epoch = hash_prompt_epoch(step.prompt_agent, variants, design_b)
    variant_ids = [v[0] for v in variants]
    chosen_id = select_variant(policy, step.prompt_agent, epoch, variant_ids)
    variant_text = next(v[1] for v in variants if v[0] == chosen_id)

    constraint_patch = get_constraint_patch(policy, step.name)
    prompt = build_step_prompt(step, variant_text, brief_text, brief_cfg, step_attempt, constraint_patch)

    pre = snapshot_state()
    if pre["staged"]:
        raise OrchestratorError(f"{ERROR_PREFIX}PRE_STAGED_NOT_EMPTY", "git diff --cached must be empty at run start")

    pre_git_head = str(pre["head"])
    pre_git_index_hash = hash_file_if_exists(REPO_ROOT / ".git" / "index")

    with tempfile.TemporaryDirectory(prefix="orchestrator-codex-") as tmp:
        temp_dir = Path(tmp)
        exit_code, stdout, stderr, _jsonl = codex_exec_step(prompt, features, temp_dir)

    post = snapshot_state()

    changed, deleted, new = changed_paths_from_snapshots(pre, post)
    invariant_errors = check_forbidden_changes(changed)

    post_git_head = str(post["head"])
    if pre_git_head != post_git_head:
        invariant_errors.append("Git HEAD changed during codex run")

    if post["staged"]:
        invariant_errors.append("git diff --cached not empty after codex run")

    post_git_index_hash = hash_file_if_exists(REPO_ROOT / ".git" / "index")
    if pre_git_index_hash is not None and post_git_index_hash is not None and pre_git_index_hash != post_git_index_hash:
        invariant_errors.append(".git/index changed during codex run")

    allowlist_errors = check_allowlist(step, changed)

    cap_errors: List[str] = []
    changed_files_count = len(changed)
    if changed_files_count > step.max_changed_files:
        cap_errors.append(
            f"Changed files cap exceeded: {changed_files_count}>{step.max_changed_files}"
        )

    total_bytes = bytes_changed_for_paths(changed, pre, post)
    if total_bytes > step.max_total_bytes_changed:
        cap_errors.append(
            f"Byte cap exceeded: {total_bytes}>{step.max_total_bytes_changed}"
        )

    if len(deleted) > step.max_deleted_files:
        cap_errors.append(
            f"Deleted files cap exceeded: {len(deleted)}>{step.max_deleted_files}"
        )

    must_revert = bool(invariant_errors or allowlist_errors or cap_errors)
    restore_paths: List[str] = []
    removed_new_paths: List[str] = []

    if must_revert:
        restore_paths, removed_new_paths = deterministic_revert(changed, new)

    # Selection update must occur deterministically based on final outcome for this attempt.
    passed = not must_revert and exit_code == 0
    clean = passed and step_attempt == 0
    update_variant_stats(policy, step.prompt_agent, epoch, chosen_id, passed, clean, step_attempt)

    run_art_dir = prepare_run_artifact_dir(run_id)
    append_jsonl(
        run_art_dir / "selection_log.jsonl",
        {
            "timestamp": int(time.time()),
            "step": step.name,
            "attempt": step_attempt + 1,
            "agent": step.prompt_agent,
            "prompt_epoch_id": epoch,
            "variant_id": chosen_id,
            "strategy": policy.get("selection_strategy", "ucb1"),
            "bootstrap_min_trials_per_variant": policy.get("bootstrap_min_trials_per_variant", 3),
        },
    )

    append_jsonl(
        run_art_dir / "step_attempts.jsonl",
        {
            "timestamp": int(time.time()),
            "step": step.name,
            "attempt": step_attempt + 1,
            "exit_code": exit_code,
            "changed_paths": changed,
            "deleted_paths": deleted,
            "new_paths": new,
            "invariant_errors": invariant_errors,
            "allowlist_errors": allowlist_errors,
            "cap_errors": cap_errors,
        },
    )

    if stdout:
        (run_art_dir / f"{step.name}.attempt{step_attempt+1}.stdout.log").write_text(stdout, encoding="utf-8")
    if stderr:
        (run_art_dir / f"{step.name}.attempt{step_attempt+1}.stderr.log").write_text(stderr, encoding="utf-8")

    return RunWindowResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        changed_paths=changed,
        deleted_paths=deleted,
        new_paths=new,
        bytes_changed=total_bytes,
        invariant_errors=invariant_errors,
        allowlist_errors=allowlist_errors,
        cap_errors=cap_errors,
        tracked_restore_paths=restore_paths,
        removed_new_paths=removed_new_paths,
    )


def run_fixer_if_possible(
    step: StepSpec,
    failure_codes: List[str],
    run_id: str,
    features: CodexFeatureSupport,
    brief_text: str,
    brief_cfg: BriefConfig,
) -> bool:
    supported = {
        "REQUIRED_FILE_MISSING",
        "REQUIRED_DIR_MISSING",
        "REQ_HEADING_MISSING",
        "TEST_HEADING_MISSING",
        "TEST_CODEBLOCK_MISSING",
        "AGENT_TASKS_HEADING_MISSING",
        "AGENT_TASKS_SECTION_MISSING",
    }
    if not any(c in supported for c in failure_codes):
        return False

    minimal_allow = ["REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md", "design/**", "frontend/**", "backend/**", "tests/**"]
    fixer_step = dataclasses.replace(step, name=f"{step.name}_fixer", allowlist=tuple(minimal_allow))

    prompt = (
        "You are a deterministic fixer.\n"
        "Fix ONLY the specific deterministic validator failures listed below.\n"
        "Do not modify /.orchestrator/** or .git/**.\n"
        "Do not edit unrelated files.\n"
        f"Failures: {', '.join(sorted(set(failure_codes)))}\n"
        "Project brief (must not be contradicted):\n"
        f"{brief_text}\n"
    )

    pre = snapshot_state()
    with tempfile.TemporaryDirectory(prefix="orchestrator-fixer-") as _tmp:
        proc = run_cmd(["codex", "exec", "-"], stdin_text=prompt)
    post = snapshot_state()

    changed, _deleted, new = changed_paths_from_snapshots(pre, post)
    invariant_errors = check_forbidden_changes(changed)
    allowlist_errors = check_allowlist(fixer_step, changed)

    run_art_dir = prepare_run_artifact_dir(run_id)
    append_jsonl(
        run_art_dir / "fixers.jsonl",
        {
            "timestamp": int(time.time()),
            "step": step.name,
            "exit_code": proc.returncode,
            "failure_codes": sorted(set(failure_codes)),
            "changed_paths": changed,
            "invariant_errors": invariant_errors,
            "allowlist_errors": allowlist_errors,
        },
    )

    if proc.returncode != 0 or invariant_errors or allowlist_errors:
        deterministic_revert(changed, new)
        return False

    return True


def should_backend_be_required(brief_text: str, brief_cfg: BriefConfig) -> bool:
    if "Backend REQUIRED" in brief_text:
        return True
    b = brief_cfg.parsed.get("backend_required") if brief_cfg.exists else None
    return bool(b is True)


def lock_violation_in_changes(step: StepSpec, changed_paths: Sequence[str], design_b: bool) -> List[str]:
    errs: List[str] = []
    changed_set = set(changed_paths)

    if brief_exists() and not step.can_modify_brief and "PROJECT_BRIEF.md" in changed_set:
        errs.append("PROJECT_BRIEF_LOCKED")

    if PROJECT_BRIEF_YAML.exists() and not step.can_modify_brief_yaml and "PROJECT_BRIEF.yaml" in changed_set:
        errs.append("PROJECT_BRIEF_YAML_LOCKED")

    if design_b and AGENTS_MD.exists() and not step.can_modify_agents_md and "AGENTS.md" in changed_set:
        errs.append("AGENTS_LOCKED")

    if not step.can_modify_prompts:
        for p in changed_paths:
            if p == "prompts" or p.startswith("prompts/"):
                errs.append("PROMPTS_RESTRICTED")
                break
            if p == ".codex/skills" or p.startswith(".codex/skills/"):
                errs.append("SKILLS_RESTRICTED")
                break

    return errs


def validate_all(design_b: bool, brief_cfg: BriefConfig) -> ValidatorResult:
    brief_text = PROJECT_BRIEF_MD.read_text(encoding="utf-8") if PROJECT_BRIEF_MD.exists() else ""
    results = [
        validate_base_files_and_structure(design_b),
        validate_project_brief_presence_and_content(),
        validate_project_brief_yaml_if_present(),
        validate_requirements_md(),
        validate_test_md(),
        validate_agent_tasks_md(),
        validate_agents_md(design_b),
        validate_infra_files_if_required(brief_text, brief_cfg),
    ]
    if design_b:
        results.append(validate_design_b_prompt_skill_guardrails())
    return merge_validator_results(results)


def maybe_prompt_library_bootstrap(
    design_b: bool,
    features: CodexFeatureSupport,
    brief_text: str,
    brief_cfg: BriefConfig,
    run_id: str,
) -> Tuple[bool, int]:
    if not design_b:
        return False, 0

    prompts_missing = (not PROMPTS_DIR.exists()) or (PROMPTS_DIR.exists() and not any(PROMPTS_DIR.rglob("*")))
    skills_missing = (not SKILLS_DIR.exists()) or (SKILLS_DIR.exists() and not any(SKILLS_DIR.rglob("*")))
    if not (prompts_missing or skills_missing):
        return False, 0

    step = StepSpec(
        name="prompt_library_bootstrap",
        role="Prompt Library Bootstrap",
        allowlist=("prompts/**", ".codex/skills/**"),
        prompt_agent="prompt_library_bootstrap",
        can_modify_prompts=True,
    )

    prompt = (
        "Create prompt library and skill files for all agents.\n"
        "Allowed paths ONLY: /prompts/** and /.codex/skills/**\n"
        "For each agent, create 2-5 prompt variants as .txt and a SKILL.md with YAML front matter including name and description.\n"
        "Do not modify any other paths.\n"
        "Project brief (must not be contradicted):\n"
        f"{brief_text}\n"
    )

    pre = snapshot_state()
    with tempfile.TemporaryDirectory(prefix="orchestrator-bootstrap-") as _tmp:
        proc = run_cmd(["codex", "exec", "-"], stdin_text=prompt)
    post = snapshot_state()

    changed, _deleted, new = changed_paths_from_snapshots(pre, post)
    invariant_errors = check_forbidden_changes(changed)
    allowlist_errors = check_allowlist(step, changed)

    if proc.returncode != 0 or invariant_errors or allowlist_errors:
        deterministic_revert(changed, new)
        return True, 1

    guard = validate_design_b_prompt_skill_guardrails()
    if not guard.ok:
        deterministic_revert(changed, new)
        return True, 1

    return True, 0


def run_prompt_tuner_once(
    run_id: str,
    policy: Dict[str, object],
    features: CodexFeatureSupport,
    brief_text: str,
    brief_cfg: BriefConfig,
) -> Tuple[bool, List[str], List[str], List[str]]:
    step = StepSpec(
        name="prompt_tuner",
        role="Prompt Tuner",
        allowlist=("prompts/**", ".codex/skills/**"),
        prompt_agent="prompt_tuner",
        can_modify_prompts=True,
    )
    result = run_step_once(
        step=step,
        step_attempt=0,
        run_id=run_id,
        policy=policy,
        design_b=True,
        features=features,
        brief_text=brief_text,
        brief_cfg=brief_cfg,
    )
    errors = result.invariant_errors + result.allowlist_errors + result.cap_errors
    guard = validate_design_b_prompt_skill_guardrails()
    if not guard.ok:
        errors.extend(guard.error_codes)
        deterministic_revert(result.changed_paths, result.new_paths)
    ok = result.exit_code == 0 and not errors
    return ok, errors, result.changed_paths, result.new_paths


def execute_specialist_steps(
    *,
    run_id: str,
    steps: Sequence[StepSpec],
    policy: Dict[str, object],
    design_b: bool,
    features: CodexFeatureSupport,
    brief_text: str,
    brief_cfg: BriefConfig,
) -> Dict[str, object]:
    retries_beyond_first_total = 0
    fixer_runs_total = 0
    changed_files_total = 0
    hard_invalid = False

    summary: Dict[str, object] = {
        "steps": [],
    }

    for step in steps:
        attempts_limit = 3
        step_ok = False
        step_errors: List[str] = []

        for attempt in range(attempts_limit):
            result = run_step_once(
                step=step,
                step_attempt=attempt,
                run_id=run_id,
                policy=policy,
                design_b=design_b,
                features=features,
                brief_text=brief_text,
                brief_cfg=brief_cfg,
            )

            changed_files_total += len(result.changed_paths)
            lock_errors = lock_violation_in_changes(step, result.changed_paths, design_b)

            if result.invariant_errors or result.allowlist_errors or result.cap_errors or lock_errors:
                errs = result.invariant_errors + result.allowlist_errors + result.cap_errors + lock_errors
                step_errors.extend(errs)
                maybe_update_constraint_patch(policy, step.name, ["ALLOWLIST_OR_INVARIANT_FAIL"])
                tighten_limits_on_failure(policy, step)
                if attempt > 0:
                    retries_beyond_first_total += 1
                continue

            if result.exit_code != 0:
                step_errors.append(f"codex exit nonzero for {step.name}: {result.exit_code}")
                maybe_update_constraint_patch(policy, step.name, ["CODEX_EXIT_NONZERO"])
                if attempt > 0:
                    retries_beyond_first_total += 1
                continue

            # Success for this step attempt, move on.
            step_ok = True
            if attempt > 0:
                retries_beyond_first_total += attempt
            break

        if not step_ok:
            # Optional narrow fixer attempt.
            fixer_ok = run_fixer_if_possible(step, ["VALIDATOR_FIX"], run_id, features, brief_text, brief_cfg)
            if fixer_ok:
                fixer_runs_total += 1
                step_ok = True
            else:
                hard_invalid = True

        summary["steps"].append(
            {
                "step": step.name,
                "ok": step_ok,
                "errors": step_errors,
            }
        )

        if not step_ok:
            break

    validators = validate_all(design_b, brief_cfg)
    tests_ok, test_results, tests_error = run_test_commands(brief_cfg)
    required_ok = validate_base_files_and_structure(design_b).ok

    summary["validator_errors"] = validators.messages
    summary["validator_error_codes"] = validators.error_codes
    summary["tests_ok"] = tests_ok
    summary["tests_error"] = tests_error
    summary["test_results"] = test_results
    summary["hard_invalid"] = hard_invalid
    summary["retries_beyond_first_total"] = retries_beyond_first_total
    summary["fixer_runs_total"] = fixer_runs_total
    summary["changed_files_total"] = changed_files_total
    summary["validators_ok"] = validators.ok
    summary["required_ok"] = required_ok
    summary["tests_ok"] = tests_ok
    score = compute_eval_score(
        design_b=design_b,
        hard_invalid=hard_invalid,
        validators_ok=validators.ok,
        tests_ok=tests_ok,
        retries_beyond_first=retries_beyond_first_total,
        fixer_runs=fixer_runs_total,
        changed_files_total=changed_files_total,
        required_ok=required_ok,
    )
    summary["score"] = score
    return summary


def run_pipeline(args: argparse.Namespace) -> int:
    ensure_python_version()
    ensure_git_repo()
    ensure_codex_available()

    features = detect_codex_features()

    ORCH_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if args.design_b:
        EVALS_DIR.mkdir(parents=True, exist_ok=True)

    policy = ensure_policy_shape(load_json(POLICY_PATH, DEFAULT_POLICY))

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_art_dir = prepare_run_artifact_dir(run_id)
    brief_cfg = load_brief_config()

    if not PROJECT_BRIEF_MD.exists():
        raise OrchestratorError("BRIEF_MISSING", "PROJECT_BRIEF.md must exist before running pipeline")
    brief_text = PROJECT_BRIEF_MD.read_text(encoding="utf-8")

    backend_required = should_backend_be_required(brief_text, brief_cfg)
    steps = default_steps(args.design_b, backend_required)

    bootstrap_ran, bootstrap_fail = maybe_prompt_library_bootstrap(args.design_b, features, brief_text, brief_cfg, run_id)
    if bootstrap_fail:
        write_json(POLICY_PATH, policy)
        return CODE_VALIDATION

    baseline = execute_specialist_steps(
        run_id=run_id,
        steps=steps,
        policy=policy,
        design_b=args.design_b,
        features=features,
        brief_text=brief_text,
        brief_cfg=brief_cfg,
    )
    run_summary = {
        "run_id": run_id,
        "design_b": bool(args.design_b),
        "features": dataclasses.asdict(features),
        "bootstrap_prompt_library": bootstrap_ran,
        "baseline": baseline,
        "tuner": None,
        "final": baseline,
    }

    if args.design_b:
        prompt_tuner_ok, tuner_errors, tuner_changed, tuner_new = run_prompt_tuner_once(
            run_id=run_id,
            policy=policy,
            features=features,
            brief_text=brief_text,
            brief_cfg=brief_cfg,
        )
        tuner_record = {
            "ran": True,
            "ok": prompt_tuner_ok,
            "errors": tuner_errors,
            "changed_paths": tuner_changed,
        }
        run_summary["tuner"] = tuner_record

        if prompt_tuner_ok:
            regression = execute_specialist_steps(
                run_id=run_id,
                steps=steps,
                policy=policy,
                design_b=args.design_b,
                features=features,
                brief_text=brief_text,
                brief_cfg=brief_cfg,
            )
            baseline_score = int(baseline.get("score", 0))
            tuned_score = int(regression.get("score", 0))
            accept = tuned_score > baseline_score and bool(regression.get("validators_ok")) and bool(regression.get("tests_ok")) and not bool(regression.get("hard_invalid"))
            tuner_record["regression"] = regression
            tuner_record["accepted"] = accept
            tuner_record["baseline_score"] = baseline_score
            tuner_record["tuned_score"] = tuned_score
            if accept:
                run_summary["final"] = regression
            else:
                deterministic_revert(tuner_changed, tuner_new)
                run_summary["final"] = baseline
        else:
            run_summary["final"] = baseline

    final = run_summary["final"]  # type: ignore[assignment]
    write_json(
        run_art_dir / "test_results.json",
        {"ok": bool(final.get("tests_ok")), "error": final.get("tests_error"), "results": final.get("test_results", [])},
    )

    write_json(run_art_dir / "run_summary.json", run_summary)

    if args.design_b:
        write_json(
            EVALS_DIR / f"{run_id}.json",
            {
                "run_id": run_id,
                "score": int(final.get("score", 0)),
                "hard_invalid": bool(final.get("hard_invalid")),
                "validators_ok": bool(final.get("validators_ok")),
                "tests_ok": bool(final.get("tests_ok")),
                "retries_beyond_first": int(final.get("retries_beyond_first_total", 0)),
                "fixer_runs": int(final.get("fixer_runs_total", 0)),
                "changed_files_total": int(final.get("changed_files_total", 0)),
            },
        )

    write_json(POLICY_PATH, policy)

    if bool(final.get("hard_invalid")):
        return CODE_INVARIANT
    if not bool(final.get("validators_ok")):
        return CODE_VALIDATION
    if not bool(final.get("tests_ok")):
        return CODE_TEST_FAIL
    return CODE_SUCCESS


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deterministic Codex orchestrator")
    p.add_argument("--design-b", action="store_true", help="Enable Design B prompt/skill loop checks")
    p.add_argument("--dry-validate", action="store_true", help="Run only deterministic validators and tests")
    return p.parse_args(argv)


def run_dry_validate(args: argparse.Namespace) -> int:
    ensure_python_version()
    ensure_git_repo()

    ORCH_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    brief_cfg = load_brief_config()
    validators = validate_all(args.design_b, brief_cfg)
    tests_ok, test_results, tests_error = run_test_commands(brief_cfg)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_art_dir = prepare_run_artifact_dir(run_id)
    write_json(run_art_dir / "dry_validate.json", {
        "validators_ok": validators.ok,
        "validator_error_codes": validators.error_codes,
        "validator_messages": validators.messages,
        "tests_ok": tests_ok,
        "tests_error": tests_error,
        "tests": test_results,
    })

    if not validators.ok:
        return CODE_VALIDATION
    if not tests_ok:
        return CODE_TEST_FAIL
    return CODE_SUCCESS


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        if args.dry_validate:
            return run_dry_validate(args)
        return run_pipeline(args)
    except OrchestratorError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return CODE_PRECONDITION
    except Exception as exc:  # noqa: BLE001
        print(f"[{ERROR_PREFIX}UNHANDLED] {exc}", file=sys.stderr)
        return CODE_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
