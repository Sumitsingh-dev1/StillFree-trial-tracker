import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { mockupPreviewPlugin } from "./mockupPreviewPlugin.ts";

async function fixture(t, files = []) {
  const root = await mkdtemp(path.join(os.tmpdir(), "mockup-discovery-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const file of files) {
    const absolute = path.join(root, "src/components/mockups", file);
    await mkdir(path.dirname(absolute), { recursive: true });
    await writeFile(absolute, "export default function Component() { return null; }");
  }
  const plugin = mockupPreviewPlugin();
  plugin.configResolved({ root });
  const generate = async () => {
    await plugin.buildStart();
    return readFile(path.join(root, "src/.generated/mockup-components.ts"), "utf8");
  };
  return { root, generate };
}

test("discovers nested TSX files, excluding private, hidden and non-TSX entries", async (t) => {
  const { root, generate } = await fixture(t, [
    "Zebra.tsx", "nested/Alpha.tsx", "_Private.tsx",
    "_private/Nested.tsx", "nested/_Private.tsx",
    ".hidden.tsx", ".hidden/Nested.tsx", "notes.ts",
  ]);
  await mkdir(path.join(root, "src/components/mockups/directory.tsx"));
  const source = await generate();
  assert.match(source, /"\.\/components\/mockups\/Zebra\.tsx"/);
  assert.match(source, /import\("\.\.\/components\/mockups\/nested\/Alpha\.tsx"\)/);
  for (const excluded of ["Private", "_private", ".hidden", "notes.ts", "directory.tsx"]) {
    assert.ok(!source.includes(excluded), `Should not discover ${excluded}`);
  }
  assert.equal((source.match(/=> import/g) ?? []).length, 2);
  assert.ok(source.indexOf("Zebra.tsx") < source.indexOf("nested/Alpha.tsx"));
  assert.equal(await generate(), source, "Discovery should produce stable output");
});

test("generates an empty module when the mockup directory is absent", async (t) => {
  const { generate } = await fixture(t);
  const source = await generate();
  assert.match(source, /export const modules: ModuleMap = \{\n\n\};/);
});

test("rescans additions and deletions", async (t) => {
  const { root, generate } = await fixture(t, ["Before.tsx"]);
  assert.match(await generate(), /Before\.tsx/);
  const directory = path.join(root, "src/components/mockups");
  await rm(path.join(directory, "Before.tsx"));
  await writeFile(path.join(directory, "After.tsx"), "export default () => null;");
  const source = await generate();
  assert.match(source, /After\.tsx/);
  assert.ok(!source.includes("Before.tsx"));
});
