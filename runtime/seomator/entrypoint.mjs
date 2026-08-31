import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const SEO_CATEGORIES = new Set([
  "core", "technical", "perf", "links", "images", "security", "crawl", "schema", "a11y", "content",
  "social", "eeat", "url", "mobile", "i18n", "legal", "js", "redirect", "htmlval", "geo",
]);
const EXPENSIVE_CATEGORIES = new Set(["perf", "js"]);
export const CLI_REQUEST_TIMEOUT_MS = 120_000;

async function assertPublicUrl(url) {
  const guardModule = process.env.EXTELLA_SAFE_FETCH_MODULE
    || new URL("../safe_fetch.mjs", import.meta.url).href;
  const { resolvePublicAddresses } = await import(guardModule);
  await resolvePublicAddresses(url);
}

export function cliTimeout(timeoutMs) {
  return Math.min(timeoutMs, CLI_REQUEST_TIMEOUT_MS);
}

export function knownTransportReason(payload) {
  const error = payload?.error;
  const fields = [payload?.status, payload?.reason, payload?.status_code, payload?.statusCode];
  if (error && typeof error === "object") fields.push(error.code, error.reason, error.status, error.status_code);
  const values = new Set(fields.filter(value => typeof value === "string" || Number.isInteger(value)).map(value => String(value).toLowerCase()));
  if ([...values].some(value => value.startsWith("captcha"))) return "captcha";
  if ([...values].some(value => value.startsWith("waf") || value.startsWith("cloudflare"))) return "waf";
  if ([...values].some(value => value.startsWith("robots"))) return "robots_denied";
  if (values.has("timeout") || values.has("timed_out") || values.has("request_timeout")) return "timeout";
  for (const status of [403, 429, 503]) if (values.has(String(status)) || values.has(`http_${status}`)) return `http_${status}`;
  return null;
}

function sourceFailure(reason) {
  const error = new Error("SEOmator source failed");
  error.code = reason;
  return error;
}

function readPlan(planPath) {
  const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
  if (
    !plan || typeof plan !== "object" || Array.isArray(plan)
    || Object.keys(plan).sort().join() !== "categories,max_pages,performance_sample_pages,timeout_ms"
    || !Number.isInteger(plan.max_pages) || plan.max_pages < 1 || plan.max_pages > 100
    || !Array.isArray(plan.categories) || plan.categories.length === 0
    || new Set(plan.categories).size !== plan.categories.length
    || plan.categories.some(category => typeof category !== "string" || !SEO_CATEGORIES.has(category))
    || !Number.isInteger(plan.performance_sample_pages) || plan.performance_sample_pages < 1
    || plan.performance_sample_pages > 5 || plan.performance_sample_pages > plan.max_pages
    || !Number.isInteger(plan.timeout_ms) || plan.timeout_ms < 1 || plan.timeout_ms > 720000
  ) throw new Error("worker plan is invalid");
  return plan;
}

