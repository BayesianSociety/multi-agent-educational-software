# Overview
- Build a web-based coding game for ages 7-12 where players guide a dog to a bone using simple blocks.
- Teach sequencing, procedural thinking, and debugging through deterministic step-by-step execution.
- The app runs in modern browsers and follows privacy and accessibility constraints from the Project Brief.

# Scope
- The editor provides `Move(1)` and `Jump` blocks for MVP.
- The workspace is a single vertical sequence and executes deterministically from first to last block.
- Running the program highlights the active block and animates movement.
- Reset returns the level to the initial state.
- The MVP includes exactly ten short levels.
- Level 1 teaches `Move` and uses no gap.
- Level 2 introduces a one-tile gap that requires `Jump`.
- Each level includes one sentence of narrative text.
- Unlocked progress is stored locally in `localStorage` and restored on refresh.
- Frontend is Next.js TypeScript in `/frontend`.
- Backend logic is Node.js TypeScript in `/backend`.
- SQLite is the data source of truth and Prisma schema exists at `/backend/prisma/schema.prisma`.
- Local development includes repository-root `docker-compose.yml`.
- Level definitions are maintained in `/design/levels/`.

# Non-Goals
- Multiplayer features.
- User accounts or cloud saves.
- Complex physics simulation.
- Variables, custom blocks, or advanced Scratch-style features in MVP.

# Acceptance Criteria
- Deterministic execution runs in consistent top-to-bottom order.
- The currently executing block is highlighted during playback.
- Level 1 is solvable using only `Move` blocks.
- Level 2 is solvable with a sequence that includes `Jump`.
- Progress persistence works across refresh using `localStorage`.
- No user accounts or personal data collection are required.
- Core interactions are keyboard operable with visible focus states and readable sizing.
- Tests run offline and deterministically, and the documented test command exits with code `0`.

# Risks
- Backend currently serves levels from JSON files; SQLite may not yet be the active runtime content source.
- Frontend may use local level data instead of backend endpoints, causing architecture drift.
- Missing dependency installation can prevent `next` commands from running.
- Documentation can drift from implementation unless validations are run after each orchestrator cycle.
