import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const webRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const version = `${Date.now()}-${randomUUID().slice(0, 8)}`;
const publicDir = join(webRoot, "public");
mkdirSync(publicDir, { recursive: true });
writeFileSync(join(publicDir, "build-version.json"), JSON.stringify({ version }));

const nextCli = join(webRoot, "node_modules", "next", "dist", "bin", "next");
const build = spawnSync(process.execPath, [nextCli, "build"], {
  cwd: webRoot,
  env: { ...process.env, NEXT_PUBLIC_APP_BUILD_VERSION: version },
  stdio: "inherit",
});
if (build.error) throw build.error;
process.exit(build.status ?? 1);
