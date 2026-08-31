import fs from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import process from "node:process";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { PrismaClient } from "@prisma/client";
import { getDailyTraffic, getSitePeriodMetrics, getTopKeywords, getTopPages } from "./lib/seo-metrics.ts";
import { getAllOpportunities } from "./lib/seo-opportunities.ts";

const SEO_CATEGORIES = new Set([
  "core", "technical", "perf", "links", "images", "security", "crawl", "schema", "a11y", "content",
  "social", "eeat", "url", "mobile", "i18n", "legal", "js", "redirect", "htmlval", "geo",
]);

const [siteUrl, outputPath, planPath] = process.argv.slice(2);
if (!siteUrl || !outputPath || !planPath) {
  throw new Error("usage: crawlseo_once.mjs <public-url> <output-path> <plan-json>");
}

const parsedUrl = new URL(siteUrl);
if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
  throw new Error("site URL must use http or https");
}
if (parsedUrl.username || parsedUrl.password) {
  throw new Error("site URL must not include credentials");
}
const plan = JSON.parse(await fs.readFile(planPath, "utf8"));
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
parsedUrl.hash = "";
const normalizedSiteUrl = parsedUrl.toString();
const siteHash = createHash("sha256").update(normalizedSiteUrl).digest("hex").slice(0, 16);
const siteId = `extella-${siteHash}`;
const prisma = new PrismaClient();
const transport = new StdioClientTransport({
  command: "node",
  args: [process.env.TSX_CLI || "node_modules/tsx/dist/cli.mjs", "mcp/server.ts"],
  cwd: process.cwd(),
  env: process.env,
  stderr: "pipe",
});
const client = new Client({ name: "extella-seo-employee", version: "2.0.0" });

async function writeJsonAtomic(filePath, value) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await fs.rename(temporary, filePath);
}

async function readSearchPerformance(siteId) {
  const site = await prisma.site.findUnique({ where: { id: siteId }, select: { gscProperty: true } });
  if (!site?.gscProperty) {
    return {
      status: "not_configured",
      reason: "not_configured",
      instruction: "Connect a read-only Google Search Console property in CrawlSEO.",
    };
  }
  const [metrics, keywords, pages, traffic, opportunities, vitals] = await Promise.all([
    getSitePeriodMetrics(siteId, 28),
    getTopKeywords(siteId, 28, 25),
    getTopPages(siteId, 28, 25),
    getDailyTraffic(siteId, 90),
    getAllOpportunities(siteId),
    prisma.vitalsReport.findMany({
      where: { siteId }, orderBy: { date: "desc" }, take: 20,
      select: { url: true, date: true, device: true, lcp: true, fid: true, cls: true, inp: true, perfScore: true, speedIndex: true, ttfb: true },
    }),
  ]);
  return {
    status: "ready",
    period_days: 28,
    metrics,
    keywords,
    pages,
    traffic: traffic.slice(-90),
    vitals,
    opportunities: opportunities.slice(0, 30),
  };
}

try {
  await prisma.user.upsert({
    where: { email: "seo-employee-local.invalid" },
    create: {
      id: "extella-seo-employee",
      email: "seo-employee-local.invalid",
      name: "Extella SEO Employee",
    },
    update: {},
  });
  await prisma.site.upsert({
    where: { id: siteId },
    create: { id: siteId, userId: "extella-seo-employee", domain: normalizedSiteUrl },
    update: { domain: normalizedSiteUrl },
  });

  await client.connect(transport);
  const result = await client.callTool({
    name: "run_crawl",
    arguments: { siteId, maxPages: plan.max_pages },
  });
  if (result.isError) throw new Error("CrawlSEO run_crawl returned an error");

  const text = result.content
    .filter((item) => item.type === "text")
    .map((item) => item.text)
    .join("\n");
  const crawlId = text.match(/Crawl ID:\s*([^\s]+)/)?.[1];
  if (!crawlId) throw new Error("CrawlSEO run_crawl did not return a crawl ID");

  const deadline = Date.now() + plan.timeout_ms;
  let crawl;
  while (Date.now() < deadline) {
    crawl = await prisma.crawl.findUnique({
      where: { id: crawlId },
      select: {
        id: true,
        status: true,
        startedAt: true,
        finishedAt: true,
        pagesFound: true,
        issuesFound: true,
        healthScore: true,
        maxPages: true,
      },
    });
    if (crawl?.status === "COMPLETED" || crawl?.status === "FAILED") break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!crawl || crawl.status !== "COMPLETED") {
    throw new Error(`CrawlSEO crawl did not complete: ${crawl?.status ?? "missing"}`);
  }
  if (
    crawl.maxPages !== plan.max_pages
    || !Number.isInteger(crawl.pagesFound)
    || crawl.pagesFound <= 0
    || crawl.pagesFound > plan.max_pages
  ) {
    throw new Error(
      `CrawlSEO page cap violated: maxPages=${crawl.maxPages}, pagesFound=${crawl.pagesFound}`,
    );
  }

  const issues = await prisma.crawlIssue.findMany({
    where: { crawlId },
    orderBy: [{ type: "asc" }, { severity: "asc" }, { url: "asc" }, { message: "asc" }],
    select: { type: true, severity: true, url: true, message: true },
  });
  const searchPerformance = await readSearchPerformance(siteId);
  await writeJsonAtomic(outputPath, {
    schema: "extella.crawlseo_source.v1",
    source: "CrawlSEO",
    source_commit: "8683b2740eca5059faa0949c2175a7548216bd50",
    tool: "run_crawl",
    tool_calls: 1,
    requested_max_pages: plan.max_pages,
    coverage: {
      planned_pages: plan.max_pages,
      crawled_pages: crawl.pagesFound,
      sampled_pages: 0,
      categories: plan.categories,
    },
    site_id: siteId,
    site_url: normalizedSiteUrl,
    crawl,
    issues,
    search_performance: searchPerformance,
  });
  console.log(JSON.stringify({ crawlId, maxPages: plan.max_pages, pagesFound: crawl.pagesFound, issuesFound: issues.length }));
} finally {
  await client.close().catch(() => {});
  await prisma.$disconnect();
}
