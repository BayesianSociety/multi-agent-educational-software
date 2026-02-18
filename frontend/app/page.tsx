"use client";

import { useEffect, useMemo, useState } from "react";
import { buildExecutionTrace, type ExecutionStep } from "../src/lib/engine";
import { levels, type Block } from "../src/lib/levels";
import { loadUnlockedLevel, saveUnlockedLevel } from "../src/lib/storage";

type RunState = "idle" | "running" | "success" | "failed";

const tickMs = 600;

export default function HomePage() {
  const [selectedLevel, setSelectedLevel] = useState(1);
  const [unlockedLevel, setUnlockedLevel] = useState(1);
  const [program, setProgram] = useState<Block[]>([]);
  const [dogPosition, setDogPosition] = useState(levels[0].start);
  const [activeBlock, setActiveBlock] = useState<number | null>(null);
  const [statusText, setStatusText] = useState("Build a program, then press Run.");
  const [runState, setRunState] = useState<RunState>("idle");

  const level = useMemo(() => levels[selectedLevel - 1], [selectedLevel]);

  useEffect(() => {
    const unlocked = loadUnlockedLevel();
    setUnlockedLevel(Math.min(unlocked, levels.length));
  }, []);

  useEffect(() => {
    setProgram([]);
    setDogPosition(level.start);
    setActiveBlock(null);
    setRunState("idle");
    setStatusText(level.narrative);
  }, [level]);

  useEffect(() => {
    if (runState !== "running") {
      return;
    }

    const trace = buildExecutionTrace(level, program);
    if (trace.length === 0) {
      setRunState("failed");
      setStatusText("Add at least one block.");
      return;
    }

    let index = 0;
    const timer = window.setInterval(() => {
      const step: ExecutionStep | undefined = trace[index];
      if (!step) {
        window.clearInterval(timer);
        return;
      }

      setActiveBlock(step.blockIndex < program.length ? step.blockIndex : null);
      setDogPosition(step.dogPosition);

      if (step.status === "running") {
        setStatusText(`Running block ${step.blockIndex + 1}: ${step.op}`);
      }

      if (step.status === "success") {
        window.clearInterval(timer);
        setRunState("success");
        setStatusText("Success! Taffy reached the bone.");
        const nextUnlocked = Math.min(level.id + 1, levels.length);
        if (nextUnlocked > unlockedLevel) {
          setUnlockedLevel(nextUnlocked);
          saveUnlockedLevel(nextUnlocked);
        }
        setActiveBlock(null);
      }

      if (step.status === "failed") {
        window.clearInterval(timer);
        setRunState("failed");
        setStatusText(step.reason ?? "That did not work. Try debugging your blocks.");
        setActiveBlock(null);
      }

      index += 1;
    }, tickMs);

    return () => window.clearInterval(timer);
  }, [level, program, runState, unlockedLevel]);

  const runProgram = () => {
    setDogPosition(level.start);
    setActiveBlock(null);
    setRunState("running");
    setStatusText("Running program...");
  };

  const resetProgram = () => {
    setDogPosition(level.start);
    setActiveBlock(null);
    setRunState("idle");
    setStatusText("Reset complete. Edit blocks and run again.");
  };

  return (
    <main className="page">
      <h1>Taffy Code Trail</h1>
      <p className="story">{level.worldStory}</p>

      <section className="level-picker" aria-label="Level picker">
        {levels.map((entry) => {
          const locked = entry.id > unlockedLevel;
          return (
            <button
              key={entry.id}
              type="button"
              onClick={() => !locked && setSelectedLevel(entry.id)}
              disabled={locked}
              className={entry.id === selectedLevel ? "active-level" : ""}
            >
              Level {entry.id}
            </button>
          );
        })}
      </section>

      <p className="narration" aria-live="polite">
        {statusText}
      </p>

      <section className="layout-grid">
        <div className="panel">
          <h2>Blocks</h2>
          <div className="stack">
            <button type="button" onClick={() => setProgram((prev) => [...prev, "Move"])}>
              Add Move(1)
            </button>
            <button type="button" onClick={() => setProgram((prev) => [...prev, "Jump"])}>
              Add Jump
            </button>
            <button type="button" onClick={() => setProgram([])}>
              Clear Blocks
            </button>
            <button
              type="button"
              onClick={() => setProgram((prev) => prev.slice(0, Math.max(0, prev.length - 1)))}
            >
              Remove Last
            </button>
          </div>
        </div>

        <div className="panel">
          <h2>Workspace</h2>
          <ol className="workspace" aria-label="Program workspace">
            {program.map((block, index) => (
              <li key={`${block}-${index}`} className={activeBlock === index ? "active-block" : ""}>
                {index + 1}. {block}
              </li>
            ))}
          </ol>
          <div className="stack run-row">
            <button type="button" onClick={runProgram} disabled={runState === "running"}>
              Run
            </button>
            <button type="button" onClick={resetProgram} disabled={runState === "running"}>
              Reset
            </button>
          </div>
        </div>

        <div className="panel">
          <h2>Play Area</h2>
          <p>{level.narrative}</p>
          <div className="track" role="img" aria-label="Dog path and bone goal">
            {level.tiles.map((tile, index) => {
              const hasDog = index === dogPosition;
              const hasGoal = index === level.goal;
              return (
                <div key={index} className={`tile ${tile}`}>
                  {hasGoal && <span className={runState === "success" ? "goal sparkle" : "goal"}>🦴</span>}
                  {hasDog && <span className={runState === "running" ? "dog running" : "dog"}>🐶</span>}
                </div>
              );
            })}
          </div>
          <p className="legend">Ground = green, gaps = dashed, obstacles = striped.</p>
        </div>
      </section>
    </main>
  );
}
