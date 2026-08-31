import fs from "node:fs";
import { spawnSync } from "node:child_process";

const secretPath = process.env.CRAWLSEO_DB_SECRET || "/run/secrets/crawlseo_db_password";
const password = fs.readFileSync(secretPath, "utf8").trim();
if (!password) throw new Error("CrawlSEO database password is empty");

const user = encodeURIComponent(process.env.CRAWLSEO_DB_USER || "crawlseo");
const database = encodeURIComponent(process.env.CRAWLSEO_DB_NAME || "crawlseo");
const host = process.env.CRAWLSEO_DB_HOST || "127.0.0.1";
const port = process.env.CRAWLSEO_DB_PORT || "5432";
process.env.DATABASE_URL = `postgresql://${user}:${encodeURIComponent(password)}@${host}:${port}/${database}?schema=public`;

if (process.argv[2] === "--serve") {
  const { startServer } = await import("./extella_worker_server.mjs");
  startServer();
} else if (process.argv[2] === "--migrate") {
  const result = spawnSync(
    "node",
    ["node_modules/prisma/build/index.js", "db", "push", "--skip-generate"],
    { cwd: "/app", env: process.env, stdio: "inherit" },
  );
  process.exit(result.status ?? 1);
} else {
  await import("./crawlseo_once.mjs");
}
