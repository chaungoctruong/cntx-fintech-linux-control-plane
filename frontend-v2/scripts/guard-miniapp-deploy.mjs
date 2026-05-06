import fs from "node:fs";
import path from "node:path";

const frontendRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const repoRoot = path.resolve(frontendRoot, "..");
const outDir = path.join(frontendRoot, "out");
const blockedNames = new Set(["bot-trading", "trading-bot"]);
const blockedRoots = [
  path.join(repoRoot, "bot-trading"),
  path.join(repoRoot, "trading-bot"),
].map((item) => path.resolve(item));

function isInside(child, parent) {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function fail(message) {
  console.error(`[miniapp-deploy-guard] ${message}`);
  process.exitCode = 1;
}

function checkPathSegments(targetPath) {
  const relative = path.relative(frontendRoot, targetPath);
  for (const part of relative.split(path.sep)) {
    if (blockedNames.has(part)) {
      fail(`blocked bot repository path leaked into frontend deploy output: ${relative}`);
      return;
    }
  }
}

function checkSymlink(targetPath) {
  const stat = fs.lstatSync(targetPath);
  if (!stat.isSymbolicLink()) {
    return;
  }
  const resolved = fs.realpathSync(targetPath);
  if (blockedRoots.some((blockedRoot) => fs.existsSync(blockedRoot) && isInside(resolved, blockedRoot))) {
    fail(`frontend deploy output contains symlink into bot repository: ${targetPath} -> ${resolved}`);
  }
}

function walk(targetPath) {
  checkPathSegments(targetPath);
  checkSymlink(targetPath);

  const stat = fs.lstatSync(targetPath);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    return;
  }

  for (const entry of fs.readdirSync(targetPath)) {
    walk(path.join(targetPath, entry));
  }
}

for (const name of blockedNames) {
  const directPath = path.join(frontendRoot, name);
  if (fs.existsSync(directPath)) {
    fail(`blocked bot repository is inside frontend project root: ${directPath}`);
  }
}

if (!fs.existsSync(outDir)) {
  fail(`frontend export directory not found: ${outDir}`);
} else {
  walk(outDir);
}

if (process.exitCode) {
  process.exit();
}

console.log("[miniapp-deploy-guard] OK: frontend export contains no bot-trading payloads");
