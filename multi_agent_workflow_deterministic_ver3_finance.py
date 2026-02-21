#!/usr/bin/env python3
"""
Codex-only multi-agent workflow (no OpenAI application programming interface key required).

Determinism levers implemented in Python (no Codex software development kit):
1) Validators beyond file existence
2) JavaScript Object Notation plan generation constrained by a JavaScript Object Notation Schema
3) Hash manifests to snapshot inputs (Secure Hash Algorithm 256)
4) File system allowlists per step

Prerequisite: Codex command line interface authenticated via `codex login`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


MODEL = os.getenv("CODEX_MODEL", "gpt-5.1-codex")
SANDBOX = os.getenv("CODEX_SANDBOX", "workspace-write")
APPROVAL = 'approval_policy="never"'
WORKSPACE = Path.cwd()
CODEX_TIMEOUT_SECONDS = int(os.getenv("CODEX_TIMEOUT_SECONDS", "1800"))

# Canonical outputs
REQUIREMENTS_MD = WORKSPACE / "REQUIREMENTS.md"
TEST_MD = WORKSPACE / "TEST.md"
AGENT_TASKS_MD = WORKSPACE / "AGENT_TASKS.md"
PLAN_JSON = WORKSPACE / ".pipeline_plan.json"
MANIFEST_JSON = WORKSPACE / ".pipeline_manifest.json"
PLAN_SCHEMA_JSON = WORKSPACE / ".pipeline_plan_schema.json"

DESIGN_DIR = WORKSPACE / "design"
FRONTEND_DIR = WORKSPACE / "frontend"
BACKEND_DIR = WORKSPACE / "backend"
TESTS_DIR = WORKSPACE / "tests"

DESIGN_SPEC = DESIGN_DIR / "design_spec.md"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"
BACKEND_SERVER = BACKEND_DIR / "server.js"
BACKEND_PKG = BACKEND_DIR / "package.json"
TEST_PLAN = TESTS_DIR / "TEST_PLAN.md"
README_MD = WORKSPACE / "README.md"
RUNBOOK_MD = WORKSPACE / "RUNBOOK.md"

# --- Unified_ver1 data scanning (13F infotable issuer index) ---
UNIFIED_VER1_DIR = WORKSPACE / "Unified_ver1"
UNIFIED_DATA_DIR = UNIFIED_VER1_DIR / "data"
ISSUERS_INDEX_JSON = (WORKSPACE / "plan") / "issuers_index.json"


# ----------------------------
# File utilities: hashing, snapshots, diffs
# ----------------------------
def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_workspace_files(root: Path) -> Iterable[Path]:
    exclude_dirnames = {".git", "node_modules", ".venv", "__pycache__"}
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        parts = set(p.parts)
        if any(x in parts for x in exclude_dirnames):
            continue
        yield p


def snapshot_workspace(root: Path) -> Dict[str, Dict[str, Any]]:
    snap: Dict[str, Dict[str, Any]] = {}
    for p in iter_workspace_files(root):
        rel = str(p.relative_to(root))
        try:
            snap[rel] = {"sha256": sha256_file(p), "size": p.stat().st_size}
        except FileNotFoundError:
            continue
    return snap


def diff_snapshots(before: Dict[str, Dict[str, Any]], after: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    created = sorted(after_keys - before_keys)
    deleted = sorted(before_keys - after_keys)
    modified: List[str] = []
    for k in sorted(before_keys & after_keys):
        if before[k]["sha256"] != after[k]["sha256"]:
            modified.append(k)
    return {"created": created, "deleted": deleted, "modified": modified}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def require_exists(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Required file missing: {path}")


def require_nonempty_text(path: Path, *, min_chars: int = 20) -> None:
    require_exists(path)
    txt = read_text(path).strip()
    if len(txt) < min_chars:
        raise RuntimeError(f"File is too small or empty: {path} (length={len(txt)})")


def ensure_dirs() -> None:
    DESIGN_DIR.mkdir(parents=True, exist_ok=True)
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    BACKEND_DIR.mkdir(parents=True, exist_ok=True)
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "plan").mkdir(parents=True, exist_ok=True)


# ----------------------------
# Policy enforcement
# ----------------------------
def matches_any_glob(rel_path: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, g) for g in globs)


@dataclass(frozen=True)
class StepPolicy:
    name: str
    allowed_create_globs: Tuple[str, ...]
    allowed_modify_globs: Tuple[str, ...]
    forbidden_globs: Tuple[str, ...] = ()
    frozen_inputs: Tuple[str, ...] = ()
    required_outputs: Tuple[str, ...] = ()


def enforce_policy(
    *,
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
    policy: StepPolicy,
) -> None:
    d = diff_snapshots(before, after)
    created = d["created"]
    deleted = d["deleted"]
    modified = d["modified"]

    if deleted:
        raise RuntimeError(f"[{policy.name}] Unexpected deletions: {deleted[:20]}")

    violations: List[str] = []

    for rel in created:
        if matches_any_glob(rel, policy.forbidden_globs):
            violations.append(f"Created forbidden: {rel}")
        elif not matches_any_glob(rel, policy.allowed_create_globs):
            violations.append(f"Created outside allowlist: {rel}")

    for rel in modified:
        if matches_any_glob(rel, policy.forbidden_globs):
            violations.append(f"Modified forbidden: {rel}")
        elif not matches_any_glob(rel, policy.allowed_modify_globs):
            violations.append(f"Modified outside allowlist: {rel}")

    for rel in policy.frozen_inputs:
        if rel in modified:
            violations.append(f"Modified frozen input: {rel}")

    if violations:
        preview = "\n - ".join(violations[:30])
        raise RuntimeError(f"[{policy.name}] Policy violation(s):\n - {preview}")

    for rel in policy.required_outputs:
        if rel not in after:
            raise RuntimeError(f"[{policy.name}] Required output missing after step: {rel}")


# ----------------------------
# Codex runner (streaming JavaScript Object Notation events)
# ----------------------------
async def run_codex(
    prompt: str,
    *,
    cwd: Optional[Path] = None,
    extra_args: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    cmd = [
        "codex",
        "exec",
        "--experimental-json",
        "--model",
        MODEL,
        "--sandbox",
        SANDBOX,
        "--config",
        APPROVAL,
        "--skip-git-repo-check",
    ]
    if extra_args:
        cmd.extend(extra_args)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd or WORKSPACE),
        env=os.environ.copy(),
    )

    assert proc.stdin
    proc.stdin.write(prompt.encode("utf-8"))
    proc.stdin.close()

    assert proc.stdout
    final_text = ""
    usage: Dict[str, Any] | None = None

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        event = json.loads(line)
        if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            final_text = event["item"].get("text", "")
        elif event.get("type") == "turn.completed":
            usage = event.get("usage")

    stderr = await proc.stderr.read()
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=CODEX_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(
            f"codex timed out after {CODEX_TIMEOUT_SECONDS} seconds: {' '.join(shlex.quote(x) for x in cmd)}"
        )

    if returncode != 0:
        raise RuntimeError(f"codex failed ({returncode}): {stderr.decode('utf-8', errors='replace')}")

    if not final_text.strip():
        final_text = "(no agent_message text)"
    return final_text, usage or {}


# ----------------------------
# Structured planning (JavaScript Object Notation Schema)
# ----------------------------
def plan_schema() -> Dict[str, Any]:
    canonical_roles = [
        "Project Manager",
        "Designer",
        "Frontend Developer",
        "Backend Developer",
        "Tester",
        "Data Scanner",  # NEW
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_name": {"type": "string", "minLength": 1},
            "roles": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "enum": canonical_roles},
                        "required_outputs": {"type": "array", "items": {"type": "string", "minLength": 1}},
                        "success_checks": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    },
                    "required": ["name", "required_outputs", "success_checks"],
                },
            },
            "global_constraints": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
        "required": ["project_name", "roles", "global_constraints"],
    }


def normalize_role_name(name: str) -> str:
    n = name.strip().lower()

    if "project manager" in n or n in {"pm"}:
        return "Project Manager"
    if "designer" in n:
        return "Designer"
    if "frontend" in n:
        return "Frontend Developer"
    if "backend" in n:
        return "Backend Developer"
    if "tester" in n or "qa" in n or "quality" in n:
        return "Tester"
    if "data scanner" in n or ("data" in n and "scanner" in n) or ("issuer" in n and "scan" in n):
        return "Data Scanner"

    return name.strip()



def validate_plan_obj(plan: Dict[str, Any]) -> None:
    required_roles = {
        "Project Manager",
        "Designer",
        "Frontend Developer",
        "Backend Developer",
        "Tester",
        "Data Scanner",  # NEW
    }

    roles = plan.get("roles", [])
    if not isinstance(roles, list):
        raise RuntimeError("Plan validation failed: roles must be an array.")

    for r in roles:
        if isinstance(r, dict) and isinstance(r.get("name"), str):
            r["name"] = normalize_role_name(r["name"])

    present = {r.get("name") for r in roles if isinstance(r, dict)}
    missing = sorted(required_roles - present)
    if missing:
        raise RuntimeError(f"Plan validation failed: missing required roles: {missing}")

    for role in roles:
        for out in role.get("required_outputs", []):
            if out.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", out):
                raise RuntimeError(f"Plan validation failed: output paths must be relative: {out}")



def role_header(role: str) -> str:
    return (
        f"You are the {role} in a multi-agent workflow.\n"
        "You MUST follow the provided sources of truth.\n"
        "You MUST write the required files to disk in the current workspace when asked.\n"
        f"Use the workspace sandbox: {SANDBOX}.\n"
        "Do not ask for approval.\n"
        "At the end, output a short single-line summary like: DONE: <files>\n"
    )


def planner_prompt(task_list: str) -> str:
    return f"""\
{role_header("Project Manager")}

