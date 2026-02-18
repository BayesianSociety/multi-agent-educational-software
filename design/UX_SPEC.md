# UX Spec (MVP)

## Screen layout
- Left: block palette (`Move(1)`, `Jump`)
- Middle: vertical workspace sequence (single lane)
- Right: playfield with dog, tiles, hazards, and bone goal
- Top controls: `Run`, `Reset`, level selector, level narrative text

## Interaction model
- Drag or click to add blocks from palette into workspace.
- Workspace executes top-to-bottom in deterministic order.
- `Run` compiles workspace into instruction list and executes step-by-step.
- Active block is highlighted during execution.
- `Reset` returns dog to start tile and clears runtime state.
- On failure, show clear on-screen text and keep blocks editable for immediate retry.

## Accessibility baseline
- Full keyboard path for block selection, ordering, `Run`, and `Reset`.
- Visible focus ring for all interactive controls.
- Success/failure and active-step states use icon/text, not color alone.
- Narrative text is always visible on screen.
- Base text size target: 16px minimum for UI controls and key labels.

## Visual tone
- Friendly, bright, kid-safe style.
- Dog movement states: idle, walk, jump.
- Goal feedback: bone sparkle animation on success.
