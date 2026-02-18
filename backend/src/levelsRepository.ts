import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export type LevelDefinition = {
  id: number;
  slug: string;
  world: string;
  narrative: string;
  tutorialFocus: string;
  grid: {
    length: number;
    start: number;
    goal: number;
    gaps: number[];
    obstacles: number[];
  };
  allowedBlocks: Array<"Move" | "Jump">;
  recommendedSolution: Array<"Move" | "Jump">;
};

const sourceDir = path.dirname(fileURLToPath(import.meta.url));
const defaultLevelsDir = path.resolve(sourceDir, "../..", "design", "levels");

function assertLevelShape(value: unknown, fileName: string): asserts value is LevelDefinition {
  if (typeof value !== "object" || value === null) {
    throw new Error(`Invalid level in ${fileName}: expected object`);
  }

  const level = value as Record<string, unknown>;

  if (typeof level.id !== "number" || typeof level.slug !== "string" || typeof level.world !== "string") {
    throw new Error(`Invalid level in ${fileName}: missing metadata`);
  }

  if (typeof level.narrative !== "string" || typeof level.tutorialFocus !== "string") {
    throw new Error(`Invalid level in ${fileName}: missing narrative fields`);
  }

  const grid = level.grid as Record<string, unknown> | undefined;
  if (
    !grid ||
    typeof grid.length !== "number" ||
    typeof grid.start !== "number" ||
    typeof grid.goal !== "number" ||
    !Array.isArray(grid.gaps) ||
    !Array.isArray(grid.obstacles)
  ) {
    throw new Error(`Invalid level in ${fileName}: invalid grid`);
  }

  if (!Array.isArray(level.allowedBlocks) || !Array.isArray(level.recommendedSolution)) {
    throw new Error(`Invalid level in ${fileName}: invalid blocks`);
  }
}

export function loadLevels(levelsDir = defaultLevelsDir): LevelDefinition[] {
  const files = fs.readdirSync(levelsDir).filter((file) => file.endsWith(".json")).sort();

  const levels = files.map((file) => {
    const raw = fs.readFileSync(path.join(levelsDir, file), "utf8");
    const parsed = JSON.parse(raw) as unknown;
    assertLevelShape(parsed, file);
    return parsed;
  });

  return levels.sort((a, b) => a.id - b.id);
}

export function findLevelById(id: number, levelsDir = defaultLevelsDir): LevelDefinition | null {
  const levels = loadLevels(levelsDir);
  return levels.find((level) => level.id === id) ?? null;
}