You must produce a JavaScript Object Notation plan that conforms EXACTLY to the output schema.
Output ONLY the JavaScript Object Notation object. Do NOT wrap it in markdown. Do NOT include commentary.
Do NOT write any files in this step.

Role naming requirement:
In roles[].name you MUST use EXACTLY one of:
- Project Manager
- Designer
- Frontend Developer
- Backend Developer
- Tester

Schema discipline:
- Do NOT add extra keys not present in the schema.
- Use relative paths only in required_outputs (for example, "backend/server.js", not "/backend/server.js").

Task list:
{task_list}
"""


# ----------------------------
# Prompts (Codex writes files)
# ----------------------------
def pm_prompt(task_list: str, plan_json: str, manifest: str) -> str:
    return f"""\
{role_header("Project Manager")}

Objective:
Convert the task list into FOUR files (exact names):
- REQUIREMENTS.md
- TEST.md
- AGENT_TASKS.md
- plan/overview.md (restates requirements and maps dependencies between roles)

Critical contract rule:
- TEST.md is the contract for the backend and tester.
- In TEST.md, you MUST explicitly list all backend endpoints using the exact pattern:
  - GET /path
  - POST /path
  - PUT /path
  - PATCH /path
  - DELETE /path
These endpoint lines must appear verbatim so they can be parsed deterministically.

