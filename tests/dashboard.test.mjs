// Optional browser tests; no JavaScript tooling is needed to serve the dashboard.
import assert from "node:assert/strict";
import { before, after, test } from "node:test";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname } from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixtures = resolve(root, "src/lesmots/lexrender/static/demo");
// served the way lexrender hosts it behind a prefix: /lesmots/dashboard (+ /dashboard/demo/*)
const prefix = "/lesmots";
const pagePath = prefix + "/dashboard";
const staticDir = "src/lesmots/lexrender/static/";
const fixture = async (name) => JSON.parse(await readFile(resolve(fixtures, name + ".json"), "utf8"));
const summary = await fixture("summary");
const entries = await fixture("entries");
let browser, server, origin;

before(async () => {
  server = createServer(async (req, res) => {
    let urlPath = new URL(req.url, "http://localhost").pathname;
    if (urlPath === pagePath) urlPath = "/" + staticDir + "dashboard.html";
    else if (urlPath.startsWith(pagePath + "/demo/")) urlPath = "/" + staticDir + "demo/" + urlPath.slice((pagePath + "/demo/").length);
    const path = resolve(root, "." + urlPath);
    if (!path.startsWith(root)) { res.writeHead(403).end(); return; }
    try {
      const data = await readFile(path);
      res.setHeader("Content-Type", { ".html": "text/html", ".json": "application/json" }[extname(path)] || "application/octet-stream");
      res.end(data);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  origin = "http://127.0.0.1:" + server.address().port;
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await new Promise((done) => server?.close(done));
});

async function newPage(t, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, colorScheme: "light", ...options });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
  return page;
}
async function seedToken(page) {
  await page.addInitScript(() => localStorage.setItem("lexrender_token", "test-token"));
}
async function loaded(page) { await page.locator("#entries-body tr").first().waitFor(); }
async function textIs(page, id, value) {
  await page.waitForFunction(([id, value]) => document.getElementById(id).textContent === value, [id, value]);
}
async function api(page, overrides = {}) {
  const seen = [];
  await page.route("**/api/dashboard/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    assert.ok(url.pathname.startsWith(prefix + "/api/dashboard/"), url.pathname);
    const path = url.pathname.slice((prefix + "/api/dashboard/").length);
    seen.push(url.pathname.slice(prefix.length) + url.search);
    assert.equal(request.headers().authorization, "Bearer test-token");
    if (overrides[path]) { await overrides[path](route, url); return; }
    const name = path.startsWith("entry/") ? "entry-" + decodeURIComponent(path.slice(6)) : path;
    await route.fulfill({ json: await fixture(name) });
  });
  return seen;
}

test("demo uses fixtures, preserves a saved token, and sorts all three ways", async (t) => {
  const page = await newPage(t);
  await seedToken(page);
  const requests = [];
  page.on("request", (request) => requests.push(request));
  await page.goto(origin + pagePath + "?demo=1");
  await loaded(page);
  assert.deepEqual(await page.locator(".stat-value").allTextContents(), ["12", "168", "9", "75%"]);
  assert.equal(await page.locator("#entries-body tr").count(), entries.length);
  assert.equal(await page.locator("#surfaces li").count(), 4);
  assert.equal(await page.locator("#sections li").count(), 4);
  assert.equal(await page.locator("#entries-body a").first().textContent(), "fall back to");
  await page.selectOption("#sort", "phrase");
  const alphabetical = [...entries].sort((a, b) => a.phrase.localeCompare(b.phrase));
  await page.waitForFunction((phrase) => document.querySelector("#entries-body a").textContent === phrase, alphabetical[0].phrase);
  assert.deepEqual(await page.locator("#entries-body a").allTextContents(), alphabetical.map((entry) => entry.phrase));
  await page.selectOption("#sort", "recent");
  const recent = [...entries].sort((a, b) => (Date.parse(b.last_seen) || 0) - (Date.parse(a.last_seen) || 0) || a.phrase.localeCompare(b.phrase));
  await page.waitForFunction((phrase) => document.querySelector("#entries-body a").textContent === phrase, recent[0].phrase);
  assert.equal(await page.locator("#entries-body a").first().textContent(), recent[0].phrase);
  await page.selectOption("#sort", "exposures");
  await page.waitForFunction(() => document.querySelector("#entries-body a").textContent === "fall back to");
  assert.equal(await page.locator("#entries-body a").first().getAttribute("href"), entries[0].link);
  assert.equal(await page.evaluate(() => localStorage.getItem("lexrender_token")), "test-token");
  assert.equal(requests.some((request) => request.url().includes("/api/")), false);
  assert.equal(requests.some((request) => request.headers().authorization), false);
  assert.equal(requests.every((request) => new URL(request.url()).origin === origin), true);
});

