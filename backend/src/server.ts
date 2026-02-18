import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { findLevelById, loadLevels } from "./levelsRepository.ts";

type RouteResult = {
  status: number;
  body: unknown;
};

function sendJson(res: http.ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(body));
}

export function routeRequest(method: string, pathname: string): RouteResult {
  if (method === "GET" && pathname === "/health") {
    return { status: 200, body: { status: "ok" } };
  }

  if (method === "GET" && pathname === "/api/levels") {
    return { status: 200, body: { levels: loadLevels() } };
  }

  const levelMatch = /^\/api\/levels\/(\d+)$/.exec(pathname);
  if (method === "GET" && levelMatch) {
    const id = Number(levelMatch[1]);
    const level = findLevelById(id);
    if (!level) {
      return { status: 404, body: { error: "Level not found" } };
    }

    return { status: 200, body: { level } };
  }

  return { status: 404, body: { error: "Not found" } };
}

export function createServer(): http.Server {
  return http.createServer((req, res) => {
    const method = req.method ?? "GET";
    const requestUrl = new URL(req.url ?? "/", "http://localhost");
    const result = routeRequest(method, requestUrl.pathname);
    sendJson(res, result.status, result.body);
  });
}

export function startServer(port = Number(process.env.PORT ?? 4000)): http.Server {
  const server = createServer();
  server.listen(port);
  return server;
}

const currentFile = fileURLToPath(import.meta.url);
const executedFile = process.argv[1] ? path.resolve(process.argv[1]) : "";

if (executedFile === currentFile) {
  startServer();
}
