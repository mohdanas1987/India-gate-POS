import { chromium } from "playwright";
import { existsSync } from "node:fs";

// The build/CI sandbox this was originally written in ships a pre-installed
// Chromium at a fixed path (no network access to download one). Outside
// that sandbox — e.g. on a developer's own machine — this path won't
// exist, so fall back to Playwright's own managed browser (run
// `npx playwright install chromium` once if that hasn't been done yet).
const SANDBOX_CHROMIUM = "/opt/pw-browsers/chromium";
const launchOptions = { args: ["--no-sandbox"] };
if (existsSync(SANDBOX_CHROMIUM)) {
  launchOptions.executablePath = SANDBOX_CHROMIUM;
}

const browser = await chromium.launch(launchOptions);
const page = await browser.newPage();
const errors = [];
page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
page.on("pageerror", (err) => errors.push(String(err)));

await page.goto("http://127.0.0.1:5173/");

// Should redirect to login since no token yet.
await page.waitForSelector("text=India Gate POS");
console.log("STEP 1 OK: login page shown");

await page.fill('input[type="email"]', "owner@indiagate.nl");
await page.fill('input[type="password"]', "Sup3rSecret!");
await page.click('button[type="submit"]');

try {
  await page.waitForSelector('input[placeholder*="Search product"]', { timeout: 5000 });
  console.log("STEP 2 OK: logged in, POS screen shown");
} catch (e) {
  console.log("DEBUG page content:", await page.content());
  console.log("DEBUG console errors so far:", errors);
  throw e;
}

await page.fill('input[placeholder*="Search product"]', "rice");
await page.waitForSelector("text=Basmati Rice 5kg", { timeout: 5000 });
console.log("STEP 3 OK: product search returned real backend data");

await page.click("text=Basmati Rice 5kg");
await page.waitForSelector("text=CASH — Complete Sale");
const cartText = await page.textContent("section.border-t");
if (!cartText.includes("Basmati Rice")) throw new Error("Product not added to cart");
console.log("STEP 4 OK: product added to cart");

await page.click("text=CASH — Complete Sale");
await page.waitForSelector("text=/Order #\\d+/", { timeout: 5000 });
const receiptText = await page.textContent("section.border-t");
console.log("STEP 5 OK: checkout completed ->", receiptText.match(/Order #\d+.*?\d+\.\d{2}/)[0]);

if (errors.length > 0) {
  console.log("CONSOLE ERRORS DETECTED:", errors);
  process.exit(1);
}

await browser.close();
console.log("SMOKE TEST PASSED — full login -> search -> cart -> checkout flow verified in a real headless browser against the live API.");
