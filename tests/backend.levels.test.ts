import test from "node:test";
import assert from "node:assert/strict";
import { findLevelById, loadLevels } from "../backend/src/levelsRepository.ts";

test("backend loads 10 levels from design JSON", () => {
  const levels = loadLevels();

  assert.equal(levels.length, 10);
  assert.equal(levels[0]?.id, 1);
  assert.equal(levels[9]?.id, 10);
  assert.equal(levels[0]?.narrative, "Taffy really wants that bone!");
});

test("backend level loading is deterministic", () => {
  const first = loadLevels();
  const second = loadLevels();

  assert.deepEqual(first, second);
});

test("backend can find a single level by id", () => {
  const level = findLevelById(2);

  assert.ok(level);
  assert.equal(level.id, 2);
  assert.equal(level.tutorialFocus, "Jump");
});
