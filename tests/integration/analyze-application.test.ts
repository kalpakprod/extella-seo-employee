import { describe, expect, it } from 'vitest';
import type { RawInput } from '../../src/contracts/analysis.js';
import { analyzeApplication, createAnalysisSession } from '../../src/core/analyze-application.js';
import { createInMemoryBrowserPort } from '../../src/adapters/browser.js';
import { createInMemoryExtellaPort } from '../../src/adapters/extella.js';
import { validateReport } from '../../src/report/validate.js';
import { makeRequest } from '../fixtures/request.js';

function doc(overrides: Partial<RawInput> & { id: string }): RawInput {
  return { kind: 'resume', displayName: `${overrides.id}.txt`, text: 'Иван Петров. Опыт 3 года.', ...overrides };
}

describe('analyzeApplication', () => {
  it('runs a full package end to end and returns a validated card', async () => {
    const result = await analyzeApplication(makeRequest(), {});
    expect(result.status).toBe('completed');
    expect(result.report).toBeDefined();
    expect(result.report!.scorecard.overall).toBe(5);
    expect(result.report!.questions.length).toBeGreaterThanOrEqual(5);
    expect(result.report!.questions.length).toBeLessThanOrEqual(8);
    expect(validateReport(result.report!, result.sourceStatuses).valid).toBe(true);
    expect(result.errors).toEqual([]);
    expect(result.stageStatuses.validation).toBe('completed');
  });

  it('never invents an overall score without a vacancy', async () => {
    const result = await analyzeApplication(makeRequest({ vacancyInputs: [] }), {});
    expect(result.report).toBeDefined();
    expect(result.report!.scorecard.overall).toBeNull();
    expect(result.report!.scorecard.insufficientData).toBe(true);
    expect(result.report!.limitations.join(' ')).toMatch(/Вакансия не передана/);
  });

  it('continues with the remaining sources when one file is unsupported', async () => {
    const result = await analyzeApplication(
      makeRequest({
        applicationInputs: [
          makeRequest().applicationInputs[0]!,
          doc({ id: 'bad', displayName: 'cv.pages', mediaType: 'application/x-iwork-pages-sffpages', contentBase64: 'AAAA', text: undefined }),
        ],
      }),
      {},
    );
    expect(result.status).toBe('partial');
    expect(result.errors.map((error) => error.code)).toContain('unsupported_file');
    expect(result.report).toBeDefined();
    expect(result.report!.header.name).toBe('Иван Петров');
  });

  it('uses the configured document extractor for binary resumes', async () => {
    const binaryResume = doc({
      id: 'binary-resume',
      displayName: 'resume.pdf',
      mediaType: 'application/pdf',
      text: undefined,
      contentBase64: Buffer.from('synthetic pdf').toString('base64'),
    });
    const result = await analyzeApplication(
      makeRequest({ applicationInputs: [binaryResume] }),
      {
        extractor: {
          supports: (mediaType) => mediaType === 'application/pdf',
          extract: () => Promise.resolve({
            blocks: [{ id: 'parser-controlled-id', page: 1, text: 'Иван Петров, 35 лет. Опыт 2018-2026. Node.js и TypeScript.' }],
          }),
        },
      },
    );
    expect(result.sourceStatuses.find((source) => source.id === 'binary-resume')).toMatchObject({
      extractionStatus: 'extracted',
      textBlocks: [expect.objectContaining({ id: 'binary-resume-b1' })],
    });
    expect(result.report?.header.name).toBe('Иван Петров');
  });

  it('reports a failed intake without producing a card', async () => {
    const result = await analyzeApplication(makeRequest({ vacancyInputs: [], applicationInputs: [] }), {});
    expect(result.status).toBe('failed');
    expect(result.report).toBeUndefined();
    expect(result.stageStatuses.intake).toBe('failed');
  });

  it('asks HR to log in when the only material is a closed page', async () => {
    const extella = createInMemoryExtellaPort();
    const result = await analyzeApplication(
      makeRequest({
        vacancyInputs: [],
        applicationInputs: [],
        suppliedUrls: ['https://hh.ru/applicant/1'],
      }),
      {
        browser: createInMemoryBrowserPort([{ url: 'https://hh.ru/applicant/1', status: 'needs_login' }]),
        extella,
      },
    );
    expect(result.status).toBe('waiting_for_user');
    expect(result.report).toBeUndefined();
    expect(result.errors.map((error) => error.code)).toContain('login_required');
    expect(extella.actions).toHaveLength(1);
    expect(extella.actions[0]?.action.url).toBe('https://hh.ru/applicant/1');
  });

  it('still produces a partial card when a link needs login but a resume exists', async () => {
    const result = await analyzeApplication(
      makeRequest({ suppliedUrls: ['https://hh.ru/applicant/1'] }),
      { browser: createInMemoryBrowserPort([{ url: 'https://hh.ru/applicant/1', status: 'needs_login' }]) },
    );
    expect(result.status).toBe('partial');
    expect(result.errors.map((error) => error.code)).toContain('login_required');
    expect(result.report).toBeDefined();
  });

  it('reads a supplied page and keeps it as a source', async () => {
    const result = await analyzeApplication(
      makeRequest({ suppliedUrls: ['https://hh.ru/vacancy/1'] }),
      {
        browser: createInMemoryBrowserPort([
          { url: 'https://hh.ru/vacancy/1', status: 'readable', title: 'Vacancy', text: 'Дополнительные детали вакансии.' },
        ]),
      },
    );
    expect(result.sourceStatuses.some((source) => source.kind === 'link' && source.extractionStatus === 'extracted')).toBe(true);
    expect(result.status).toBe('completed');
  });

  it('allocates link source ids that cannot collide with attachment ids', async () => {
    const result = await analyzeApplication(
      makeRequest({
        applicationInputs: [{ ...makeRequest().applicationInputs[0]!, id: 'link-1' }],
        suppliedUrls: ['https://hh.ru/vacancy/1'],
      }),
      {
        browser: createInMemoryBrowserPort([
          { url: 'https://hh.ru/vacancy/1', status: 'readable', text: 'Дополнительные детали.' },
        ]),
      },
    );
    expect(result.sourceStatuses.map((source) => source.id)).toEqual(
      expect.arrayContaining(['link-1', 'link-2']),
    );
  });

  it('notes that a link was not read when no browser is connected', async () => {
    const result = await analyzeApplication(makeRequest({ suppliedUrls: ['https://hh.ru/vacancy/1'] }), {});
    expect(result.errors.map((error) => error.code)).toContain('integration_unavailable');
    expect(result.report!.limitations.join(' ')).toMatch(/Ссылка не открыта/);
  });

  it('blocks non-http and private submitted URLs before calling the browser', async () => {
    const calls: string[] = [];
    const result = await analyzeApplication(
      makeRequest({ suppliedUrls: ['file:///etc/passwd', 'http://127.0.0.1/admin'] }),
      {
        browser: {
          listOpenTabs: () => Promise.resolve([]),
          readSubmittedPage: ({ url }) => {
            calls.push(url);
            return Promise.resolve({ status: 'readable', url, blocks: [], warnings: [] });
          },
        },
      },
    );
    expect(calls).toEqual([]);
    expect(result.errors.map((error) => error.code)).toEqual(['source_unavailable', 'source_unavailable']);
  });

  it('stops on cancellation and cleans the result', async () => {
    const controller = new AbortController();
    controller.abort();
    const result = await analyzeApplication(makeRequest(), {}, controller.signal);
    expect(result.status).toBe('cancelled');
    expect(result.report).toBeUndefined();
    expect(result.errors.map((error) => error.code)).toContain('cancelled');
  });

  it('refuses to mix two candidates in one package', async () => {
    const result = await analyzeApplication(
      makeRequest({
        applicationInputs: [
          makeRequest().applicationInputs[0]!,
          doc({ id: 'res-2', text: 'Мария Сидорова, 29 лет. Опыт 4 года.' }),
        ],
      }),
      {},
    );
    expect(result.status).toBe('failed');
    expect(result.report).toBeUndefined();
    expect(result.errors.map((error) => error.code)).toContain('invalid_request');
    expect(result.errors.map((error) => error.message).join(' ')).toMatch(/несколько кандидатов/);
  });

  it('does not treat a duplicated resume of the same person as two candidates', async () => {
    const duplicated = makeRequest();
    const result = await analyzeApplication(
      makeRequest({ applicationInputs: [...duplicated.applicationInputs, { ...duplicated.applicationInputs[0]!, id: 'res-copy' }] }),
      {},
    );
    expect(result.status).toBe('completed');
  });

  it('publishes progress and the report through the Extella port', async () => {
    const extella = createInMemoryExtellaPort();
    await analyzeApplication(makeRequest(), { extella });
    expect(extella.progress.length).toBeGreaterThanOrEqual(2);
    expect(extella.progress.at(-1)?.progress).toBe(1);
    expect(extella.reports.get('req-int-1')).toBeDefined();
  });

  it('is deterministic for the same input', async () => {
    const first = await analyzeApplication(makeRequest(), {});
    const second = await analyzeApplication(makeRequest(), {});
    expect(second.report).toEqual(first.report);
  });
});

