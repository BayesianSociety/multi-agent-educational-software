# multi-agent-educational-software
cat Prompt_4_separate_pipeline_engine_from_project_pack.txt Prompt_4_project_brief.txt \
  | codex exec --full-auto --json -o ./last_message.txt 

# explain core differences between multi_agent_workflow_deterministic_ver3_finance.py and orchestrator.py
- multi_agent_workflow_deterministic_ver3_finance.py and orchestrator.py are both deterministic Codex pipelines,
  but they optimize for different goals.

  Core philosophy

  - multi_agent_workflow_deterministic_ver3_finance.py: product-first, domain-specific generator for one finance
    app flow (13F issuer indexing is hardwired). See multi_agent_workflow_deterministic_ver3_finance.py:1318 and
    Data Scanner logic at multi_agent_workflow_deterministic_ver3_finance.py:502.
  - orchestrator.py: framework-first, reusable orchestrator for many projects with policy-driven iteration and
    optional self-improvement loops (--design-b). See orchestrator.py:2, orchestrator.py:1721,
    orchestrator.py:1847.

  Approach differences

  - Pipeline shape:
      - Finance file: fixed sequence with one async parallel phase (frontend + backend) using asyncio.gather
        (multi_agent_workflow_deterministic_ver3_finance.py:1129).
      - Orchestrator: generic step engine (StepSpec) with retries, fixer, scoring, and optional tuner
        (orchestrator.py:776, orchestrator.py:1598, orchestrator.py:1707).
  - Prompting:
      - Finance file: prompts are embedded and task list is hardcoded in script
        (multi_agent_workflow_deterministic_ver3_finance.py:1318).
      - Orchestrator: prompt variants loaded from prompt libraries/templates and selected via strategies like UCB1
        (orchestrator.py:866, orchestrator.py:980).
  - Determinism guardrails:
      - Finance file: per-step file-policy checks over workspace snapshots (StepPolicy, enforce_policy)
        (multi_agent_workflow_deterministic_ver3_finance.py:149,
        multi_agent_workflow_deterministic_ver3_finance.py:158).
      - Orchestrator: git-aware invariants + allowlists + caps + deterministic rollback (orchestrator.py:353,
        orchestrator.py:1228).
  - Validation/testing:
      - Finance file: validates generated artifacts and endpoint coverage textually; no real test command
        execution.
      - Orchestrator: runs real test commands parsed from TEST.md/brief profile (orchestrator.py:456) and merges
        many validators (orchestrator.py:1486).

  Application differences

  - Use multi_agent_workflow_deterministic_ver3_finance.py when you want a fast, constrained, finance-specific
    build pipeline with explicit role prompts and outputs.
  - Use orchestrator.py when you want a long-lived orchestration system: repeatable runs, telemetry, prompt/skill
    evolution, regression gating, and policy persistence (.orchestrator/policy.json, run logs).
 multi_agent_workflow_deterministic_ver3_finance.py and orchestrator.py are both deterministic Codex pipelines,
  but they optimize for different goals.

  Core philosophy

  - multi_agent_workflow_deterministic_ver3_finance.py: product-first, domain-specific generator for one finance
    app flow (13F issuer indexing is hardwired). See multi_agent_workflow_deterministic_ver3_finance.py:1318 and
    Data Scanner logic at multi_agent_workflow_deterministic_ver3_finance.py:502.
  - orchestrator.py: framework-first, reusable orchestrator for many projects with policy-driven iteration and
    optional self-improvement loops (--design-b). See orchestrator.py:2, orchestrator.py:1721,
    orchestrator.py:1847.

  Approach differences

  - Pipeline shape:
      - Finance file: fixed sequence with one async parallel phase (frontend + backend) using asyncio.gather
        (multi_agent_workflow_deterministic_ver3_finance.py:1129).
      - Orchestrator: generic step engine (StepSpec) with retries, fixer, scoring, and optional tuner
        (orchestrator.py:776, orchestrator.py:1598, orchestrator.py:1707).
  - Prompting:
      - Finance file: prompts are embedded and task list is hardcoded in script
        (multi_agent_workflow_deterministic_ver3_finance.py:1318).
      - Orchestrator: prompt variants loaded from prompt libraries/templates and selected via strategies like UCB1
        (orchestrator.py:866, orchestrator.py:980).
  - Determinism guardrails:
      - Finance file: per-step file-policy checks over workspace snapshots (StepPolicy, enforce_policy)
        (multi_agent_workflow_deterministic_ver3_finance.py:149,
        multi_agent_workflow_deterministic_ver3_finance.py:158).
      - Orchestrator: git-aware invariants + allowlists + caps + deterministic rollback (orchestrator.py:353,
        orchestrator.py:1228).
  - Validation/testing:
      - Finance file: validates generated artifacts and endpoint coverage textually; no real test command
        execution.
      - Orchestrator: runs real test commands parsed from TEST.md/brief profile (orchestrator.py:456) and merges
        many validators (orchestrator.py:1486).

  Application differences

  - Use multi_agent_workflow_deterministic_ver3_finance.py when you want a fast, constrained, finance-specific
    build pipeline with explicit role prompts and outputs.
  - Use orchestrator.py when you want a long-lived orchestration system: repeatable runs, telemetry, prompt/skill
    evolution, regression gating, and policy persistence (.orchestrator/policy.json, run logs).  

# additional questions to codex
- Explain this statement: Orchestrator: prompt variants loaded from prompt libraries/templates and selected via strategies like UCB1, and indicate where are those prompt variants. Do not change anything in the files or folders.

- Also, I want to see where are the individual prompts for frontend, backend, designer, and the other agents.

- Who and how to create this: .orchestrator/prompt_templates/<agent>/*.txt 

- The orchestrator.py is the main file. Think about which parts from multi_agent_workflow_deterministic_ver3_finance.py, would make orchestrator.py an even better application creator.

# currently, the generated app starts as two processes:
## starting the application
  1. Backend API

  npm --prefix backend run start

  - Runs backend/src/server.ts on PORT or default 4000 (backend/src/server.ts:52, backend/package.json).

  2. Frontend

  npm --prefix frontend run dev

  - Starts Next.js dev server (default http://localhost:3000) (frontend/package.json).

  If you meant running the generator itself (orchestrator.py) to create/update the app:

  python3 orchestrator.py

  (Optionally python3 orchestrator.py --design-b.)


