import test from "node:test";
import assert from "node:assert/strict";
import { routeRequest } from "../backend/src/server.ts";

test("GET /health returns ok", () => {
  const result = routeRequest("GET", "/health");
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { status: "ok" });
});

test("GET /api/levels/:id returns matching level", () => {
  const result = routeRequest("GET", "/api/levels/1");
  assert.equal(result.status, 200);
  assert.equal((result.body as { level: { id: number } }).level.id, 1);
});
