import { writeFileSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { openBrowser, renderFrames, selectComposition } from "@remotion/renderer";

const folder = resolve(process.argv[2]);
const props = JSON.parse(await readFile(join(folder, "render-props.json"), "utf8"));
const scratch = await mkdtemp(join(tmpdir(), "vocab-render-"));
let browser;
try {
  const serveUrl = await bundle({
    entryPoint: fileURLToPath(new URL("./src.jsx", import.meta.url)),
    outDir: join(scratch, "bundle"),
  });
  browser = await openBrowser("chrome", { browserExecutable: process.env.CHROME_BIN || undefined });
  const story = Math.ceil(props.narrationSeconds * 30);
  const changes = new Set([0, story, story + 75]);
  for (const item of props.timings) {
    changes.add(item.revealFrame);
    changes.add(Math.ceil(item.endSeconds * 30 - 1e-8));
  }
  const frames = [...changes].sort((a, b) => a - b).map(frame => ({ scene: 0, frame }));
  const options = { serveUrl, inputProps: { scenes: [{ ...props, annotationOffset: process.platform === "linux" ? -1 : 1 }], frames }, puppeteerInstance: browser };
  const composition = await selectComposition({ ...options, id: "PackedFrames" });
  await renderFrames({
    ...options, composition, outputDir: null, imageFormat: "jpeg", jpegQuality: 92,
    concurrency: 3, muted: true, onStart: () => {}, onFrameUpdate: () => {},
    onFrameBuffer: (buffer, index) => writeFileSync(join(folder, `frame-${index}.jpg`), buffer),
  });
  await writeFile(join(folder, "frames.json"), JSON.stringify(
    frames.map(({ frame }, index) => [frame, join(folder, `frame-${index}.jpg`)]),
  ));
} finally {
  if (browser) await browser.close({ silent: true });
  await rm(scratch, { recursive: true, force: true });
}
