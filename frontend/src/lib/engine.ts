import type { Block, Level, Tile } from "./levels";

export type ProgramInstruction = {
  op: Block;
};

export type ExecutionStep = {
  blockIndex: number;
  op: Block;
  dogPosition: number;
  status: "running" | "success" | "failed";
  reason?: string;
};

const isBlockingTile = (tile: Tile | undefined): boolean => tile === "gap" || tile === "obstacle";

export function compileBlocks(blocks: Block[]): ProgramInstruction[] {
  return blocks.map((op) => ({ op }));
}

export function applyInstruction(level: Level, dogPosition: number, op: Block): { nextPosition: number; failed: boolean; reason?: string } {
  if (op === "Move") {
    const next = dogPosition + 1;
    const tile = level.tiles[next];
    if (tile === undefined) {
      return { nextPosition: dogPosition, failed: true, reason: "Taffy ran out of path." };
    }
    if (isBlockingTile(tile)) {
      return { nextPosition: next, failed: true, reason: "Taffy hit trouble. Try Jump." };
    }
    return { nextPosition: next, failed: false };
  }

  const over = dogPosition + 1;
  const landing = dogPosition + 2;
  const overTile = level.tiles[over];
  const landingTile = level.tiles[landing];

  if (landingTile === undefined) {
    return { nextPosition: dogPosition, failed: true, reason: "Jump was too far." };
  }

  if (!isBlockingTile(overTile)) {
    return { nextPosition: dogPosition, failed: true, reason: "Jump should cross one obstacle or gap." };
  }

  if (isBlockingTile(landingTile)) {
    return { nextPosition: landing, failed: true, reason: "Taffy landed on a bad tile." };
  }

  return { nextPosition: landing, failed: false };
}

export function buildExecutionTrace(level: Level, blocks: Block[]): ExecutionStep[] {
  const instructions = compileBlocks(blocks);
  const steps: ExecutionStep[] = [];
  let dog = level.start;

  for (let index = 0; index < instructions.length; index += 1) {
    const current = instructions[index];
    const result = applyInstruction(level, dog, current.op);
    dog = result.nextPosition;

    const reachedGoal = dog >= level.goal;
    const status: ExecutionStep["status"] = result.failed ? "failed" : reachedGoal ? "success" : "running";

    steps.push({
      blockIndex: index,
      op: current.op,
      dogPosition: dog,
      status,
      reason: result.reason
    });

    if (status !== "running") {
      return steps;
    }
  }

  if (dog < level.goal) {
    steps.push({
      blockIndex: instructions.length,
      op: "Move",
      dogPosition: dog,
      status: "failed",
      reason: "Taffy did not reach the bone yet."
    });
  }

  return steps;
}