test("missing token waits for sign-in; pasted token authenticates every API and sign-out clears it", async (t) => {
  const page = await newPage(t);
  const seen = await api(page);
  await page.goto(origin + pagePath);
  await page.locator("#auth").waitFor();
  assert.deepEqual(seen, []);
  await page.fill("#token", " test-token ");
  await page.getByRole("button", { name: "Open my dashboard" }).click();
  await loaded(page);
  assert.equal(await page.evaluate(() => localStorage.getItem("lexrender_token")), "test-token");
  assert.equal(await page.inputValue("#token"), "");
  for (const sort of ["recent", "phrase"]) {
    const response = page.waitForResponse((response) => response.url().endsWith("entries?sort=" + sort));
    await page.selectOption("#sort", sort);
    await response;
  }
  await page.locator("#entries-body td.numeric").first().click();
  await page.locator(".history li").first().waitFor();
  assert.equal(await page.locator("#entry-title").textContent(), entries[0].phrase);
  assert.equal(await page.locator(".history li").count(), entries[0].exposures);
  assert.ok((await page.locator(".history").textContent()).includes("Replaced “"));
  assert.ok((await page.locator(".history").textContent()).includes("API reliability review"));
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#entry-panel").evaluate((element) => element.open), false);
  await page.waitForFunction(() => document.activeElement === document.querySelector(".history-button"));
  assert.deepEqual([...new Set(seen)].sort(), ["/api/dashboard/summary", "/api/dashboard/timeline?days=30", "/api/dashboard/surfaces", "/api/dashboard/entries?sort=exposures", "/api/dashboard/entries?sort=recent", "/api/dashboard/entries?sort=phrase", "/api/dashboard/entry/fall-back-to"].sort());
  await page.click("#sign-out");
  await page.locator("#auth").waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem("lexrender_token")), null);
  assert.equal(await page.locator("#dashboard").isVisible(), false);
});

for (const endpoint of ["summary", "timeline", "surfaces", "entries", "entry/fall-back-to"]) {
  test("401 from " + endpoint + " clears the saved token and hides personal data", async (t) => {
    const page = await newPage(t);
    await seedToken(page);
    await api(page, { [endpoint]: (route) => route.fulfill({ status: 401, json: {} }) });
    await page.goto(origin + pagePath);
    if (endpoint.startsWith("entry/")) {
      await loaded(page);
      await page.locator(".history-button").first().click();
    }
    await page.locator("#auth").waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem("lexrender_token")), null);
    assert.equal(await page.locator("#dashboard").isVisible(), false);
    assert.equal(await page.locator("#entry-panel").evaluate((element) => element.open), false);
    assert.match(await page.locator("#auth-message").textContent(), /expired or is invalid/);
  });
}

test("empty data renders zero coverage, a complete 30-day chart, and useful empty states", async (t) => {
  const page = await newPage(t);
  await seedToken(page);
  await api(page, Object.fromEntries(["summary", "timeline", "surfaces", "entries"].map((endpoint) => [endpoint, (route) => route.fulfill({ json: endpoint === "summary" ? { entries: 0, version: 0, exposures_total: 0, entries_exposed: 0, by_section: [] } : [] })])));
  await page.goto(origin + pagePath);
  await page.locator("#entries-empty").waitFor();
  assert.deepEqual(await page.locator(".stat-value").allTextContents(), ["0", "0", "0", "0%"]);
  assert.equal(await page.locator("#timeline .chart-point").count(), 30);
  assert.equal(await page.locator("#timeline-data tbody tr").count(), 30);
  assert.equal(await page.locator("#timeline-data tbody td:nth-child(2)").allTextContents().then((values) => values.every((value) => value === "0")), true);
  assert.match(await page.locator("#surfaces").textContent(), /No exposures/);
  await textIs(page, "chart-readout", "No exposures recorded in this period");
});

test("sparse timeline keeps zero days and exposes point values to keyboard users", async (t) => {
  const page = await newPage(t);
  await page.route("**/timeline.json", (route) => route.fulfill({ json: [{ date: "2026-11-14", count: 5 }] }));
  await page.goto(origin + pagePath + "?demo=1");
  await loaded(page);
  await textIs(page, "timeline-total", "5");
  const cells = await page.locator("#timeline-data tbody td:nth-child(2)").allTextContents();
  assert.deepEqual(cells, [...Array(29).fill("0"), "5"]);
  await page.locator(".chart-point").last().focus();
  assert.match(await page.locator("#chart-readout").textContent(), /5 exposures/);
});

