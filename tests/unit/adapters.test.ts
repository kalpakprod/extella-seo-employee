import { describe, expect, it } from 'vitest';
import {
  assertRedirectAllowed,
  assertUrlAllowed,
  createInMemoryBrowserPort,
  createManualBrowserPort,
  createUnavailableBrowserPort,
  isForbiddenHost,
  UrlBlockedError,
} from '../../src/adapters/browser.js';
import {
  createScriptedExpertPort,
  createUnavailableExpertPort,
  withExpertTimeout,
} from '../../src/adapters/expert.js';
import { createInMemoryExtellaPort } from '../../src/adapters/extella.js';
import { escapeHtml, renderCandidateReportMarkdown } from '../../src/presentation/candidate-report.js';
import { makeValidReport } from '../fixtures/report.js';

describe('url policy', () => {
  it('blocks local, private, link-local and metadata hosts', () => {
    for (const host of [
      'localhost',
      'app.localhost',
      '127.0.0.1',
      '10.0.0.5',
      '172.16.1.1',
      '172.31.255.1',
      '192.168.1.1',
      '169.254.169.254',
      '100.64.0.1',
      'metadata.google.internal',
      'db.internal',
      'printer.local',
      '::1',
      'fd00::1',
      'fe80::1',
      'febf::1',
      'ff02::1',
      '::ffff:127.0.0.1',
      '::ffff:7f00:1',
    ]) {
      expect(isForbiddenHost(host), host).toBe(true);
    }
  });

  it('allows public hosts and addresses', () => {
    for (const host of ['example.com', 'hh.ru', '8.8.8.8', '93.184.216.34']) {
      expect(isForbiddenHost(host), host).toBe(false);
    }
  });

  it('only accepts http/https without embedded credentials', async () => {
    await expect(assertUrlAllowed('ftp://example.com/cv')).rejects.toBeInstanceOf(UrlBlockedError);
    await expect(assertUrlAllowed('file:///etc/passwd')).rejects.toBeInstanceOf(UrlBlockedError);
    await expect(assertUrlAllowed('https://user:pass@example.com/')).rejects.toBeInstanceOf(UrlBlockedError);
    await expect(assertUrlAllowed('not a url')).rejects.toBeInstanceOf(UrlBlockedError);
  });

  it('rejects a public hostname that resolves to a private address', async () => {
    await expect(
      assertUrlAllowed('https://example.com/cv', { resolve: () => Promise.resolve(['10.1.2.3']) }),
    ).rejects.toBeInstanceOf(UrlBlockedError);
    const allowed = await assertUrlAllowed('https://example.com/cv', {
      resolve: () => Promise.resolve(['93.184.216.34']),
    });
    expect(allowed.hostname).toBe('example.com');
  });

  it('fails closed when DNS does not resolve', async () => {
    await expect(
      assertUrlAllowed('https://nope.example/', { resolve: () => Promise.reject(new Error('ENOTFOUND')) }),
    ).rejects.toBeInstanceOf(UrlBlockedError);
    await expect(
      assertUrlAllowed('https://empty.example/', { resolve: () => Promise.resolve([]) }),
    ).rejects.toBeInstanceOf(UrlBlockedError);
  });

  it('keeps redirects on the same host', () => {
    expect(assertRedirectAllowed('https://hh.ru/a', 'https://hh.ru/b').hostname).toBe('hh.ru');
    expect(() => assertRedirectAllowed('https://hh.ru/a', 'https://evil.example/b')).toThrow(UrlBlockedError);
    expect(() => assertRedirectAllowed('https://hh.ru/a', 'http://127.0.0.1/b')).toThrow(UrlBlockedError);
    expect(() => assertRedirectAllowed('https://hh.ru/a', 'http://hh.ru/b')).toThrow(UrlBlockedError);
  });
});

