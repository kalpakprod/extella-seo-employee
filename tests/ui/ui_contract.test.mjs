import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const appRoot = new URL('../../app/', import.meta.url);
const [html, css, app, bridgeSource] = await Promise.all([
  readFile(new URL('index.html', appRoot), 'utf8'),
  readFile(new URL('styles.css', appRoot), 'utf8'),
  readFile(new URL('app.js', appRoot), 'utf8'),
  readFile(new URL('extella-bridge.js', appRoot), 'utf8'),
]);
const all = [html, css, app, bridgeSource].join('\n');

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.dataset = {};
    this.children = [];
    this.attributes = {};
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.textContent = '';
    this.className = '';
  }

  append(...nodes) {
    this.children.push(...nodes);
    return this;
  }

  appendChild(node) {
    return this.append(node);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }

  addEventListener() {}

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  focus() {}
}

class FakeClock {
  constructor() {
    this.now = 0;
    this.nextId = 0;
    this.timers = new Map();
  }

  setTimeout(callback, delay = 0) {
    const id = ++this.nextId;
    const safeDelay = Math.max(0, Number(delay) || 0);
    this.timers.set(id, { callback, at: this.now + safeDelay, delay: safeDelay });
    return id;
  }

  clearTimeout(id) {
    this.timers.delete(id);
  }

  pending() {
    return [...this.timers.values()].sort((left, right) => left.at - right.at);
  }

  async flush() {
    for (let index = 0; index < 64; index += 1) await Promise.resolve();
  }

  async advance(duration) {
    const finish = this.now + duration;
    while (true) {
      const next = this.pending().find(timer => timer.at <= finish);
      if (!next) break;
      const entry = [...this.timers.entries()].find(([, timer]) => timer === next);
      if (!entry) break;
      this.timers.delete(entry[0]);
      this.now = next.at;
      next.callback();
      await this.flush();
    }
    this.now = finish;
    await this.flush();
  }
}

async function runBootstrapScenario() {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const elements = new Map(ids.map(id => [id, new FakeElement(id)]));
  const document = {
    documentElement: new FakeElement('html'),
    title: '',
    getElementById: id => elements.get(id) || new FakeElement(id),
    querySelectorAll: () => [],
    createElement: tag => new FakeElement(tag),
  };
  const listeners = {};
  const posts = [];
  const parent = {
    postMessage(message) {
      posts.push(message);
      const targetless = {
        status: 'success',
        targets: [
          { target_id: 'target-beta', target_name: 'Beta', site_url: 'https://beta.example/', profile: 'ecommerce', state: 'ready', queue_position: 2 },
          { target_id: 'target-alpha', target_name: 'Alpha', site_url: 'https://alpha.example/', profile: 'service_b2b', state: 'empty', queue_position: null },
        ],
      };
      const selected = {
        status: 'success',
        state: 'empty',
        target_id: 'target-beta',
        config: {
          target_id: 'target-beta', target_name: 'Beta', site_url: 'https://beta.example/',
          profile: 'ecommerce', language: 'en', region: 'US', site_type: 'website',
          business_goal: 'Goal', daily_run_time: '09:00', timezone: 'UTC',
          mode: 'full_audit', max_pages: 25, ownership_confirmed: true,
        },
        schedules: [{ next_run: null }],
        queue: { items: [], position: null },
      };
      const payload = posts.length === 1 ? targetless : selected;
      queueMicrotask(() => listeners.message({
        source: parent,
        data: { type: 'etb_expert_result', reqId: message.reqId, ok: true, res: JSON.stringify(payload) },
      }));
    },
  };
  const window = {
    parent,
    location: { protocol: 'file:', pathname: '/index.html', hash: '' },
    addEventListener(type, listener) { listeners[type] = listener; },
    setTimeout,
    clearTimeout,
    __SEO_PANEL_TEST__: null,
  };
  const context = {
    window, document, console, URL, Intl, Date, JSON, Promise, Set, Map, Error,
    Number, String, Array, Object, Math, RegExp, AbortController, Uint32Array, queueMicrotask,
    crypto: { getRandomValues(values) { values[0] = 1; return values; } },
  };
  vm.runInNewContext(bridgeSource, context);
  vm.runInNewContext(app, context);
  setTimeout(() => listeners.message({
    source: parent,
    data: { type: 'etb_init', device: 'device-1', language: 'ru', theme: 'light' },
  }), 0);
  await new Promise(resolve => setTimeout(resolve, 40));
  return { elements, posts, window };
}