describe('createAnalysisSession', () => {
  it('joins a repeated requestId instead of starting a second run', async () => {
    const session = createAnalysisSession({});
    const [first, second] = await Promise.all([session.analyze(makeRequest()), session.analyze(makeRequest())]);
    expect(second).toBe(first);
    expect(session.activeCount()).toBe(0);
  });

  it('returns the cached finished result and can be cleared', async () => {
    const session = createAnalysisSession({});
    const first = await session.analyze(makeRequest());
    const cached = await session.analyze(makeRequest());
    expect(cached).toBe(first);
    session.clear('req-int-1');
    const rerun = await session.analyze(makeRequest());
    expect(rerun).not.toBe(first);
    expect(rerun.report).toEqual(first.report);
  });

  it('removes a rejected run so the request id can be retried', async () => {
    let fail = true;
    const session = createAnalysisSession({
      extella: {
        publishProgress: () => {
          if (fail) {
            fail = false;
            return Promise.reject(new Error('bridge unavailable'));
          }
          return Promise.resolve();
        },
        publishReport: () => Promise.resolve(),
        requestUserAction: () => Promise.resolve(),
        publishFailure: () => Promise.resolve(),
      },
    });
    await expect(session.analyze(makeRequest())).rejects.toThrow('bridge unavailable');
    expect(session.activeCount()).toBe(0);
    await expect(session.analyze(makeRequest())).resolves.toMatchObject({ status: 'completed' });
  });
});
