const KEY = "taffy_unlocked_level";

export function loadUnlockedLevel(): number {
  if (typeof window === "undefined") {
    return 1;
  }

  const value = window.localStorage.getItem(KEY);
  const parsed = Number.parseInt(value ?? "1", 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return 1;
  }
  return parsed;
}

export function saveUnlockedLevel(level: number): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(KEY, String(level));
}
