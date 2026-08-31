#!/usr/bin/env node
/**
 * Export the pinned SEOmator catalog as stable JSON without adding a product dependency.
 *
 * It first invokes the upstream registry through Node's native TypeScript stripping. If the
 * pinned checkout cannot load because its optional runtime dependencies are unavailable, the
 * checked-in upstream rule reference is the deterministic fallback. That fallback is explicit
 * in the generated metadata and must be replaced by a registry export in a provisioned checkout.
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(here, '..');
const args = process.argv.slice(2);

function option(name, fallback) {
  const index = args.indexOf(name);
  return index === -1 ? fallback : resolve(args[index + 1]);
}

const sourceRoot = option('--source', resolve(projectRoot, '..', '..', 'work', 'seo-audit-skill'));
const outputPath = option('--output', resolve(projectRoot, 'experts', 'rule_catalog.v2.json'));
const dependencyRoot = resolve(process.env.SEOMATOR_DEPENDENCY_ROOT ?? sourceRoot);
const documentedRulesPath = resolve(sourceRoot, 'docs', 'SEO-AUDIT-RULES.md');
const categories = new Map([
  ['Core SEO', 'core'], ['Performance', 'perf'], ['Links', 'links'], ['Images', 'images'],
  ['Security', 'security'], ['Technical SEO', 'technical'], ['Crawlability', 'crawl'],
  ['Structured Data', 'schema'], ['Content', 'content'], ['JavaScript Rendering', 'js'],
  ['Accessibility', 'a11y'], ['Social', 'social'], ['E-E-A-T', 'eeat'], ['URL Structure', 'url'],
  ['Redirects', 'redirect'], ['Mobile', 'mobile'], ['Internationalization', 'i18n'],
  ['HTML Validation', 'htmlval'], ['AI/GEO Readiness', 'geo'], ['Legal Compliance', 'legal'],
]);
const profiles = ['service_b2b', 'ecommerce', 'local_business', 'content_media', 'saas_marketplace'];

function upstreamRevision() {
  return execFileSync('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
}

function registryRules() {
  const loaderUrl = pathToFileURL(resolve(sourceRoot, 'src', 'rules', 'loader.ts')).href;
  const registryUrl = pathToFileURL(resolve(sourceRoot, 'src', 'rules', 'registry.ts')).href;
  const program = [
    "import { registerHooks } from 'node:module';",
    `const cheerioUrl = ${JSON.stringify(pathToFileURL(resolve(dependencyRoot, 'node_modules', 'cheerio', 'dist', 'esm', 'index.js')).href)};`,
    "registerHooks({resolve(specifier, context, nextResolve) {",
    "  if (specifier === 'cheerio') return { url: cheerioUrl, format: 'module', shortCircuit: true };",
    '  try { return nextResolve(specifier, context); }',
    "  catch (error) { if (specifier.endsWith('.js')) return nextResolve(`${specifier.slice(0, -3)}.ts`, context); throw error; }",
    '}});',
    `const { loadAllRules } = await import(${JSON.stringify(loaderUrl)});`,
    `const { getAllRules } = await import(${JSON.stringify(registryUrl)});`,
    'await loadAllRules();',
    "process.stdout.write(JSON.stringify(getAllRules().map((rule) => ({id: rule.id, name: rule.name, description: rule.description, category: rule.category, weight: rule.weight}))));",
  ].join('\n');
  const raw = execFileSync(process.execPath, ['--experimental-strip-types', '--input-type=module', '--eval', program], {
    cwd: sourceRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed) || parsed.length !== 251) {
    throw new Error('upstream registry did not expose 251 rules');
  }
  return parsed;
}

function documentedRules() {
  if (!existsSync(documentedRulesPath)) {
    throw new Error(`upstream rule reference is missing: ${documentedRulesPath}`);
  }
  let category = null;
  const rules = [];
  for (const line of readFileSync(documentedRulesPath, 'utf8').split(/\r?\n/u)) {
    if (line.startsWith('## ')) {
      category = categories.get(line.slice(3).trim()) ?? null;
      continue;
    }
    if (!category || !line.startsWith('| `')) continue;
    const columns = line.split('|').map((part) => part.trim());
    const id = columns[1];
    const severity = columns[3];
    if (!id?.startsWith('`') || !id.endsWith('`') || !severity) continue;
    const sourceRule = id.slice(1, -1);
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)+$/u.test(sourceRule)) continue;
    rules.push({ id: sourceRule, category, severity });
  }
  if (rules.length !== 251 || new Set(rules.map((rule) => rule.category)).size !== 20) {
    throw new Error(`documented upstream export is incomplete: ${rules.length} rules`);
  }
  return rules;
}

function severityFor(value) {
  if (value.includes('fail')) return 'critical';
  if (value.includes('warn')) return 'warning';
  return 'info';
}

function catalogEntry(rule, statusById) {
  const legacyDescription = rule.id === 'core-description-present';
  const ruleKey = legacyDescription ? 'meta-description-missing' : rule.id;
  const sourceSeverity = statusById.get(rule.id);
  if (!sourceSeverity) throw new Error(`missing documented source severity for ${rule.id}`);
  return {
    rule_key: ruleKey,
    category: rule.category,
    severity: severityFor(sourceSeverity),
    source_name: rule.name,
    source_description: rule.description,
    source_severity: sourceSeverity,
    severity_policy: 'seomator-documentation-status-v1',
    confirmed_fact: legacyDescription ? 'На странице отсутствует meta description.' : null,
    remediation: legacyDescription ? 'Добавить точное meta description для страницы.' : null,
    actionable: legacyDescription,
    profiles,
    source_rules: legacyDescription
      ? { CrawlSEO: 'MISSING_DESCRIPTION', SEOmator: rule.id }
      : { SEOmator: rule.id },
    corroboration: legacyDescription ? { verified: [['CrawlSEO', 'SEOmator']] } : { verified: [] },
    verification: legacyDescription ? 'Повторить проверку и убедиться, что правило больше не срабатывает.' : null,
    version: '2.0.0',
  };
}

const revision = upstreamRevision();
const documented = documentedRules();
const statusById = new Map(documented.map((rule) => [rule.id, rule.severity]));
const exported = registryRules();
const rules = exported.map((rule) => catalogEntry(rule, statusById)).sort((left, right) => left.rule_key.localeCompare(right.rule_key));
const output = {
  schema: 'extella.seo_employee_rule_catalog.v2',
  catalog_version: '2.0.0',
  upstream: { repository: 'seo-skills/seo-audit-skill', revision, mode: 'registry', limitation: null },
  rules,
};
writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, 'utf8');
console.log(`wrote ${rules.length} rules across ${new Set(rules.map((rule) => rule.category)).size} categories (registry)`);
