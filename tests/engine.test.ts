import test from "node:test";
import assert from "node:assert/strict";
import { buildExecutionTrace } from "../frontend/src/lib/engine.ts";
import { levels } from "../frontend/src/lib/levels.ts";

test("level 1 succeeds with simple moves", () => {
  const level1 = levels[0];
  const trace = buildExecutionTrace(level1, ["Move", "Move", "Move", "Move", "Move"]);
  const last = trace.at(-1);

  assert.ok(last);
  assert.equal(last.status, "success");
  assert.equal(last.dogPosition, 5);
});

test("level 2 fails without jump", () => {
  const level2 = levels[1];
  const trace = buildExecutionTrace(level2, ["Move", "Move", "Move"]);
  const last = trace.at(-1);

  assert.ok(last);
  assert.equal(last.status, "failed");
  assert.match(last.reason ?? "", /Try Jump|trouble/i);
});

test("level 2 succeeds with move jump move move", () => {
  const level2 = levels[1];
  const trace = buildExecutionTrace(level2, ["Move", "Jump", "Move", "Move"]);
  const last = trace.at(-1);

  assert.ok(last);
  assert.equal(last.status, "success");
  assert.equal(last.dogPosition, 5);
});

test("deterministic trace repeats exactly", () => {
  const level4 = levels[3];
  const blocks = ["Jump", "Move", "Jump", "Move"] as const;

  const first = buildExecutionTrace(level4, [...blocks]);
  const second = buildExecutionTrace(level4, [...blocks]);

  assert.deepEqual(first, second);
});
