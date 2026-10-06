import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";

const zip = (version, build) => execFileSync("python3", ["-c", `
import io, json, sys, zipfile
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, 'w') as archive:
    archive.writestr('manifest.json', json.dumps({'version': sys.argv[1]}))
    archive.writestr('build.txt', sys.argv[2])
sys.stdout.buffer.write(buffer.getvalue())
`, version, build]);
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const oldZip = zip("0.2.0", "old");
const currentZip = zip("0.2.3", "current");
const rebuiltZip = zip("0.2.3", "rebuilt");
const website = new URL("../../website/", import.meta.url);

let server, origin, servedZip, metadata, metadataStatus, metadataDelay, zipRequests;
test.beforeAll(async () => {
  server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname === "/downloads/threadly-extension.json") {
      await new Promise((resolve) => setTimeout(resolve, metadataDelay));
      if (response.destroyed) return;
      response.writeHead(metadataStatus, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(JSON.stringify(metadata));
    } else if (url.pathname === "/downloads/threadly-extension.zip") {
      zipRequests++;
      // Simulate a cached pre-fix response at the bare URL. New URLs model the
      // reviewed Caddy no-store policy, verified separately against real Caddy.
      response.writeHead(200, {
        "Content-Type": "application/zip",
        "Content-Disposition": "attachment; filename=threadly-extension.zip",
        "Cache-Control": url.search ? "no-store" : "public, max-age=86400",
      });
      response.end(servedZip);
    } else {
      const file = url.pathname === "/install/" ? "install/index.html" : url.pathname.slice(1);
      try {
        const body = await readFile(new URL(file, website));
        response.writeHead(200, {
          "Content-Type": file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html",
          "Cache-Control": "no-cache",
          "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:",
        });
        response.end(body);
      } catch { response.writeHead(404); response.end(); }
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => { await new Promise((resolve) => server.close(resolve)); });
test.beforeEach(() => {
  servedZip = currentZip;
  metadata = { version: "0.2.3", sha256: digest(currentZip) };
  metadataStatus = 200;
  metadataDelay = 0;
  zipRequests = 0;
});

async function downloadedBytes(page) {
  const pending = page.waitForEvent("download");
  await page.locator("[data-extension-download]").click();
  const download = await pending;
  expect(await download.failure()).toBeNull();
  return readFile(await download.path());
}

test("a cached 0.2.0 bare URL cannot override the advertised 0.2.3 download", async ({ page }) => {
  await page.goto(`${origin}/install/`);
  servedZip = oldZip;
  const cached = await page.evaluate(async () => [...new Uint8Array(await (await fetch("/downloads/threadly-extension.zip")).arrayBuffer())]);
  expect(Buffer.from(cached)).toEqual(oldZip);
  servedZip = currentZip;
  // Prove the old URL is still stale, with no second server request.
  const repeated = await page.evaluate(async () => [...new Uint8Array(await (await fetch("/downloads/threadly-extension.zip")).arrayBuffer())]);
  expect(Buffer.from(repeated)).toEqual(oldZip);
  expect(zipRequests).toBe(1);
  await expect(page.locator("[data-extension-version]")).toHaveText("Version 0.2.3");
  await expect(page.locator("[data-extension-download]")).toHaveAttribute("href", `/downloads/threadly-extension.zip?sha256=${digest(currentZip)}`);
  expect(await downloadedBytes(page)).toEqual(currentZip);
  expect(zipRequests).toBe(2);
});

test("same-version rebuilds and rollbacks change download identity", async ({ page }) => {
  for (const [version, bytes] of [["0.2.3", currentZip], ["0.2.3", rebuiltZip], ["0.2.0", oldZip]]) {
    servedZip = bytes;
    metadata = { version, sha256: digest(bytes) };
    await page.goto(`${origin}/install/`);
    await expect(page.locator("[data-extension-download]")).toHaveAttribute("href", `/downloads/threadly-extension.zip?sha256=${digest(bytes)}`);
    await expect(page.locator("[data-extension-version]")).toHaveText(`Version ${version}`);
    expect(await downloadedBytes(page)).toEqual(bytes);
  }
});

for (const scenario of ["unavailable", "invalid checksum", "invalid version", "timeout"]) {
  test(`the uncached fallback remains usable when metadata is ${scenario}`, async ({ page }) => {
    if (scenario === "unavailable") metadataStatus = 503;
    if (scenario === "invalid checksum") metadata.sha256 = "../../other.zip?unexpected=1";
    if (scenario === "invalid version") metadata.version = "<script>unexpected</script>";
    if (scenario === "timeout") metadataDelay = 6000;
    const completed = scenario === "timeout"
      ? page.waitForEvent("requestfailed", (request) => request.url().endsWith("threadly-extension.json"))
      : page.waitForResponse((response) => response.url().endsWith("threadly-extension.json"))
        .then((response) => scenario === "unavailable" ? null : response.finished());
    await page.goto(`${origin}/install/`);
    await completed;
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.locator("[data-extension-version]")).toBeHidden();
    await expect(page.locator("[data-extension-download]")).toHaveAttribute("href", "/downloads/threadly-extension.zip?download=1");
    expect(await downloadedBytes(page)).toEqual(currentZip);
  });
}

test("JavaScript-disabled downloads avoid the legacy bare URL too", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto(`${origin}/install/`);
  await expect(page.locator("[data-extension-version]")).toBeHidden();
  expect(await downloadedBytes(page)).toEqual(currentZip);
  await context.close();
});