Determinism requirements:
- Follow the JavaScript Object Notation plan exactly.
- Do NOT modify .pipeline_plan.json or .pipeline_manifest.json.
- Do NOT create any other files besides the four listed above.
- If the plan/ directory does not exist, create it.

REQUIREMENTS.md must include (at minimum):
- Goal and scope of the financial analysis application
- Data sources and assumptions
- Core user workflows (for example: browse issuers, view issuer details, view holdings summary)
- Frontend requirements (tables, filters, sorting, basic charts if feasible without frameworks)
- Backend requirements (endpoints, input validation, response shapes)
- Non-functional requirements (performance expectations, error handling, determinism)

TEST.md must include (at minimum):
- A list of endpoints in method + path format (see contract rule above)
- For each endpoint: success status code, example response shape, and at least one failure case
- Any acceptance criteria that are testable

AGENT_TASKS.md must include:
- A clear breakdown per role: Designer, Data Scanner, Frontend Developer, Backend Developer, Tester
- Explicit dependencies (for example: Frontend depends on plan/issuers_index.json and backend endpoints)

plan/overview.md must include:
- A short system overview
- A dependency map between roles and files (for example: Data Scanner -> plan/issuers_index.json -> Frontend panel)

Write these files now.

JavaScript Object Notation plan (read-only):
{plan_json}

Input manifest (read-only; do not modify):
{manifest}

Task list:
{task_list}
"""


def designer_prompt(requirements: str, agent_tasks: str, manifest: str) -> str:
    return f"""\
{role_header("Designer")}

Source of truth:
- REQUIREMENTS.md
- AGENT_TASKS.md

Determinism requirements:
- Do NOT modify REQUIREMENTS.md, TEST.md, AGENT_TASKS.md, .pipeline_plan.json, or .pipeline_manifest.json.
- Write only into /design.

Deliverables:
- design/design_spec.md (required)
- design/wireframe.md only if AGENT_TASKS.md requires it

Design objective:
Produce a one-page UI/UX spec for a financial analysis dashboard (no frameworks).
The dashboard must incorporate the issuer index panel derived from plan/issuers_index.json.

design/design_spec.md must include:
- Page layout description: header, main content area, and a right-side issuer panel
- Information architecture:
  - A main table view for issuer-period items and/or issuer lists
  - A detail view pattern (for example: clicking an issuer shows a detail card)
  - Filtering and sorting controls (for example: filter by cik, search issuer substring, filter by period)
- Accessibility and usability basics (keyboard navigation expectations, readable typography)
- Error states (missing issuer index, empty results)
- A minimal visual style (spacing, typography, simple colors) suitable for analytical software

Write the required file(s) now.

Input manifest (read-only; do not modify):
{manifest}

REQUIREMENTS.md:
{requirements}

AGENT_TASKS.md:
{agent_tasks}
"""




def data_scanner_prompt(manifest: str) -> str:
    return f"""\
{role_header("Data Scanner")}

Objective:
Scan the local 13F data directory and produce a compact issuer index.

Data root:
- Unified_ver1/data/

Expected on-disk structure (generalized):
- Unified_ver1/data/<CIK10>/<PERIOD_OR_ACCESSION>/infotable.xml
Where:
- <CIK10> is a folder name like "0001029160" (but could be any CIK-like folder)
- <PERIOD_OR_ACCESSION> is a folder name like "000090266425003648" (but could be any period/accession folder)
- There may be MANY period folders under a single CIK folder.
- Other entities may have more or fewer periods.

Your task:
1) Recursively enumerate all files named exactly: "infotable.xml" under Unified_ver1/data
2) For each infotable.xml, parse it and extract ALL <nameOfIssuer> tag values (deduplicate per file).
   - This must be namespace-agnostic (the XML may include namespaces).
   - If a file is malformed, skip it and record an error entry instead of failing the whole run.
3) Write EXACTLY ONE output file:
   - plan/issuers_index.json

Output format (JSON, no markdown, write to disk):
{{
  "data_root": "Unified_ver1/data",
  "generated_at_utc": "<ISO8601 UTC timestamp>",
  "items": [
    {{
      "cik": "<CIK folder name>",
      "period": "<PERIOD folder name>",
      "infotable_relpath": "Unified_ver1/data/<CIK>/<PERIOD>/infotable.xml",
      "issuers": ["Issuer A", "Issuer B", ...],
      "issuer_count": <int>
    }},
    ...
  ],
  "errors": [
    {{
      "infotable_relpath": "...",
      "error": "..."
    }}
  ]
}}

Sorting rules (deterministic):
- items sorted by cik ASC, then period ASC
- issuers within each item sorted ASC
- errors sorted by infotable_relpath ASC

