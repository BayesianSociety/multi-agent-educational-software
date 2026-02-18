export type Block = "Move" | "Jump";
export type Tile = "ground" | "gap" | "obstacle";

export type Level = {
  id: number;
  title: string;
  narrative: string;
  worldStory: string;
  tiles: Tile[];
  start: number;
  goal: number;
};

export const levels: Level[] = [
  {
    id: 1,
    title: "Park Path",
    narrative: "Taffy really wants that bone!",
    worldStory: "Taffy is exploring the park to find treats!",
    tiles: ["ground", "ground", "ground", "ground", "ground", "ground"],
    start: 0,
    goal: 5
  },
  {
    id: 2,
    title: "Little Gap",
    narrative: "A small crack blocks the path.",
    worldStory: "Taffy learns to jump over tricky spots.",
    tiles: ["ground", "ground", "gap", "ground", "ground", "ground"],
    start: 0,
    goal: 5
  },
  {
    id: 3,
    title: "Bench Hop",
    narrative: "A low bench is in the way.",
    worldStory: "The park has playful obstacles everywhere.",
    tiles: ["ground", "obstacle", "ground", "ground", "ground", "ground"],
    start: 0,
    goal: 5
  },
  {
    id: 4,
    title: "Twin Trouble",
    narrative: "Two tricky spots appear ahead.",
    worldStory: "Taffy keeps moving with careful planning.",
    tiles: ["ground", "gap", "ground", "obstacle", "ground", "ground", "ground"],
    start: 0,
    goal: 6
  },
  {
    id: 5,
    title: "Long Walk",
    narrative: "The bone is farther down the trail.",
    worldStory: "The park opens up into a wider trail.",
    tiles: ["ground", "ground", "ground", "ground", "ground", "gap", "ground", "ground"],
    start: 0,
    goal: 7
  },
  {
    id: 6,
    title: "Pond Edge",
    narrative: "Watch out for the pond edge!",
    worldStory: "Now Taffy explores near the pond.",
    tiles: ["ground", "gap", "ground", "ground", "gap", "ground", "ground"],
    start: 0,
    goal: 6
  },
  {
    id: 7,
    title: "Garden Gate",
    narrative: "A gate and gap block the route.",
    worldStory: "Taffy enters the garden paths.",
    tiles: ["ground", "obstacle", "ground", "gap", "ground", "ground", "ground"],
    start: 0,
    goal: 6
  },
  {
    id: 8,
    title: "Bridge Bits",
    narrative: "Broken bridge planks need jumps.",
    worldStory: "Taffy crosses old bridges with care.",
    tiles: ["ground", "gap", "ground", "gap", "ground", "ground", "ground"],
    start: 0,
    goal: 6
  },
  {
    id: 9,
    title: "Park Sprint",
    narrative: "A quick route with one final jump.",
    worldStory: "Taffy is close to the biggest treat yet.",
    tiles: ["ground", "ground", "obstacle", "ground", "ground", "ground", "ground"],
    start: 0,
    goal: 6
  },
  {
    id: 10,
    title: "Final Bone",
    narrative: "The grand bone glitters at the end.",
    worldStory: "Taffy reaches the park's treasure trail!",
    tiles: ["ground", "gap", "ground", "obstacle", "ground", "gap", "ground", "ground"],
    start: 0,
    goal: 7
  }
];
