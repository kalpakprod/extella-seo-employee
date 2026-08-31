import fs from "node:fs";

const path = new URL("./package.json", import.meta.url);
const pkg = JSON.parse(fs.readFileSync(path, "utf8"));
for (const name of ["react", "react-dom", "react-router-dom", "recharts", "zustand"]) {
  const version = pkg.devDependencies?.[name];
  if (!version) throw new Error(`package-lock compatibility dependency missing: ${name}`);
  pkg.dependencies[name] = version;
  delete pkg.devDependencies[name];
}
const buildDevDependencies = new Set([
  "@types/better-sqlite3",
  "@types/cli-progress",
  "@types/node",
  "tsup",
  "typescript",
]);
for (const name of Object.keys(pkg.devDependencies || {})) {
  if (!buildDevDependencies.has(name)) delete pkg.devDependencies[name];
}
fs.writeFileSync(path, `${JSON.stringify(pkg, null, 2)}\n`);