describe('browser ports', () => {
  it('reads only pages it was given and reports missing ones', async () => {
    const port = createInMemoryBrowserPort([
      { url: 'https://hh.ru/vacancy/1', status: 'readable', title: 'Vacancy', text: 'Текст вакансии.' },
    ]);
    const readable = await port.readSubmittedPage({ url: 'https://hh.ru/vacancy/1' });
    expect(readable.status).toBe('readable');
    const missing = await port.readSubmittedPage({ url: 'https://hh.ru/vacancy/2' });
    expect(missing.status).toBe('unavailable');
    expect(await port.listOpenTabs()).toHaveLength(1);
  });

  it('surfaces a login requirement instead of guessing', async () => {
    const port = createManualBrowserPort('нужен вход HR');
    const result = await port.readSubmittedPage({ url: 'https://hh.ru/private' });
    expect(result).toMatchObject({ status: 'needs_login' });
  });

  it('reports the browser as unavailable when it is not connected', async () => {
    const port = createUnavailableBrowserPort('платформа не подключена');
    const result = await port.readSubmittedPage({ url: 'https://hh.ru/x' });
    expect(result.status).toBe('unavailable');
  });
});

describe('expert port', () => {
  it('returns a typed integration error when no expert is available', async () => {
    const result = await createUnavailableExpertPort('нет контура').analyze({
      task: 'review',
      locale: 'ru-RU',
      instructions: '',
      documents: [],
      schema: {},
    });
    expect(result).toMatchObject({ ok: false, error: { code: 'integration_unavailable', recoverable: true } });
  });

  it('ends a hung provider with a typed timeout', async () => {
    const slow = createScriptedExpertPort(() => ({ ok: true, payload: {} }), { delayMs: 50 });
    const result = await withExpertTimeout(slow, 5).analyze({
      task: 'review',
      locale: 'ru-RU',
      instructions: '',
      documents: [],
      schema: {},
    });
    expect(result).toMatchObject({ ok: false, error: { code: 'model_timeout' } });
  });

  it('returns the scripted payload for the fast path', async () => {
    const port = createScriptedExpertPort(() => ({ ok: true, payload: { verdict: 'ok' } }));
    const result = await port.analyze({ task: 'review', locale: 'ru-RU', instructions: '', documents: [], schema: {} });
    expect(result).toEqual({ ok: true, payload: { verdict: 'ok' } });
  });
});

describe('extella port', () => {
  it('records progress, report and user actions', async () => {
    const port = createInMemoryExtellaPort();
    await port.publishProgress({ requestId: 'r1', status: 'analyzing', stage: 'matching', message: 'Сопоставление' });
    await port.requestUserAction('r1', { kind: 'login', url: 'https://hh.ru', instructions: 'Войдите' });
    await port.publishReport('r1', makeValidReport());
    expect(port.progress).toHaveLength(1);
    expect(port.actions[0]?.action.kind).toBe('login');
    expect(port.reports.get('r1')?.requestId).toBe('req-1');
  });
});

describe('presentation', () => {
  it('escapes untrusted text', () => {
    expect(escapeHtml('<script>alert("x")</script>')).toBe('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;');
  });

  it('renders a card without executing embedded markup', () => {
    const markdown = renderCandidateReportMarkdown(makeValidReport());
    expect(markdown).toContain('Вопросы для интервью');
    expect(markdown).toContain('Senior Backend Engineer');
    expect(markdown).toContain('Цитата');
    expect(markdown).toContain('res\\-1/res\\-1\\-b0');
    expect(markdown).not.toContain('<script>');
  });

  it('escapes HTML in every rendered header field', () => {
    const markdown = renderCandidateReportMarkdown(
      makeValidReport({
        requestId: '<img src=x onerror=alert(1)>',
        header: { name: '<script>x</script>', role: '<b>role</b>', verdict: '<svg/onload=alert(1)>' },
      }),
    );
    expect(markdown).not.toContain('<script>');
    expect(markdown).not.toContain('<svg');
    expect(markdown).not.toContain('<img');
    expect(markdown).toContain('&lt;script&gt;');
  });
});