Determinism requirements:
- Do NOT modify any other file.
- Do NOT modify REQUIREMENTS.md, TEST.md, AGENT_TASKS.md, design/*, frontend/*, backend/*, tests/*,
  .pipeline_plan.json, or .pipeline_manifest.json.
- Create only plan/issuers_index.json (and do not create any other files).

If Unified_ver1/data does not exist or contains no infotable.xml:
- Still write plan/issuers_index.json with empty items and an errors entry describing the situation.

Write the required file now.

Input manifest (read-only; do not modify):
{manifest}
"""


def frontend_prompt(requirements: str, agent_tasks: str, design_spec: str, manifest: str, issuers_index_json: str) -> str:
    return f"""\
{role_header("Frontend Developer")}

Source of truth:
- REQUIREMENTS.md
- AGENT_TASKS.md
- design/design_spec.md
- plan/issuers_index.json (generated by Data Scanner)

Determinism requirements:
- Do NOT modify REQUIREMENTS.md, TEST.md, AGENT_TASKS.md, design/design_spec.md, or plan/issuers_index.json.
- Do NOT modify .pipeline_plan.json or .pipeline_manifest.json.
- Write only into /frontend.

Deliverables:
- frontend/index.html (required)
- frontend/styles.css and frontend/main.js only if AGENT_TASKS.md requires separate files

Rules:
- No frameworks, no external dependencies.
- The page must be fully usable as static HTML (you may embed JSON data directly into the page).

Application to build:
A single-page financial analysis dashboard that helps a user explore SEC 13F issuer data extracted locally.

Required UI features:
1) Main dashboard area (left / center):
   - Display a table of issuer-index items derived from plan/issuers_index.json.
   - Each row should include at least: cik, period, issuer_count.
   - Include a simple interaction to inspect details:
     - When a user selects a row, show the sorted list of issuers for that cik+period in a detail panel or modal.

2) Controls:
   - Search box to filter issuer names (case-insensitive substring match).
   - Filter by cik (dropdown or text filter).
   - Filter by period (dropdown or text filter).
   - Sorting at least by cik and period (client-side).

3) Right-side panel (issuer index panel):
   - Display issuer information derived from plan/issuers_index.json.
   - For each item, show the period folder name and a readable list of issuer names.
   - Keep it sufficiently general: it must render any number of cik folders and any number of periods.
   - Use collapsible/scrollable UI for large lists.

4) Data and error handling:
   - If plan/issuers_index.json contains errors or has no items, display a clear message.
   - Do not crash if arrays are empty.

Optional (only if easy without frameworks):
- Basic summary metrics: total items, total unique issuers across all items, total errors.

You are given the issuers_index.json content below; you may embed it directly into the page (for example, a <script type="application/json"> block)
so the page is fully static and does not rely on fetching local files.

Write the required file(s) now.

Input manifest (read-only; do not modify):
{manifest}

REQUIREMENTS.md:
{requirements}

AGENT_TASKS.md:
{agent_tasks}

design/design_spec.md:
{design_spec}

plan/issuers_index.json content (read-only):
{issuers_index_json}
"""



def backend_prompt(requirements: str, agent_tasks: str, manifest: str) -> str:
    return f"""\
{role_header("Backend Developer")}

Source of truth:
- REQUIREMENTS.md
- AGENT_TASKS.md
- TEST.md (endpoint contract; implement every endpoint listed there)

Determinism requirements:
- Do NOT modify REQUIREMENTS.md, TEST.md, AGENT_TASKS.md.
- Do NOT modify .pipeline_plan.json or .pipeline_manifest.json.
- Write only into /backend.

Deliverables:
- backend/package.json (required; include start script)
- backend/server.js (required)

Rules:
- Minimal Node.js server.
- In-memory storage only (no external database).
- Implement GET /health.
- Implement every endpoint listed in TEST.md (method + path).
- Basic validation and Cross-Origin Resource Sharing for local development.

Financial analysis backend guidance (typical endpoints; follow TEST.md as the contract):
- GET /api/index
  - Returns the issuer index data (you may serve the embedded plan/issuers_index.json content if you choose to copy it in server.js).
- GET /api/items
  - Optional query parameters: cik, period, q (issuer substring).
  - Returns filtered items.
- GET /api/item
  - Query parameters: cik and period (required).
  - Returns the issuers list for that item.
- POST /api/analyze
  - Accepts a JSON body with analysis parameters (for example: group_by, top_n, filter criteria).
  - Returns computed summary statistics.

Important:
- Do not invent endpoints that are not listed in TEST.md.
- If TEST.md includes POST endpoints, include JSON body parsing and validation.

Write the required file(s) now.

Input manifest (read-only; do not modify):
{manifest}

REQUIREMENTS.md:
{requirements}

AGENT_TASKS.md:
{agent_tasks}
"""






def tester_prompt(requirements: str, test_md: str, manifest: str) -> str:
    return f"""\
{role_header("Tester")}

Source of truth (read-only):
- REQUIREMENTS.md
- TEST.md

Determinism requirements:
- Do NOT modify REQUIREMENTS.md, TEST.md, AGENT_TASKS.md.
- Do NOT modify .pipeline_plan.json or .pipeline_manifest.json.
- Write only into /tests.
- If you create tests/test.sh, it MUST start with a shebang line (#!/usr/bin/env bash).

Deliverables:
- tests/TEST_PLAN.md (required)
- tests/test.sh (optional, ONLY if TEST.md explicitly requests an automated script)

Hard requirement for tests/TEST_PLAN.md content:
- It MUST cover EVERY endpoint explicitly listed in TEST.md using patterns like:
  - GET /path
  - POST /path
  - PUT /path
  - PATCH /path
  - DELETE /path

Content requirements for tests/TEST_PLAN.md:
- Include a section titled "Manual checks".
- Under that section, include bullet points that explicitly reference:
  - Each endpoint from TEST.md with expected status code and response shape
  - Validation behavior for at least one valid and one invalid payload for any endpoint that accepts a body
- If the frontend depends on backend endpoints, include a check that the frontend can call the relevant endpoint(s).

If TEST.md requests an automated script:
- Create tests/test.sh that:
  - Sends a request to GET /health and fails if status is not 200
  - Sends requests to the other endpoints listed in TEST.md (as applicable) and fails if status is not 200/201 where appropriate
  - Uses curl only (no extra dependencies)
  - Is minimal and easy to run

Write the required file(s) now.

Input manifest (read-only; do not modify):
{manifest}

REQUIREMENTS.md:
{requirements}

TEST.md:
{test_md}
"""



def tester_fix_prompt(error: str, manifest: str) -> str:
    return f"""\
{role_header("Tester Fixer")}

A validation failed with this error:
{error}

Fix instructions (non-negotiable):
- Modify ONLY: tests/TEST_PLAN.md
- Do NOT create or modify any other file.
- Ensure tests/TEST_PLAN.md explicitly covers EVERY endpoint listed in TEST.md, including method + path, for example:
  - GET /health
  - GET /api/holdings
  - POST /api/analyze

Write the fix now.

Input manifest (read-only; do not modify):
{manifest}
"""




def docs_prompt(requirements: str, manifest: str) -> str:
    return f"""\
{role_header("Docs Writer")}

Determinism requirements:
- Do NOT modify REQUIREMENTS.md, TEST.md, AGENT_TASKS.md.
- Do NOT modify .pipeline_plan.json or .pipeline_manifest.json.

Deliverable:
- README.md (required)

README.md must describe the financial analysis application, including:
- What the application does (issuer index exploration and analysis)
- How to run the pipeline output (frontend and backend)
- How the issuer index is produced (plan/issuers_index.json from Unified_ver1/data)
- What backend endpoints exist (refer to TEST.md)
- How to run tests (tests/TEST_PLAN.md and optional tests/test.sh)
- Troubleshooting notes for missing Unified_ver1/data or empty issuer index

Write README.md now.

Input manifest (read-only; do not modify):
{manifest}

REQUIREMENTS.md:
{requirements}
"""




def runbook_prompt(manifest: str) -> str:
    return f"""\
{role_header("Release Engineer")}

Determinism requirements:
- Do NOT modify .pipeline_plan.json or .pipeline_manifest.json.

Deliverable:
- RUNBOOK.md (required)

Write RUNBOOK.md now.

Input manifest (read-only; do not modify):
{manifest}
"""

def extract_endpoints_from_test_md(test_md: str) -> List[Tuple[str, str]]:
    """
    Extract endpoint contracts from TEST.md.

    Expected patterns anywhere in the text, for example:
      - GET /health
      - POST /scores
      - GET /api/holdings

    Returns a deterministic list of (method_lower, path_lower), sorted by method then path,
    with duplicates removed.
    """
    if not test_md:
        return []

    matches = re.findall(r"(?im)\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^ \t\r\n]+)", test_md)
    norm = {(m.strip().lower(), p.strip().lower()) for (m, p) in matches if m and p}

    return sorted(norm, key=lambda x: (x[0], x[1]))


# ----------------------------
# Validators (structured success conditions)
# ----------------------------
def validate_pm_outputs() -> None:
    # Existing required outputs
    for p in [REQUIREMENTS_MD, TEST_MD, AGENT_TASKS_MD]:
        require_nonempty_text(p, min_chars=120)

    # New required output: plan/overview.md
    overview_path = WORKSPACE / "plan" / "overview.md"
    require_nonempty_text(overview_path, min_chars=120)

    overview_txt = read_text(overview_path).lower()
    # Minimal deterministic checks that it is actually an overview/dependency map
    for required_term in ["project manager", "designer", "frontend", "backend", "tester"]:
        if required_term not in overview_txt:
            raise RuntimeError(f"plan/overview.md validation failed: missing role reference '{required_term}'.")



def validate_design_outputs() -> None:
    require_nonempty_text(DESIGN_SPEC, min_chars=120)
    if not re.search(r"layout|screen|ui", read_text(DESIGN_SPEC), flags=re.IGNORECASE):
        raise RuntimeError("design/design_spec.md validation failed: missing basic user interface description.")


def validate_frontend_outputs() -> None:
    require_nonempty_text(FRONTEND_INDEX, min_chars=200)
    html = read_text(FRONTEND_INDEX).lower()

    # Keep a lightweight deterministic requirement that the page has some script logic.
    if "<script" not in html:
        raise RuntimeError("frontend/index.html validation failed: expected a script tag.")

    # Issuer panel evidence (deterministic, lightweight) stays required.
    if "issuer" not in html and "nameofissuer" not in html:
        raise RuntimeError("frontend/index.html validation failed: expected issuer panel content (issuer/nameOfIssuer).")
    if "period" not in html and "0000" not in html:
        raise RuntimeError("frontend/index.html validation failed: expected period reference for issuer panel.")




def validate_backend_outputs() -> None:
    require_nonempty_text(BACKEND_SERVER, min_chars=200)
    require_nonempty_text(BACKEND_PKG, min_chars=40)

    pkg = json.loads(read_text(BACKEND_PKG))
    scripts = pkg.get("scripts", {})
    if not isinstance(scripts, dict) or "start" not in scripts:
        raise RuntimeError("backend/package.json validation failed: missing scripts.start.")

    js = read_text(BACKEND_SERVER).lower()

    # Always require a health endpoint for operational sanity.
    if "/health" not in js:
        raise RuntimeError("backend/server.js validation failed: missing '/health' route.")

    # Contract-driven validation: require all endpoints specified in TEST.md to be present in server.js.
    endpoints = extract_endpoints_from_test_md(read_text(TEST_MD))

    # If TEST.md declares endpoints, enforce them. This is what makes the pipeline general.
    for method, path in endpoints:
        # /health is already enforced above; still allow it to be listed.
        if path not in js:
            raise RuntimeError(f"backend/server.js validation failed: missing route path '{path}' from TEST.md.")

    # If TEST.md includes any POST endpoints, ensure there is evidence of POST handling in the code.
    has_post_contract = any(m == "post" for (m, _p) in endpoints)
    if has_post_contract and "post" not in js:
        raise RuntimeError("backend/server.js validation failed: TEST.md requires POST, but no POST route signal found.")



def validate_tests_outputs() -> None:
    require_nonempty_text(TEST_PLAN, min_chars=80)
    plan_txt = read_text(TEST_PLAN).lower()

    # Always require evidence of a health check.
    health_ok = ("/health" in plan_txt) or ("healthcheck" in plan_txt) or ("health check" in plan_txt) or ("liveness" in plan_txt)
    if not health_ok:
        raise RuntimeError(
            "tests/TEST_PLAN.md validation failed: expected /health (or a clear healthcheck synonym)."
        )

    # Contract-driven validation: ensure TEST_PLAN covers every endpoint listed in TEST.md.
    endpoints = extract_endpoints_from_test_md(read_text(TEST_MD))

    # If TEST.md lists endpoints, require that TEST_PLAN mentions each path.
    # This keeps validation simple and deterministic, while allowing financial routes.
    for method, path in endpoints:
        # Require either "get /path" (method + path) or at least "/path" to appear.
        method_path = f"{method} {path}"
        if method_path not in plan_txt and path not in plan_txt:
            raise RuntimeError(
                f"tests/TEST_PLAN.md validation failed: expected coverage for '{method.upper()} {path}' from TEST.md."
            )

    sh_path = TESTS_DIR / "test.sh"
    if sh_path.exists():
        require_nonempty_text(sh_path, min_chars=10)
        if not read_text(sh_path).lstrip().startswith("#!"):
            raise RuntimeError("tests/test.sh validation failed: expected shebang line.")



def validate_docs_outputs() -> None:
    require_nonempty_text(README_MD, min_chars=120)
    txt = read_text(README_MD).lower()
    for keyword in ["backend", "frontend", "test"]:
        if keyword not in txt:
            raise RuntimeError(f"README.md validation failed: expected '{keyword}'.")


def validate_runbook_outputs() -> None:
    require_nonempty_text(RUNBOOK_MD, min_chars=80)
    txt = read_text(RUNBOOK_MD).lower()
    if "troubleshooting" not in txt and "issue" not in txt:
        raise RuntimeError("RUNBOOK.md validation failed: expected troubleshooting section.")

def validate_issuer_index_outputs() -> None:
    require_nonempty_text(ISSUERS_INDEX_JSON, min_chars=40)
    try:
        obj = json.loads(read_text(ISSUERS_INDEX_JSON))
    except Exception as e:
        raise RuntimeError(f"plan/issuers_index.json validation failed: invalid JSON: {e}")

    if not isinstance(obj, dict):
        raise RuntimeError("plan/issuers_index.json validation failed: root must be an object.")
    if "items" not in obj or "errors" not in obj:
        raise RuntimeError("plan/issuers_index.json validation failed: expected keys 'items' and 'errors'.")
    if not isinstance(obj["items"], list) or not isinstance(obj["errors"], list):
        raise RuntimeError("plan/issuers_index.json validation failed: 'items' and 'errors' must be arrays.")

    # Light structural checks for items
    for it in obj["items"][:50]:
        if not isinstance(it, dict):
            raise RuntimeError("plan/issuers_index.json validation failed: each item must be an object.")
        for k in ["cik", "period", "infotable_relpath", "issuers", "issuer_count"]:
            if k not in it:
                raise RuntimeError(f"plan/issuers_index.json validation failed: missing '{k}' in item.")
        if not isinstance(it["issuers"], list):
            raise RuntimeError("plan/issuers_index.json validation failed: issuers must be an array.")



# ----------------------------
# Manifest (snapshotting inputs)
# ----------------------------
def write_manifest(note: str) -> None:
    snap = snapshot_workspace(WORKSPACE)
    manifest = {
        "note": note,
        "model": MODEL,
        "sandbox": SANDBOX,
        "snapshot_sha256": sha256_bytes(json.dumps(snap, sort_keys=True).encode("utf-8")),
        "files": snap,
    }
    write_text(MANIFEST_JSON, json.dumps(manifest, indent=2))


# ----------------------------
# Steps
# ----------------------------
async def step_plan(task_list: str) -> Dict[str, Any]:
    write_text(PLAN_SCHEMA_JSON, json.dumps(plan_schema(), indent=2))

    before = snapshot_workspace(WORKSPACE)
    plan_text, usage = await run_codex(
        planner_prompt(task_list),
        extra_args=["--output-schema", str(PLAN_SCHEMA_JSON)],
    )
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Planner",
            allowed_create_globs=(".pipeline_plan_schema.json",),
            allowed_modify_globs=(".pipeline_plan_schema.json",),
            required_outputs=(".pipeline_plan_schema.json",),
        ),
    )

    plan_obj = json.loads(plan_text)
    validate_plan_obj(plan_obj)
    write_text(PLAN_JSON, json.dumps(plan_obj, indent=2))
    print(f"[Planner] Plan ready (output tokens={usage.get('output_tokens','?')})")
    return plan_obj


async def step_pm(task_list: str) -> None:
    plan_json = read_text(PLAN_JSON)
    manifest = read_text(MANIFEST_JSON)

    before = snapshot_workspace(WORKSPACE)
    _, usage = await run_codex(pm_prompt(task_list, plan_json, manifest))
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Project Manager",
            allowed_create_globs=("REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md", "plan/overview.md"),
            allowed_modify_globs=("REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md", "plan/overview.md"),
            forbidden_globs=(".pipeline_plan.json", ".pipeline_manifest.json"),
            frozen_inputs=(".pipeline_plan.json", ".pipeline_manifest.json"),
            required_outputs=("REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md", "plan/overview.md"),
        ),
    )

    validate_pm_outputs()
    print(f"[Project Manager] Validated (output tokens={usage.get('output_tokens','?')})")


async def step_designer() -> None:
    requirements = read_text(REQUIREMENTS_MD)
    agent_tasks = read_text(AGENT_TASKS_MD)
    manifest = read_text(MANIFEST_JSON)

    before = snapshot_workspace(WORKSPACE)
    _, usage = await run_codex(designer_prompt(requirements, agent_tasks, manifest))
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Designer",
            allowed_create_globs=("design/*",),
            allowed_modify_globs=("design/*",),
            forbidden_globs=(".pipeline_plan.json", ".pipeline_manifest.json", "REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md"),
            frozen_inputs=("REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md", ".pipeline_plan.json", ".pipeline_manifest.json"),
            required_outputs=("design/design_spec.md",),
        ),
    )

    validate_design_outputs()
    print(f"[Designer] Validated (output tokens={usage.get('output_tokens','?')})")


async def step_data_scanner() -> None:
    manifest = read_text(MANIFEST_JSON)

    before = snapshot_workspace(WORKSPACE)
    _, usage = await run_codex(data_scanner_prompt(manifest))
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Data Scanner",
            allowed_create_globs=("plan/issuers_index.json",),
            allowed_modify_globs=("plan/issuers_index.json",),
            forbidden_globs=(
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
            ),
            frozen_inputs=(
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
            ),
            required_outputs=("plan/issuers_index.json",),
        ),
    )

    validate_issuer_index_outputs()
    print(f"[Data Scanner] Validated (output tokens={usage.get('output_tokens','?')})")



async def step_frontend_backend_parallel() -> None:
    requirements = read_text(REQUIREMENTS_MD)
    agent_tasks = read_text(AGENT_TASKS_MD)
    design_spec = read_text(DESIGN_SPEC)
    manifest = read_text(MANIFEST_JSON)

    issuers_index_json = ""
    if ISSUERS_INDEX_JSON.exists():
        issuers_index_json = read_text(ISSUERS_INDEX_JSON)
    else:
        # Still pass something deterministic so the frontend can render an empty panel.
        issuers_index_json = json.dumps(
            {
                "data_root": "Unified_ver1/data",
                "generated_at_utc": "unknown",
                "items": [],
                "errors": [{"infotable_relpath": "", "error": "plan/issuers_index.json missing"}],
            },
            indent=2,
        )

    before = snapshot_workspace(WORKSPACE)

    async def do_frontend():
        return await run_codex(frontend_prompt(requirements, agent_tasks, design_spec, manifest, issuers_index_json))

    async def do_backend():
        return await run_codex(backend_prompt(requirements, agent_tasks, manifest))

    (_, fe_usage), (_, be_usage) = await asyncio.gather(do_frontend(), do_backend())
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Frontend and Backend",
            allowed_create_globs=("frontend/*", "backend/*"),
            allowed_modify_globs=("frontend/*", "backend/*"),
            forbidden_globs=(
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
                "design/design_spec.md",
                "plan/issuers_index.json",
            ),
            frozen_inputs=(
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
                "design/design_spec.md",
                "plan/issuers_index.json",
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
            ),
            required_outputs=("frontend/index.html", "backend/server.js", "backend/package.json"),
        ),
    )

    validate_frontend_outputs()
    validate_backend_outputs()

    print(
        f"[Frontend] Validated (output tokens={fe_usage.get('output_tokens','?')}) | "
        f"[Backend] Validated (output tokens={be_usage.get('output_tokens','?')})"
    )



async def step_tester() -> None:
    requirements = read_text(REQUIREMENTS_MD)
    test_md = read_text(TEST_MD)
    manifest = read_text(MANIFEST_JSON)

    # ---- First attempt ----
    before = snapshot_workspace(WORKSPACE)
    _, usage = await run_codex(tester_prompt(requirements, test_md, manifest))
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Tester",
            allowed_create_globs=("tests/*",),
            allowed_modify_globs=("tests/*",),
            forbidden_globs=(
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
            ),
            frozen_inputs=(
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
            ),
            required_outputs=("tests/TEST_PLAN.md",),
        ),
    )

    try:
        validate_tests_outputs()
        print(f"[Tester] Validated (output tokens={usage.get('output_tokens','?')})")
        return
    except RuntimeError as e:
        validation_error = str(e)

    # ---- Repair attempt (ONLY edits tests/TEST_PLAN.md) ----
    manifest = read_text(MANIFEST_JSON)  # refresh in case manifest changed elsewhere

    before_fix = snapshot_workspace(WORKSPACE)
    _, fix_usage = await run_codex(tester_fix_prompt(validation_error, manifest))
    after_fix = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before_fix,
        after=after_fix,
        policy=StepPolicy(
            name="Tester Fixer",
            allowed_create_globs=(),  # do not create anything new in fix
            allowed_modify_globs=("tests/TEST_PLAN.md",),
            forbidden_globs=(
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
                "tests/test.sh",  # prevent fixer from introducing new script
            ),
            frozen_inputs=(
                "REQUIREMENTS.md",
                "TEST.md",
                "AGENT_TASKS.md",
                ".pipeline_plan.json",
                ".pipeline_manifest.json",
            ),
            required_outputs=("tests/TEST_PLAN.md",),
        ),
    )

    # Re-validate after fix
    validate_tests_outputs()
    print(
        f"[Tester] Fixed and validated (first output tokens={usage.get('output_tokens','?')}, "
        f"fix output tokens={fix_usage.get('output_tokens','?')})"
    )


async def step_docs_and_runbook() -> None:
    requirements = read_text(REQUIREMENTS_MD)
    manifest = read_text(MANIFEST_JSON)

    before = snapshot_workspace(WORKSPACE)
    _, docs_usage = await run_codex(docs_prompt(requirements, manifest))
    _, rb_usage = await run_codex(runbook_prompt(manifest))
    after = snapshot_workspace(WORKSPACE)

    enforce_policy(
        before=before,
        after=after,
        policy=StepPolicy(
            name="Documentation and Runbook",
            allowed_create_globs=("README.md", "RUNBOOK.md"),
            allowed_modify_globs=("README.md", "RUNBOOK.md"),
            forbidden_globs=(".pipeline_plan.json", ".pipeline_manifest.json", "REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md"),
            frozen_inputs=("REQUIREMENTS.md", "TEST.md", "AGENT_TASKS.md", ".pipeline_plan.json", ".pipeline_manifest.json"),
            required_outputs=("README.md", "RUNBOOK.md"),
        ),
    )

    validate_docs_outputs()
    validate_runbook_outputs()

    print(
        f"[Documentation] Validated (output tokens={docs_usage.get('output_tokens','?')}) | "
        f"[Runbook] Validated (output tokens={rb_usage.get('output_tokens','?')})"
    )


# ----------------------------
# Main
# ----------------------------

async def main() -> None:
    ensure_dirs()

    task_list = """
    Goal: Build a small financial analysis application (single-page dashboard) powered by locally stored SEC 13F infotable.xml data.

    High-level requirements:
    - A multiple-page frontend dashboard for exploring issuer data extracted from 13F infotable.xml and period_comparison.csv file.
    - The dashboard must support:
      - Viewing all issuer-index items (cik + period + issuer_count)
      - Filtering by cik
      - Filtering by period
      - Searching issuer names (case-insensitive substring)
      - Selecting an item to view the full issuer list for that cik+period
      - Showing basic summary metrics (total items, total unique issuers, number of errors) if feasible
      - It must show the outcomes of the comparison between the latest period and the previous period which can be found inside the period_comparison.csv file

    Backend requirements:
    - Minimal Node.js server with Cross-Origin Resource Sharing enabled for local development.
    - Must include GET /health.
    - Must include financial analysis endpoints and list them explicitly in TEST.md using method + path lines.
      Example endpoints you may choose to require in TEST.md:
      - GET /api/index
      - GET /api/items
      - GET /api/item
      - POST /api/analyze

    NEW data integration requirement:
    - There is a local folder Unified_ver1/data/ containing SEC 13F infotable.xml files organized as:
      Unified_ver1/data/<CIK10>/<PERIOD_OR_ACCESSION>/infotable.xml
    - There is also a local file in the Unified_ver1/period_comparison.csv which already contains the comparison of the latest values.
    - A dedicated Data Scanner agent must scan all available infotable.xml and extract all values values, even the ones occuring sparsely.
    - The Data Scanner must write plan/issuers_index.json.
    - The frontend must display issuer values.

    Roles:
    - Project Manager: write requirements, tests (including endpoint contract lines), and agent task breakdown.
    - Designer: create a multiple-page UI/UX specification for the financial dashboard.
    - Data Scanner: scan Unified_ver1/data and period_comparison.csv and write plan/issuers_index.json (do not change this behavior).
    - Frontend Developer: implement the financial dashboard in static HTML.
    - Backend Developer: implement the backend endpoints listed in TEST.md.
    - Tester: write a test plan (and optional script) that covers every endpoint listed in TEST.md.

    Constraints:
    - No external database—memory storage is fine.
    - Keep everything readable for beginners; no frameworks required.
    - All outputs should be small files saved in clearly named folders.
    """.strip()

    write_manifest("Initial manifest before pipeline run")
    await step_plan(task_list)
    write_manifest("Manifest after planning")

    await step_pm(task_list)
    write_manifest("Manifest after project manager")

    await step_designer()
    write_manifest("Manifest after designer")

    # NEW: data scanner step
    await step_data_scanner()
    write_manifest("Manifest after data scanner")

    await step_frontend_backend_parallel()
    write_manifest("Manifest after frontend and backend")

    await step_tester()
    write_manifest("Manifest after tester")

    await step_docs_and_runbook()
    write_manifest("Manifest after documentation and runbook")

    print("\nProject generated and validated:")
    for p in [
        PLAN_SCHEMA_JSON,
        PLAN_JSON,
        MANIFEST_JSON,
        REQUIREMENTS_MD,
        TEST_MD,
        AGENT_TASKS_MD,
        (WORKSPACE / "plan" / "overview.md"),
        ISSUERS_INDEX_JSON,
        DESIGN_SPEC,
        FRONTEND_INDEX,
        BACKEND_PKG,
        BACKEND_SERVER,
        TEST_PLAN,
        README_MD,
        RUNBOOK_MD,
    ]:
        print(f" - {p}")



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