test("dashboard and detail failures can be retried", async (t) => {
  const page = await newPage(t);
  await seedToken(page);
  let summaryCalls = 0, detailCalls = 0;
  await api(page, {
    summary: async (route) => route.fulfill(++summaryCalls === 1 ? { status: 503, json: {} } : { json: summary }),
    "entry/fall-back-to": async (route) => route.fulfill(++detailCalls === 1 ? { status: 503, json: {} } : { json: await fixture("entry-fall-back-to") })
  });
  await page.goto(origin + pagePath);
  await page.locator("#error").waitFor();
  await page.click("#retry");
  await loaded(page);
  await page.locator(".history-button").first().click();
  await page.locator("#entry-detail button").click();
  await page.locator(".history li").first().waitFor();
  assert.equal(await page.locator(".history li").count(), 28);
});

test("late sort responses cannot overwrite the latest selection", async (t) => {
  const page = await newPage(t);
  await seedToken(page);
  let delayedRoute;
  await api(page, { entries: async (route, url) => {
    if (url.searchParams.get("sort") === "recent") { delayedRoute = route; return; }
    await route.fulfill({ json: url.searchParams.get("sort") === "phrase" ? [...entries].reverse() : entries });
  } });
  await page.goto(origin + pagePath);
  await loaded(page);
  const requested = page.waitForRequest((request) => request.url().endsWith("entries?sort=recent"));
  await page.selectOption("#sort", "recent");
  await requested;
  await page.selectOption("#sort", "phrase");
  await page.waitForFunction((phrase) => document.querySelector("#entries-body a").textContent === phrase, entries.at(-1).phrase);
  await delayedRoute.fulfill({ json: entries });
  await page.waitForLoadState("networkidle");
  assert.equal(await page.locator("#entries-body a").first().textContent(), entries.at(-1).phrase);
});

test("API strings are rendered as text and unsafe phrase links are rejected", async (t) => {
  const page = await newPage(t);
  await seedToken(page);
  const unsafe = { ...entries[0], phrase: '<img src=x onerror="window.injected=true">', link: "javascript:window.injected=true", section: "<script>bad()</script>" };
  await api(page, {
    entries: (route) => route.fulfill({ json: [unsafe] }),
    "entry/fall-back-to": (route) => route.fulfill({ json: { entry: unsafe, exposures: [{ at: null, surface: "<b>Codex</b>", label: "<img src=x>", mode: "local", via: "<script>bad()</script>" }] } })
  });
  await page.goto(origin + pagePath);
  await loaded(page);
  assert.equal(await page.locator("#entries-body a").first().getAttribute("href"), null);
  assert.equal(await page.locator("#entries-body img, #entries-body script").count(), 0);
  await page.locator(".history-button").first().click();
  await page.locator(".history li").waitFor();
  assert.equal(await page.locator("#entry-link").isVisible(), false);
  assert.equal(await page.locator(".history img, .history script, .history b").count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
});

test("light, dark, and 375px layouts fit; modal traps focus and closes with Escape", async (t) => {
  const page = await newPage(t);
  const screenshots = process.env.DASHBOARD_SCREENSHOTS === "1";
  const output = resolve(root, "docs/screenshots/dashboard");
  if (screenshots) await mkdir(output, { recursive: true });
  await page.goto(origin + pagePath + "?demo=1");
  await loaded(page);
  const lightBg = await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor);
  if (screenshots) await page.screenshot({ path: resolve(output, "desktop-light.png"), fullPage: true });
  await page.emulateMedia({ colorScheme: "dark" });
  assert.notEqual(await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor), lightBg);
  if (screenshots) await page.screenshot({ path: resolve(output, "desktop-dark.png"), fullPage: true });
  await page.locator(".history-button").first().focus();
  await page.keyboard.press("Enter");
  await page.locator(".history li").first().waitFor();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.getElementById("entry-panel").contains(document.activeElement)), true);
  if (screenshots) await page.screenshot({ path: resolve(output, "history-dark.png") });
  await page.keyboard.press("Escape");
  for (const theme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: theme });
    await page.setViewportSize({ width: 375, height: 812 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 375);
    await page.waitForFunction(() => document.getElementById("timeline").getAttribute("viewBox") === "0 0 340 198");
    if (screenshots && theme === "light") await page.screenshot({ path: resolve(output, "mobile-light.png"), fullPage: true });
    await page.locator(".history-button").first().click();
    await page.locator(".history li").first().waitFor();
    assert.ok(await page.locator("#entry-panel").evaluate((element) => element.getBoundingClientRect().width <= 375));
    await page.keyboard.press("Escape");
  }
});
