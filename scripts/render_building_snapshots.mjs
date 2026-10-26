// Offline step: renders an oblique 3D view of each hotel building that has no
// photo, from the local PMTiles basemap (OSM/Overture heights) via the hidden
// /snapshot/:id route of a running stand. Output is JPEG in data/raw; the
// companion scripts/build_snapshot_index.py converts it to WebP for the seed.
// Usage: node scripts/render_building_snapshots.mjs [http://localhost:8080]
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, existsSync } from "node:fs";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium } = require("playwright");
const base = process.argv[2] || "http://localhost:8080";
const out = new URL("../data/raw/building_snapshots/", import.meta.url).pathname;
mkdirSync(out, { recursive: true });
const hotels = JSON.parse(readFileSync(new URL("../data/seed/hotels.json", import.meta.url)));
const photos = JSON.parse(readFileSync(new URL("../data/seed/hotel_photos.json", import.meta.url))).photos;
const todo = hotels.filter((h) => !photos[h.id]);
// SHARD=k/n renders every n-th hotel starting at k, for parallel processes.
const [shard, shards] = (process.env.SHARD || "0/1").split("/").map(Number);
const mine = todo.filter((_, i) => i % shards === shard);
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || undefined,
});
const page = await (await browser.newContext({ viewport: { width: 960, height: 600 } })).newPage();
let done = 0;
for (const hotel of mine) {
  const file = `${out}${hotel.id}.jpg`;
  if (existsSync(file) && !process.env.FORCE) continue;
  await page.goto(`${base}/snapshot/${encodeURIComponent(hotel.id)}`, { waitUntil: "load" });
  const target = page.locator("[data-snapshot-status]");
  await page.waitForSelector('[data-snapshot-status="ready"], [data-snapshot-status="error"], [data-snapshot-status="missing"]', { timeout: 45000 });
  if ((await target.getAttribute("data-snapshot-status")) !== "ready") {
    console.warn(`skip ${hotel.id}: ${await target.getAttribute("data-snapshot-status")}`);
    continue;
  }
  await page.waitForTimeout(400);
  await target.screenshot({ path: file, type: "jpeg", quality: 92 });
  done += 1;
}
console.log(`rendered ${done} of ${mine.length} hotels (shard ${shard}/${shards}) into ${out}`);
await browser.close();
