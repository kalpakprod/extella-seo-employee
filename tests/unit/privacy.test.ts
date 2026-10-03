import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  MODEL_INPUT_NOTICE,
  redactForModel,
  redactSourcesForModel,
} from '../../src/privacy/redact.js';
import { createTemporaryInputStore } from '../../src/privacy/temporary-inputs.js';
import { SYNTHETIC_SOURCES, makeDocument } from '../fixtures/synthetic.js';

describe('redactForModel', () => {
  it('replaces direct identifiers with stable labels', () => {
    const result = redactForModel(
      'Иван Петров, ivan.petrov@example.com, +7 (999) 123-45-67, telegram @ivan_petrov, https://github.com/ivanpetrov',
    );
    expect(result.text).toContain('[почта]');
    expect(result.text).toContain('[телефон]');
    expect(result.text).toContain('[мессенджер]');
    expect(result.text).toContain('[ссылка на профиль]');
    expect(result.text).not.toContain('ivan.petrov@example.com');
    expect(result.text).not.toContain('999');
    expect(result.total).toBeGreaterThanOrEqual(4);
    expect(result.counts.map((count) => count.kind)).toContain('email');
  });

  it('removes government identifiers', () => {
    const result = redactForModel('СНИЛС 123-456-789 00, ИНН 7707083893');
    expect(result.text).toContain('[гос. номер]');
    expect(result.text).not.toContain('7707083893');
  });

  it('is deterministic and idempotent for the same text', () => {
    const text = 'Почта: a@b.ru';
    expect(redactForModel(text)).toEqual(redactForModel(text));
    expect(redactForModel(redactForModel(text).text).total).toBe(0);
  });

  it('leaves ordinary professional facts untouched', () => {
    const result = redactForModel('Опыт backend 10 лет, Node.js, TypeScript, зарплата 320 000 руб.');
    expect(result.total).toBe(0);
    expect(result.text).toBe('Опыт backend 10 лет, Node.js, TypeScript, зарплата 320 000 руб.');
  });
});

describe('redactSourcesForModel', () => {
  it('returns minimized copies and never claims full anonymisation', () => {
    const result = redactSourcesForModel(SYNTHETIC_SOURCES);
    expect(result.documents).toHaveLength(SYNTHETIC_SOURCES.length);
    expect(result.notice).toBe(MODEL_INPUT_NOTICE);
    expect(result.notice).toMatch(/не гарантия/i);
    const originalText = SYNTHETIC_SOURCES.flatMap((source) => source.textBlocks.map((block) => block.text)).join('\n');
    const redactedText = result.documents.flatMap((source) => source.textBlocks.map((block) => block.text)).join('\n');
    expect(redactedText.length).toBeLessThanOrEqual(originalText.length);
  });

  it('keeps the evidence sources untouched', () => {
    const sources = [
      makeDocument({
        id: 'res-contact',
        inputKind: 'resume',
        displayName: 'resume.txt',
        text: 'Иван Петров, ivan.petrov@example.com, +7 999 123-45-67.',
      }),
    ];
    const result = redactSourcesForModel(sources);
    expect(result.documents[0]?.textBlocks[0]?.text).not.toContain('ivan.petrov@example.com');
    expect(sources[0]?.textBlocks[0]?.text).toContain('ivan.petrov@example.com');
    expect(sources[0]?.textBlocks[0]?.text).toContain('+7 999 123-45-67');
  });
});

describe('createTemporaryInputStore', () => {
  let root: string | null = null;

  afterEach(async () => {
    if (root !== null) {
      await rm(root, { recursive: true, force: true });
      root = null;
    }
  });

  async function makeStore(now?: () => number) {
    root = await mkdtemp(join(tmpdir(), 'hr-agent-test-'));
    return createTemporaryInputStore({
      root,
      ttlMs: 1000,
      ...(now === undefined ? {} : { now }),
    });
  }

  it('stores attachment bytes with owner-only permissions', async () => {
    const store = await makeStore();
    const entry = await store.save('att-1', 'секретный текст');
    expect(entry.bytes).toBeGreaterThan(0);
    expect(await store.read('att-1')).toEqual(Buffer.from('секретный текст', 'utf8'));
    const { stat } = await import('node:fs/promises');
    const mode = (await stat(entry.path)).mode & 0o777;
    expect(mode).toBe(0o600);
    await store.cleanupAll();
  });

  it('never writes a user-controlled path', async () => {
    const store = await makeStore();
    const entry = await store.save('../../etc/passwd', 'x');
    expect(entry.path.startsWith(root!)).toBe(true);
    expect(entry.path).not.toContain('passwd');
    await store.cleanupAll();
  });

  it('removes a single entry on cleanup and reports existence', async () => {
    const store = await makeStore();
    await store.save('att-1', 'x');
    expect(await store.exists('att-1')).toBe(true);
    await store.cleanup('att-1');
    expect(await store.exists('att-1')).toBe(false);
    expect(store.list()).toHaveLength(0);
    await store.cleanupAll();
  });

  it('removes expired entries by TTL and keeps fresh ones', async () => {
    let clock = 0;
    const store = await makeStore(() => clock);
    await store.save('old', 'a');
    clock = 500;
    await store.save('fresh', 'b');
    clock = 1200;
    expect(await store.cleanupExpired()).toEqual(['old']);
    expect(await store.exists('fresh')).toBe(true);
    await store.cleanupAll();
  });

  it('deletes the whole directory on cleanupAll', async () => {
    const store = await makeStore();
    const entry = await store.save('att-1', 'x');
    await store.cleanupAll();
    expect(await store.exists('att-1')).toBe(false);
    const { stat } = await import('node:fs/promises');
    await expect(stat(entry.path)).rejects.toThrow();
  });
});