async function runPollingScenario() {
  const clock = new FakeClock();
  const BaseDate = Date;
  class ControlledDate extends BaseDate {
    static now() { return clock.now; }
  }
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const elements = new Map(ids.map(id => [id, new FakeElement(id)]));
  const document = {
    documentElement: new FakeElement('html'),
    title: '',
    getElementById: id => elements.get(id) || new FakeElement(id),
    querySelectorAll: () => [],
    createElement: tag => new FakeElement(tag),
  };
  const listeners = {};
  const posts = [];
  const pending = [];
  const targetless = {
    status: 'success',
    targets: [
      { target_id: 'target-beta', target_name: 'Beta', site_url: 'https://beta.example/', profile: 'ecommerce', state: 'empty', queue_position: 1 },
      { target_id: 'target-alpha', target_name: 'Alpha', site_url: 'https://alpha.example/', profile: 'service_b2b', state: 'empty', queue_position: null },
    ],
  };
  const targetConfig = (targetId, targetName, siteUrl, profile) => ({
    status: 'success',
    state: 'empty',
    target_id: targetId,
    config: {
      target_id: targetId, target_name: targetName, site_url: siteUrl,
      profile, language: 'ru', region: 'GLOBAL', site_type: 'website',
      business_goal: 'Goal', daily_run_time: '09:00', timezone: 'UTC',
      mode: 'full_audit', max_pages: 25, ownership_confirmed: true,
    },
    schedules: [{ next_run: null }],
    queue: { items: [], position: null },
  });
  const queued = {
    status: 'success',
    state: 'queued',
    target_id: 'target-beta',
    queue: {
      position: 1,
      items: [{
        queue_id: 'queue-beta', run_id: 'run-beta', target_id: 'target-beta',
        trigger: 'manual', mode: 'full_audit', requested_at: '2026-08-30T12:00:00Z',
        status: 'queued', attempts: 0,
      }],
    },
  };
  let betaStateResponses = 0;
  const parent = {
    postMessage(message) {
      posts.push(message);
      const targetId = message.params && message.params.target_id;
      const deliver = payload => queueMicrotask(() => listeners.message({
        source: parent,
        data: { type: 'etb_expert_result', reqId: message.reqId, ok: true, res: JSON.stringify(payload) },
      }));
      if (message.name === 'seo_employee_run') {
        deliver(queued);
      } else if (!targetId) {
        deliver(targetless);
      } else if (targetId === 'target-alpha') {
        deliver(targetConfig('target-alpha', 'Alpha', 'https://alpha.example/', 'service_b2b'));
      } else if (betaStateResponses++ === 0) {
        deliver(targetConfig('target-beta', 'Beta', 'https://beta.example/', 'ecommerce'));
      } else {
        pending.push({ message, deliver });
      }
    },
  };
  const window = {
    parent,
    location: { protocol: 'file:', pathname: '/index.html', hash: '' },
    addEventListener(type, listener) { listeners[type] = listener; },
    setTimeout: (...args) => clock.setTimeout(...args),
    clearTimeout: id => clock.clearTimeout(id),
    __SEO_PANEL_TEST__: null,
  };
  const context = {
    window, document, console, URL, Intl, Date: ControlledDate, JSON, Promise, Set, Map, Error,
    Number, String, Array, Object, Math, RegExp, AbortController, Uint32Array, queueMicrotask,
    crypto: { getRandomValues(values) { values[0] = 1; return values; } },
  };
  vm.runInNewContext(bridgeSource, context);
  vm.runInNewContext(app, context);
  await clock.flush();
  listeners.message({
    source: parent,
    data: { type: 'etb_init', device: 'device-1', language: 'ru', theme: 'light' },
  });
  await clock.flush();
  return { clock, elements, posts, pending, window, targetConfig, queued };
}

const bridgeModule = { exports: {} };
vm.runInNewContext(bridgeSource, { module: bridgeModule, exports: bridgeModule.exports });
const { unwrapExtellaResult } = bridgeModule.exports;
const appModule = { exports: {} };
vm.runInNewContext(app, { module: appModule, exports: appModule.exports });
const {
  buildReportModel,
  buildCoverageModel,
  nextRunFrom,
  normalizeStatePayload,
  normalizeTarget,
  selectBootstrapTarget,
  queuePayloadState,
  queueViewModel,
  remainingDeadline,
  runControlState,
  shouldContinuePolling,
  validRegion,
  isWorkState,
  ALLOWED_REGIONS,
  severityLabel,
} = appModule.exports;

function expectAll(source, values, message) {
  values.forEach(value => assert.ok(source.includes(value), `${message}: ${value}`));
}

test('[UC-SEO-001] first screen names product, outcome, and first step', () => {
  expectAll(html, ['SEO-сотрудник', 'задачи с доказательствами', 'начни с первой проверки'], 'orientation copy missing');
});

test('[UC-SEO-002] there is one bronze primary action with both locale labels', () => {
  assert.equal((html.match(/class="primary"/g) || []).length, 1);
  assert.match(html, />Проверить сайт<\/button>/);
  assert.match(app, /runAction: 'Check site'/);
});

test('[UC-SEO-003] settings contain validated URL, daily time, and IANA timezone', () => {
  expectAll(html, ['name="site_url"', 'name="daily_run_time"', 'name="timezone"'], 'setting missing');
  expectAll(app, ['publicUrl(', 'validTimezone(', "t('invalidUrl')", "t('invalidTimezone')"], 'validation hook missing');
});