function writeOutput(outputPath, value) {
  const temporary = `${outputPath}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, "utf8");
    fs.renameSync(temporary, outputPath);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function resultCategories(report, expected) {
  if (!Array.isArray(report.categoryResults)) return null;
  const categories = new Map();
  for (const category of report.categoryResults) {
    if (!category || typeof category !== "object" || typeof category.categoryId !== "string" || !Array.isArray(category.results)) {
      return null;
    }
    if (categories.has(category.categoryId)) return null;
    categories.set(category.categoryId, category);
  }
  return expected.every(category => categories.has(category)) ? categories : null;
}

function candidateFrom(value, baseUrl) {
  const rawUrl = typeof value === "string" ? value : value?.url;
  if (typeof rawUrl !== "string") return null;
  try {
    const parsed = new URL(rawUrl);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.origin !== baseUrl.origin) {
      return null;
    }
    const depth = parsed.pathname.split("/").filter(Boolean).length;
    return { url: parsed.toString(), pathDepth: depth };
  } catch {
    return null;
  }
}

export function deriveSampleUrls(report, siteUrl, maximum) {
  const baseUrl = new URL(siteUrl);
  const candidates = new Map();
  const excluded = new Set();
  const add = (value, isHomepage = false) => {
    const candidate = candidateFrom(value, baseUrl);
    if (!candidate) return;
    const existing = candidates.get(candidate.url);
    if (isHomepage || !existing) candidates.set(candidate.url, { ...candidate, pathDepth: isHomepage ? 0 : candidate.pathDepth });
  };
  const isExcludedSignal = result => {
    if (result.status === "fail" && ["technical-server-error", "technical-4xx-non-404", "technical-bad-content-type"].includes(result.ruleId)) {
      return true;
    }
    if (!(["warn", "fail"].includes(result.status)) || result.ruleId !== "core-robots-meta" || !result.details || typeof result.details !== "object") return false;
    const directiveSets = Array.isArray(result.details.allDirectives) ? result.details.allDirectives : [];
    return directiveSets.some(entry => {
      const directives = typeof entry === "string" ? [entry] : entry?.directives;
      return Array.isArray(directives) && directives.some(value => typeof value === "string" && /(^|[\s,;])noindex($|[\s,;])/i.test(value));
    });
  };
  add(baseUrl.toString(), true);
  for (const field of ["pages", "pageResults"]) {
    if (Array.isArray(report[field])) for (const page of report[field]) add(page);
  }
  if (Array.isArray(report.categoryResults)) {
    for (const category of report.categoryResults) {
      if (!category || typeof category !== "object" || !Array.isArray(category.results)) continue;
      for (const result of category.results) {
        if (!result || typeof result !== "object") continue;
        const urls = Array.isArray(result.urls) ? result.urls : [result.details?.pageUrl ?? result.url];
        for (const url of urls) {
          const candidate = candidateFrom(url, baseUrl);
          if (candidate && isExcludedSignal(result)) excluded.add(candidate.url);
          else add(url);
        }
      }
    }
  }
  return [...candidates.values()]
    .filter(candidate => !excluded.has(candidate.url))
    .sort((left, right) => left.pathDepth - right.pathDepth || left.url.localeCompare(right.url))
    .slice(0, maximum)
    .map(candidate => candidate.url);
}

function mergeCategory(categoryId, reports) {
  const entries = reports.map(report => report.get(categoryId));
  const results = entries.flatMap(entry => entry.results);
  const scores = entries.map(entry => entry.score).filter(score => Number.isFinite(score));
  return {
    ...entries[0],
    categoryId,
    results,
    passCount: results.filter(result => result.status === "pass").length,
    warnCount: results.filter(result => result.status === "warn").length,
    failCount: results.filter(result => result.status === "fail").length,
    ...(scores.length ? { score: scores.reduce((total, score) => total + score, 0) / scores.length } : {}),
  };
}

export function mergeSampleResults(mainReport, mainCategories, sampleReports, expensiveCategories) {
  const main = resultCategories(mainReport, mainCategories);
  if (!main) return null;
  const samples = sampleReports.map(report => resultCategories(report, expensiveCategories));
  if (samples.some(report => report === null)) return null;
  return [
    ...mainCategories.map(category => main.get(category)),
    ...expensiveCategories.map(category => mergeCategory(category, samples)),
  ];
}

function runCli(url, outputPath, categories, { crawl, noCwv, maxPages, timeoutMs }) {
  const args = [
    "/app/dist/cli.js", "audit", url,
    ...(crawl ? ["--crawl"] : []),
    "--categories", categories.join(","), "--format", "json", "--output", outputPath,
    ...(noCwv ? ["--no-cwv"] : []), "--max-pages", String(maxPages),
    "--timeout", String(cliTimeout(timeoutMs)),
  ];
  const result = spawnSync("node", args, { cwd: "/work", env: process.env, stdio: "inherit" });
  if (![0, 1].includes(result.status) || !fs.existsSync(outputPath)) throw new Error("SEOmator CLI did not produce output");
  const report = JSON.parse(fs.readFileSync(outputPath, "utf8"));
  const reason = knownTransportReason(report);
  if (reason) throw sourceFailure(reason);
  return report;
}

function unsupported(outputPath, plan, reason) {
  writeOutput(outputPath, { status: "unsupported", reason, coverage: {
    planned_pages: plan.max_pages, crawled_pages: 0, sampled_pages: 0, categories: plan.categories,
  } });
}

async function main() {
  if (process.argv[2] === "--serve") {
    const { startServer } = await import("./extella_worker_server.mjs");
    startServer();
    return;
  }
  const [siteUrl, outputPath, planPath] = process.argv.slice(2);
  if (!siteUrl || !outputPath || !planPath) throw new Error("usage: seomator <public-url> <output-path> <plan-json>");
  const parsedUrl = new URL(siteUrl);
  if (!["http:", "https:"].includes(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password) {
    throw new Error("site URL must use http or https without credentials");
  }
  await assertPublicUrl(parsedUrl);
  const plan = readPlan(planPath);
  const mainCategories = plan.categories.filter(category => !EXPENSIVE_CATEGORIES.has(category));
  const expensiveCategories = plan.categories.filter(category => EXPENSIVE_CATEGORIES.has(category));
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  if (!mainCategories.length) return unsupported(outputPath, plan, "seomator_sample_selection_unsupported");

  const temporary = `${outputPath}.${process.pid}.main.tmp`;
  const sampleOutputs = [];
  try {
    const mainReport = runCli(parsedUrl.toString(), temporary, mainCategories, {
      crawl: true, noCwv: true, maxPages: plan.max_pages, timeoutMs: plan.timeout_ms,
    });
    if (mainReport.error || !Number.isInteger(mainReport.crawledPages) || mainReport.crawledPages < 1 || mainReport.crawledPages > plan.max_pages) {
      throw new Error("SEOmator did not produce a complete bounded report");
    }
    const sampleUrls = expensiveCategories.length
      ? deriveSampleUrls(mainReport, parsedUrl.toString(), plan.performance_sample_pages) : [];
    const sampleReports = [];
    for (const [index, sampleUrl] of sampleUrls.entries()) {
      const sampleOutput = `${outputPath}.${process.pid}.sample-${index}.tmp`;
      sampleOutputs.push(sampleOutput);
      sampleReports.push(runCli(sampleUrl, sampleOutput, expensiveCategories, {
        crawl: false, noCwv: false, maxPages: 1, timeoutMs: plan.timeout_ms,
      }));
    }
    const categoryResults = expensiveCategories.length
      ? mergeSampleResults(mainReport, mainCategories, sampleReports, expensiveCategories)
      : resultCategories(mainReport, mainCategories) && mainReport.categoryResults;
    if (!categoryResults) return unsupported(outputPath, plan, "seomator_sample_output_unsupported");
    const { overallScore: _mainOnlyScore, ...reportWithoutMainOnlyScore } = mainReport;
    const report = {
      ...reportWithoutMainOnlyScore,
      categoryResults,
      coverage: {
        planned_pages: plan.max_pages,
        crawled_pages: mainReport.crawledPages,
        sampled_pages: sampleUrls.length,
        sampled_urls: sampleUrls,
        categories: plan.categories,
      },
    };
    writeOutput(outputPath, report);
    console.log(JSON.stringify({ source: "SEOmator", crawledPages: mainReport.crawledPages, sampledPages: sampleUrls.length, output: path.basename(outputPath) }));
  } catch (error) {
    const reason = error && typeof error === "object" && typeof error.code === "string"
      && ["waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout"].includes(error.code)
      ? error.code : "audit_failed";
    writeOutput(outputPath, {
      status: reason === "audit_failed" ? "failed" : "unavailable",
      reason,
      coverage: { planned_pages: plan.max_pages, crawled_pages: 0, sampled_pages: 0, categories: plan.categories },
    });
  } finally {
    fs.rmSync(temporary, { force: true });
    for (const sampleOutput of sampleOutputs) fs.rmSync(sampleOutput, { force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