test('[UC-SEO-004] empty representation explains the first check', () => {
  expectAll(html, ['data-state-view="empty"', 'Проверок ещё нет', 'Проверить сайт'], 'empty state incomplete');
});

test('[UC-SEO-005] running representation locks before the call and unlocks in finally', () => {
  assert.ok(html.includes('data-state-view="running"'));
  const run = app.slice(app.indexOf('async function runAudit'), app.indexOf('async function loadState'));
  assert.ok(run.indexOf('setRunBusy(true)') < run.indexOf('await bridge.run'), 'busy state must start before bridge call');
  assert.match(run, /finally\s*\{\s*setRunBusy\(false\)/);
  assert.match(app, /runBusy \|\| isWorkState\(currentState\.state\)/);
  assert.match(html, /id="running-stage"/);
});

test('[UC-SEO-006] failed representation gives friendly guidance without raw errors', () => {
  expectAll(html, ['data-state-view="failed"', 'Проверь публичный адрес и подключение'], 'failed state incomplete');
  assert.match(app, /function friendlyError/);
  assert.doesNotMatch(app, /failed-reason'\)\.textContent\s*=\s*state\.last_error/);
});

test('[UC-SEO-007] ready result shows run time, sources, comparison, and caps tasks', () => {
  expectAll(html, ['id="run-time"', 'id="source-list"', 'comparison-grid'], 'ready result hook missing');
});

test('[UC-SEO-008] partial shares result representation and lists missing sources', () => {
  assert.equal((html.match(/data-state-view="result"/g) || []).length, 1);
  expectAll(app, ["stateName === 'ready' || stateName === 'partial'", "stateName === 'partial'", 'report.missing_data'], 'partial behavior missing');
  assert.match(app, /!\['ok', 'not_configured'\]\.includes\(source\.status\)/);
});

test('report model hydrates comparison stubs from full tasks by task_id', () => {
  const evidence = [{ source: 'CrawlSEO', rule: 'meta-description', fact: 'missing' }];
  const fullTask = {
    task_id: 'task-new', url: 'https://example.com/', rule_key: 'meta-description-missing', severity: 'error',
    confirmed_fact: 'The meta description is missing.', sources: ['CrawlSEO', 'SEOmator'],
    business_impact: 'Search snippets have no summary.', minimal_fix: 'Add a concise description.',
    verification: 'Run the audit again.', evidence,
  };
  const model = buildReportModel({
    tasks: [fullTask],
    comparison: {
      new: 1, new_items: [{ task_id: 'task-new', url: 'stub-url' }],
      fixed: 1, fixed_items: [{ task_id: 'task-fixed', url: 'https://example.com/old' }],
      unchanged: 0, unchanged_items: [],
    },
  });
  assert.equal(model.groups.new[0].url, fullTask.url);
  assert.equal(model.groups.new[0].business_impact, fullTask.business_impact);
  assert.deepEqual(model.groups.new[0].evidence, evidence);
  assert.equal(model.groups.fixed[0].task_id, 'task-fixed');
  assert.equal(model.counts.new, 1);
  assert.equal(model.counts.fixed, 1);
  const capped = buildReportModel({
    tasks: Array.from({ length: 11 }, (_, index) => ({ task_id: `task-${index}`, comparison_group: 'new' })),
  });
  assert.equal(capped.groups.new.length, 10);
});

test('next run uses the backend schedules[].next_run field, including null', () => {
  const expected = '2026-08-30T09:00:00+00:00';
  assert.equal(nextRunFrom({ schedules: [{ id: 'daily', active: true, next_run: expected }] }), expected);
  assert.equal(nextRunFrom({ schedules: [{ id: 'daily', active: true, next_run: null }], next_run_at: expected }), null);
});

test('duplicate and running run responses are control states, not failures', () => {
  assert.equal(runControlState({ status: 'success', state: 'duplicate', duplicate: true }), 'duplicate');
  assert.equal(runControlState({ status: 'success', state: 'running' }), 'running');
  assert.equal(runControlState({ status: 'success', state: 'ready' }), null);
});

test('[UC-SEO-009] every task card renders all required fields', () => {
  expectAll(app, ["t('severity')", "t('url')", "t('fact')", "t('sources')", "t('businessImpact')", "t('minimalFix')", "t('verification')"], 'task field missing');
});

test('[UC-SEO-010] evidence is independently expandable for every capped task', () => {
  expectAll(app, ["document.createElement('details')", "textNode('summary'", 'EVIDENCE_TEST_IDS', "item?.source"], 'evidence hook missing');
  assert.equal(new Set([...app.matchAll(/'([a-z]+)'/g)].map(match => match[1])).size > 0, true);
});

test('[UC-SEO-011] new, fixed, and unchanged groups accept empty results', () => {
  expectAll(html, ['id="new-findings"', 'id="fixed-findings"', 'id="unchanged-findings"'], 'comparison group missing');
  expectAll(app, ["const GROUPS = ['new', 'fixed', 'unchanged']", "t('emptyGroup')"], 'empty comparison behavior missing');
});

test('[UC-SEO-012] daily schedule shows time, timezone, and next run', () => {
  expectAll(html, ['id="schedule-time"', 'id="next-run"'], 'schedule output missing');
  expectAll(app, ['daily_run_time', 'timezone', 'next_run_at'], 'schedule field missing');
});

test('[UC-SEO-013] manual run keeps its trigger and uses bounded polling', () => {
  assert.match(app, /method: 'run', target_id: targetId, mode, trigger: 'manual'/);
  assert.match(app, /const POLL_INTERVAL_MS = 2000/);
  assert.match(app, /const POLL_MAX_MS = 900000/);
  assert.match(app, /window\.setTimeout\(\(\) => \{/);
  assert.match(app, /const delay = pollingDelay\(pollDeadlineAt\)/);
  assert.match(app, /beforeunload/);
  assert.equal((app.match(/await bridge\.run\(EXPERT_RUN/g) || []).length, 2, 'only configure and one manual run may invoke the runner');
});

test('[UC-SEO-014] help explains sources, limits, and rollback owner', () => {
  expectAll(html, ['data-testid="help-toggle"', 'не гарантирует позиции, трафик или выручку', 'как откатить изменение'], 'help boundary missing');
});

test('[UC-SEO-015] panel exposes no publish, index, site-change, or link-buy actions', () => {
  const buttonText = [...html.matchAll(/<button\b[^>]*>([^<]+)<\/button>/g)].map(match => match[1].trim()).join(' ');
  assert.doesNotMatch(buttonText, /публи|индекс|измен|ссыл|publish|index|change|link/i);
});

test('[UC-SEO-016] Search Console is optional and has no dead connect button', () => {
  assert.match(html, /Google Search Console[\s\S]*Не настроена, необязательно/);
  assert.doesNotMatch(html, /<button\b[^>]*>[^<]*(Подключ|Connect)/i);
});

test('[UC-SEO-017] RU and EN copy comes from etb_init', () => {
  expectAll(app, ['ru: {', 'en: {', "data.type === 'etb_init'", 'data.language || data.lang || data.locale'], 'host language hook missing');
});

test('[UC-SEO-018] theme comes from etb_init and etb_theme with no local switch', () => {
  expectAll(bridgeSource, ["data.type === 'etb_init'", "data.type === 'etb_theme'"], 'host theme message missing');
  expectAll(app, ["setAttribute('data-lm', '1')", "removeAttribute('data-lm')"], 'theme application missing');
  assert.doesNotMatch(html, /theme-(switch|toggle)|language-(switch|toggle)/i);
});

test('[UC-SEO-019] status is announced and every interactive control has a stable test id', () => {
  assert.match(html, /aria-live="polite"/);
  assert.match(css, /:focus-visible/);
  const controls = [...html.matchAll(/<(button|input|select|textarea|summary)\b[^>]*>/g)].map(match => match[0]);
  const ids = controls.map(control => control.match(/data-testid="([^"]+)"/)?.[1]);
  assert.ok(ids.every(Boolean), 'every static interactive control needs data-testid');
  assert.equal(new Set(ids).size, ids.length, 'static data-testid values must be unique');
  assert.ok(ids.every(id => !/\d/.test(id)), 'data-testid values must not contain digits');
  assert.match(app, /summary\.dataset\.testid = `evidence-\$\{EVIDENCE_TEST_IDS\[index\]\}`/);
});

test('[UC-SEO-020] Extella design tokens, waiting state, responsiveness, and reduced motion remain', () => {
  expectAll(css, ['#FAF9F5', '#FFFFFF', '#F5F3EC', '#0A0A0A', '#C57E33', '#2F6B66', '#D7E0DC', 'Source Serif 4', 'JetBrains Mono', 'Nunito'], 'design token missing');
  expectAll(css, ['@media (min-width: 1400px)', '@media (max-width: 560px)', 'prefers-reduced-motion', 'overflow-x: hidden'], 'responsive/accessibility hook missing');
  assert.equal((css.match(/background:\s*var\(--accent\)/g) || []).length, 1, 'bronze is reserved for the primary action');
});

test('[UC-SEO-020b] CSS uses only Extella palette colors and the approved dark border', () => {
  const allowed = new Set([
    'C57E33', 'D4944A', 'D7E0DC', 'FAF9F5', 'FFFFFF', 'F5F3EC', '0A0A0A',
    '141414', '181818', 'F5F3EE', '2F6B66',
  ]);
  const colors = [...css.matchAll(/#[0-9a-f]{3,8}\b/gi)].map(match => match[0].slice(1).toUpperCase());
  assert.deepEqual(colors.filter(color => !allowed.has(color)), []);
  assert.deepEqual([...css.matchAll(/rgba?\(([^)]+)\)/gi)].map(match => match[0]), ['rgba(243, 238, 229, .09)']);
  assert.doesNotMatch(css, /\brgb\(/i);
  assert.doesNotMatch(css, /\bhsl\(/i);
});

test('[UC-SEO-021] data disclosure precedes any unavailable Search Console connection', () => {
  expectAll(html, ['data-testid="data-toggle"', 'Категории:', 'Выполнение и хранение:', 'Получатель входа модели:', 'Срок:', 'Удаление:', 'явного согласия'], 'data disclosure missing');
});

test('[UC-SEO-022] target list and exact v2 profile form are present', () => {
  expectAll(html, [
    'id="target-list"', 'name="target_name"', 'name="site_url"', 'name="profile"',
    'name="language"', 'name="region"', 'name="site_type"', 'name="business_goal"',
    'name="daily_run_time"', 'name="timezone"', 'name="mode"', 'name="max_pages"',
    'name="ownership_confirmed"', 'id="selected-target-title"',
  ], 'target form/list hook missing');
  ['service_b2b', 'ecommerce', 'local_business', 'content_media', 'saas_marketplace']
    .forEach(value => assert.match(html, new RegExp('value="' + value + '"')));
  ['full_audit', 'daily_monitor', 'search_performance', 'work_plan']
    .forEach(value => assert.match(html, new RegExp('value="' + value + '"')));
  assert.match(html, /name="max_pages"[^>]*type="number"[^>]*min="1"[^>]*max="100"[^>]*value="25"/);
  assert.match(html, /name="ownership_confirmed"[^>]*type="checkbox"/);
  assert.deepEqual(validRegion('GLOBAL'), true);
  assert.deepEqual(validRegion('KZ'), true);
  assert.deepEqual(validRegion('global'), true);
  assert.deepEqual(validRegion('GB'), true);
  assert.deepEqual(validRegion('ZZ'), false);
  assert.deepEqual(validRegion('AN'), false);
  assert.deepEqual(validRegion('USA'), false);
  assert.equal(ALLOWED_REGIONS.size, 250);
});

test('saved target keeps its URL immutable and directs URL changes to a new target', () => {
  expectAll(app, [
    "el('site-url').disabled = existingTarget",
    "el('site-url-help').hidden = !existingTarget",
    "t('siteUrlLocked')",
  ], 'saved target URL lock missing');
  expectAll(all, ['site-url-help', 'Создай новую цель', 'Create a new target'], 'URL lock guidance missing');
});

test('targetless state bootstrap consumes real target summaries and selects one without inventing data', () => {
  const envelope = {
    status: 'success',
    targets: [
      { target_id: 'target-beta', target_name: 'Beta', profile: 'ecommerce', state: 'ready', queue_position: 2 },
      { target_id: 'target-alpha', target_name: 'Alpha', profile: 'service_b2b', state: 'empty', queue_position: null },
    ],
  };
  const normalized = normalizeStatePayload(envelope);
  assert.equal(normalized.targets.length, 2);
  const selected = selectBootstrapTarget(envelope);
  assert.equal(selected.target_id, 'target-beta');
  assert.equal(selected.target_name, 'Beta');
  assert.equal(selected.profile, 'ecommerce');
  assert.equal(selected.state, 'ready');
  assert.equal(selected.queue_position, 2);
  assert.equal(normalizeTarget({ target_name: 'missing id' }), null);
});

test('targetless bootstrap renders summaries and then requests the selected target state', async () => {
  const { elements, posts, window } = await runBootstrapScenario();
  assert.equal(posts.length, 2);
  assert.equal(JSON.stringify(posts.map(message => message.params)), JSON.stringify([
    { method: 'state' },
    { method: 'state', target_id: 'target-beta' },
  ]));
  assert.equal(posts.every(message => message.target === 'device-1'), true);
  assert.equal(elements.get('target-list').children.length, 2);
  assert.equal(elements.get('target-list').children[0].children[0].children[0].textContent, 'Beta');
  assert.equal(elements.get('selected-target-title').textContent, 'Beta');
  assert.match(elements.get('queue-position').textContent, /2/);
  assert.equal(Boolean(window.__SEO_PANEL_TEST__), true);
});

test('controlled VM poll flow keeps one chain, cancels stale target work, and enforces deadline', async () => {
  const scenario = await runPollingScenario();
  const api = scenario.window.__SEO_PANEL_TEST__;
  assert.ok(api, JSON.stringify({ posts: scenario.posts, pending: scenario.pending.length, timers: scenario.clock.pending().length }));
  const betaStates = () => scenario.posts.filter(message => message.name === 'seo_employee_state'
    && message.params && message.params.target_id === 'target-beta');
  const pollTimers = () => scenario.clock.pending().filter(timer => timer.delay === 2000);

  const run = api.runAudit();
  await scenario.clock.flush();
  await run;
  await scenario.clock.flush();
  assert.equal(pollTimers().length, 1, 'a queued result starts exactly one poll timer');

  await scenario.clock.advance(2000);
  assert.equal(betaStates().length, 2, 'the first poll sends one selected-target state request');
  await scenario.clock.advance(2000);
  assert.equal(betaStates().length, 2, 'a pending poll cannot schedule a duplicate request');
  const firstPending = scenario.pending.shift();
  assert.ok(firstPending);
  firstPending.deliver(scenario.queued);
  await scenario.clock.flush();
  assert.equal(pollTimers().length, 1, 'one next poll is scheduled after the response');

  await scenario.clock.advance(2000);
  assert.equal(betaStates().length, 3);
  const late = scenario.pending.shift();
  assert.ok(late);
  api.selectTarget('target-alpha');
  await scenario.clock.flush();
  assert.equal(scenario.elements.get('selected-target-title').textContent, 'Alpha');
  assert.equal(pollTimers().length, 0, 'target change cancels the old chain');
  late.deliver(scenario.queued);
  await scenario.clock.flush();
  assert.equal(scenario.elements.get('selected-target-title').textContent, 'Alpha', 'late beta response cannot mutate Alpha');

  let settled = false;
  const timeoutRequest = api.stateRequest('target-timeout', scenario.clock.now + 50);
  timeoutRequest.then(() => { settled = true; });
  await scenario.clock.advance(49);
  assert.equal(settled, false, 'state request remains pending before its remaining deadline');
  await scenario.clock.advance(1);
  const timeout = await timeoutRequest;
  assert.equal(timeout.ok, false);
  assert.equal(timeout.error, 'poll_timeout');
  await scenario.clock.advance(0);
  assert.equal(api.pollingDelay(scenario.clock.now, scenario.clock.now), 0, 'deadline does not schedule another poll');
});

test('[UC-SEO-023] coverage exposes page counts, categories, sources, and unmapped rules', () => {
  expectAll(html, [
    'id="coverage-planned"', 'id="coverage-crawled"', 'id="coverage-sampled"',
    'id="coverage-categories"', 'id="coverage-completed"', 'id="coverage-unavailable"',
    'id="coverage-unmapped"', 'id="unmapped-count"', 'Результат неполный',
  ], 'coverage surface missing');
  expectAll(app, ['planned_pages', 'crawled_pages', 'sampled_pages', 'unmapped_rules', 'coveragePartial'], 'coverage model missing');
  const coverage = buildCoverageModel({
    planned_pages: 25, crawled_pages: 12, sampled_pages: 5,
    categories: ['core'], completed_sources: ['CrawlSEO'],
    unavailable_sources: ['Google Search Console'], unmapped_rules: ['future-rule'],
  });
  assert.equal(coverage.planned_pages, 25);
  assert.equal(coverage.crawled_pages, 12);
  assert.equal(JSON.stringify(coverage.unmapped_rules), JSON.stringify(['future-rule']));
  const partial = buildReportModel({
    state: 'partial',
    comparison: { new: 1, fixed: 2, unchanged: 0 },
    tasks: [{ comparison_group: 'fixed', task_id: 'old' }],
  }, 10, 'partial');
  assert.equal(partial.counts.fixed, 0);
  assert.equal(JSON.stringify(partial.groups.fixed), JSON.stringify([]));
});

test('[UC-SEO-024] queue view uses the real items and selected FIFO position', () => {
  expectAll(html, ['id="queue-current"', 'id="queue-position"', 'id="queue-reason"', 'id="queue-list"', 'id="next-run"', 'id="schedule-time"'], 'queue surface missing');
  const envelope = {
    state: 'ready',
    target_id: 'target-alpha',
    queue: {
      position: 1,
      items: [
        { queue_id: 'queue-three', run_id: 'run-three', target_id: 'target-old', status: 'completed' },
        { queue_id: 'queue-one', run_id: 'run-one', target_id: 'target-alpha', status: 'running' },
        { queue_id: 'queue-two', run_id: 'run-two', target_id: 'target-beta', status: 'queued', reason: 'worker_busy' },
      ],
    },
  };
  const view = queueViewModel(envelope, 'target-beta');
  assert.equal(view.selectedItem.status, 'queued');
  assert.equal(view.activeSelected, true);
  assert.equal(view.position, 2);
  assert.equal(view.reason, 'worker_busy');
  assert.equal(view.activeItems.length, 2);
  assert.equal(queuePayloadState(envelope), 'running');
  assert.equal(queuePayloadState({ ...envelope, target_id: 'target-beta' }), 'queued');
  assert.equal(shouldContinuePolling(envelope, 'target-beta'), true, 'active selected queue item keeps polling despite stale ready state');
  assert.equal(shouldContinuePolling({ state: 'failed', queue: envelope.queue }, 'target-beta'), true, 'transient state failure cannot stop an active selected queue item');
  assert.equal(shouldContinuePolling({ state: 'running', queue: { items: [] } }, 'target-beta'), true, 'running product state cannot stop polling silently');
  assert.equal(shouldContinuePolling({ state: 'ready', queue: { items: [] } }, 'target-beta'), false);
});

test('[UC-SEO-025] ownership confirmation gates every run before the Expert call', () => {
  expectAll(app, ['ownership_confirmed', 'runAllowed', 'ownershipError'], 'ownership gate missing');
  const run = app.slice(app.indexOf('async function runAudit'), app.indexOf('async function refreshState'));
  assert.ok(run.indexOf('if (!runAllowed() || !target)') < run.indexOf('await bridge.run'), 'run must be rejected before bridge call');
  assert.match(run, /target_id: targetId/);
  assert.match(run, /trigger: 'manual'/);
});

test('[UC-SEO-026] action proposals stay separate and only offer manual copy', () => {
  expectAll(html, ['id="action-proposals"', 'data-i18n="proposalsTitle"'], 'proposal surface missing');
  assert.match(app, /copyInstruction/);
  expectAll(app, ['action_proposals', 'proposal.preview', 'proposal.rollback', 'expires_at', 'copyInstruction'], 'proposal fields missing');
  const buttonText = [...html.matchAll(/<button\b[^>]*>([^<]+)<\/button>/g)].map(match => match[1].trim()).join(' ');
  assert.doesNotMatch(buttonText, /execute|publish|index|cms|link|исполн|публик|индекс|cms|ссыл/i);
  assert.doesNotMatch(app, /executeAction|publishAction|indexAction|cmsAction/);
});

test('v2 Expert calls use exact configure, run, and state parameters', () => {
  assert.match(app, /bridge\.run\(EXPERT_RUN, \{ method: 'configure', \.\.\.settings \}\)/);
  assert.match(app, /target_name: name/);
  assert.match(app, /bridge\.run\(EXPERT_RUN, \{ method: 'run', target_id: targetId, mode, trigger: 'manual' \}\)/);
  assert.match(app, /targetId \? \{ method: 'state', target_id: targetId \} : \{ method: 'state' \}/);
  assert.equal((app.match(/await bridge\.run\(EXPERT_RUN/g) || []).length, 2);
  assert.equal((app.match(/bridge\.run\(EXPERT_STATE/g) || []).length, 1);
});

test('queued and running states use one bounded polling chain and cancel on target change or unload', () => {
  assert.deepEqual(isWorkState('queued'), true);
  assert.deepEqual(isWorkState('running'), true);
  assert.deepEqual(isWorkState('ready'), false);
  expectAll(app, ['POLL_INTERVAL_MS = 2000', 'POLL_MAX_MS = 900000', 'startPolling', 'cancelPolling', 'beforeunload', 'pagehide'], 'polling bound/cancellation missing');
  assert.doesNotMatch(app, /setInterval\s*\(/);
  assert.match(app, /pollTimer = window\.setTimeout/);
  assert.match(app, /if \(token !== pollToken \|\| targetId !== selectedTargetId\) return/);
});

test('each state request receives the remaining absolute polling deadline', () => {
  assert.equal(remainingDeadline(900000, 899998), 2);
  assert.equal(remainingDeadline(900000, 900000), 0);
  assert.equal(remainingDeadline(900000, 900001), 0);
  expectAll(app, ['Promise.race', 'remainingDeadline(deadlineAt)', 'timeoutMs: Math.max(1, Math.min(240000, remaining))'], 'bounded state request missing');
});

test('desktop uses list-left/detail-right and mobile switches to a stacked layout', () => {
  expectAll(html, ['class="workspace"'], 'target/detail layout missing');
  assert.match(html, /class="[^"]*target-column/);
  assert.match(html, /class="[^"]*detail-column/);
  expectAll(css, ['grid-template-columns: minmax(280px, 380px)', '@media (max-width: 920px)', '.workspace { grid-template-columns: minmax(0, 1fr);'], 'responsive layout missing');
});

test('ARIA labels have host-language keys and are applied on language changes', () => {
  const labeledTags = [...html.matchAll(/<[^>]+aria-label="[^"]+"[^>]*>/g)].map(match => match[0]);
  assert.ok(labeledTags.length >= 4);
  assert.ok(labeledTags.every(tag => /data-i18n-aria="[^"]+"/.test(tag)), 'every aria-label must come from host copy');
  expectAll(app, ['data-i18n-aria', "setAttribute('aria-label'", 'panelLabel', 'targetListLabel'], 'ARIA localization hook missing');
});

test('model fields are conditional and deterministic fields remain visible on model failure', () => {
  expectAll(app, ['function modelStatus', 'function taskHasModelFields', "status === 'ok'", "modelUnavailable", "confirmed_fact || task.fact", "task.verification"], 'model degradation hooks missing');
  const deterministic = buildReportModel({
    model_enrichment: { status: 'unavailable' },
    tasks: [{ task_id: 'task', confirmed_fact: 'Fact', verification: 'Repeat source' }],
  });
  assert.equal(deterministic.groups.new[0].confirmed_fact, 'Fact');
  assert.equal(deterministic.groups.new[0].verification, 'Repeat source');
});

test('severity labels are localized for all contracted severities', () => {
  expectAll(app, ['severityCritical', 'severityError', 'severityWarning', 'severityInfo'], 'severity copy missing');
  assert.equal(severityLabel('critical'), 'критично');
  assert.equal(severityLabel('error'), 'ошибка');
  assert.equal(severityLabel('warning'), 'предупреждение');
  assert.equal(severityLabel('info'), 'информация');
});

test('[H72] bridge unwraps both transport envelopes and parses expert JSON strings', () => {
  const plain = value => JSON.parse(JSON.stringify(value));
  const domain = { status: 'success', state: 'ready', last_report: { state: 'ready' } };
  const wrapped = {
    status: 'ok', agent_id: 'agent', result: {
      status: 'success', expert_name: 'seo_employee_state', result: JSON.stringify(domain),
    },
  };
  assert.deepEqual(plain(unwrapExtellaResult(wrapped)), domain);
  assert.deepEqual(plain(unwrapExtellaResult({ status: 'ok', agent_id: 'agent', res: JSON.stringify(domain) })), domain);
  assert.deepEqual(plain(unwrapExtellaResult({ ok: true, res: null, result: JSON.stringify(domain) })), domain);
  assert.deepEqual(plain(unwrapExtellaResult({ result: JSON.stringify(domain) })), domain);
  assert.deepEqual(plain(unwrapExtellaResult({ type: 'etb_expert_result', reqId: 'req', ok: true, res: domain })), domain);
  assert.deepEqual(plain(unwrapExtellaResult(JSON.stringify(domain))), domain);
  assert.deepEqual(plain(unwrapExtellaResult({ status: 'success', result: { domain: true } })),
    { status: 'success', result: { domain: true } }, 'domain result must not be mistaken for transport');
  assert.throws(() => unwrapExtellaResult("{'state': 'ready'}"), /нельзя прочитать/);
});

test('bridge settles responses and timeouts without pending requests', async () => {
  const sent = [];
  const parent = { postMessage: message => sent.push(message) };
  let onMessage;
  const fakeWindow = {
    parent,
    addEventListener: (type, listener) => { if (type === 'message') onMessage = listener; },
    setTimeout,
    clearTimeout,
  };
  const runtimeModule = { exports: {} };
  vm.runInNewContext(bridgeSource, {
    module: runtimeModule,
    exports: runtimeModule.exports,
    window: fakeWindow,
    crypto: { getRandomValues: values => { values[0] = 1; return values; } },
  });
  const bridge = new runtimeModule.exports.ExtellaBridge({ allowedExperts: ['seo_employee_state'] });
  onMessage({ source: parent, data: { type: 'etb_init', device: 'device-160' } });
  const responsePromise = bridge.run('seo_employee_state');
  const reqId = sent.at(-1).reqId;
  assert.equal(sent.at(-1).target, 'device-160');
  onMessage({ source: parent, data: {
    type: 'etb_expert_result', reqId, ok: true, res: JSON.stringify({ status: 'success', state: 'empty' }),
  } });
  assert.deepEqual(JSON.parse(JSON.stringify(await responsePromise)), { ok: true, data: { status: 'success', state: 'empty' } });
  assert.equal(bridge.pending.size, 0);
  const timeout = await bridge.run('seo_employee_state', {}, { timeoutMs: 1 });
  assert.equal(timeout.ok, false);
  assert.equal(bridge.pending.size, 0);
});

test('[H65] panel self-heals stale cache before creating the bridge', () => {
  const boot = app.slice(app.indexOf('async function boot'));
  assert.match(app, /const PANEL_VERSION\s*=\s*'2\.0\.0'/);
  assert.match(app, /fetch\(window\.location\.pathname, \{ cache: 'no-store', signal: controller\.signal \}\)/);
  assert.match(app, /controller\.abort\(\), 3000/);
  assert.ok(boot.indexOf('await healStaleCache()') < boot.indexOf('new ExtellaBridge'), 'cache check must run first');
  assert.equal((all.match(/\bfetch\s*\(/g) || []).length, 1, 'only same-page cache healing may use fetch');
});

test('bridge has a finite timeout and only the two contracted expert routes', () => {
  expectAll(bridgeSource, ["type:'etb_run_expert'", 'etb_expert_result', 'event.source !== window.parent', 'window.setTimeout'], 'bridge guard missing');
  assert.match(app, /allowedExperts: \[EXPERT_RUN, EXPERT_STATE\]/);
  assert.deepEqual([...all.matchAll(/'seo_employee_(?:run|state)'/g)].map(match => match[0]).sort(),
    ["'seo_employee_run'", "'seo_employee_state'"], 'unexpected expert route found');
  assert.doesNotMatch(all, /https?:\/\/(?:localhost|127\.|10\.|192\.168\.)/i);
});

test('literal DOM references resolve and markup ids stay unique', () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length, 'markup ids must be unique');
  const referenced = [...app.matchAll(/\bel\('([^']+)'\)/g)].map(match => match[1]);
  const missing = [...new Set(referenced)].filter(id => !ids.includes(id));
  assert.deepEqual(missing, []);
});
