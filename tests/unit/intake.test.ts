import { describe, expect, it } from 'vitest';
import type { AnalysisRequest, RawInput } from '../../src/contracts/analysis.js';
import { SCHEMA_VERSION } from '../../src/contracts/analysis.js';
import {
  INPUT_LIMITS,
  decodedByteLength,
  mediaTypeOf,
  packageByteSize,
  validateAnalysisRequest,
} from '../../src/intake/validate-input.js';
import {
  detectLanguage,
  extractDocument,
  splitTextBlocks,
  type DocumentExtractorPort,
} from '../../src/intake/extract-document.js';

function input(overrides: Partial<RawInput> = {}): RawInput {
  return {
    id: 'doc-1',
    kind: 'resume',
    displayName: 'resume.txt',
    text: 'Иван Петров. Опыт работы 5 лет.',
    ...overrides,
  };
}

function request(overrides: Partial<AnalysisRequest> = {}): AnalysisRequest {
  return {
    schemaVersion: SCHEMA_VERSION,
    requestId: 'req-1',
    locale: 'ru-RU',
    vacancyInputs: [input({ id: 'vac-1', kind: 'vacancy', displayName: 'vacancy.txt', text: 'Роль: Backend Engineer' })],
    applicationInputs: [input()],
    suppliedUrls: [],
    ...overrides,
  };
}

describe('validateAnalysisRequest', () => {
  it('accepts a minimal honest package', () => {
    const result = validateAnalysisRequest(request());
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
  });

  it('rejects an empty package', () => {
    const result = validateAnalysisRequest(request({ vacancyInputs: [], applicationInputs: [] }));
    expect(result.ok).toBe(false);
    expect(result.errors.map((error) => error.code)).toContain('empty_input');
  });

  it('rejects a mismatched schema version', () => {
    const result = validateAnalysisRequest(request({ schemaVersion: '0.9.0' }));
    expect(result.ok).toBe(false);
    expect(result.errors[0]?.code).toBe('invalid_request');
  });

  it('reports unsupported file types without aborting the run', () => {
    const result = validateAnalysisRequest(
      request({
        applicationInputs: [input({ displayName: 'resume.pages', mediaType: 'application/x-iwork-pages-sffpages', contentBase64: 'AAAA' })],
      }),
    );
    expect(result.ok).toBe(true);
    expect(result.errors).toContainEqual(
      expect.objectContaining({ code: 'unsupported_file', recoverable: true }),
    );
  });

  it('rejects oversized files and packages explicitly', () => {
    const oversize = Buffer.alloc(INPUT_LIMITS.maxFileBytes + 4).toString('base64');
    const result = validateAnalysisRequest(
      request({ applicationInputs: [input({ displayName: 'huge.pdf', mediaType: 'application/pdf', contentBase64: oversize })] }),
    );
    expect(result.errors.map((error) => error.code)).toContain('resource_limit');
    expect(result.errors.some((error) => error.message.includes('превышает лимит'))).toBe(true);
  });

  it('rejects invalid base64 as unreadable rather than guessing', () => {
    const result = validateAnalysisRequest(
      request({ applicationInputs: [input({ displayName: 'resume.pdf', mediaType: 'application/pdf', contentBase64: 'not base64!!' })] }),
    );
    expect(result.errors).toContainEqual(expect.objectContaining({ code: 'unreadable_document' }));
  });

  it('caps the number of attachments', () => {
    const many = Array.from({ length: INPUT_LIMITS.maxAttachments + 1 }, (_, index) =>
      input({ id: `doc-${index}`, displayName: `doc-${index}.txt` }),
    );
    const result = validateAnalysisRequest(request({ applicationInputs: many }));
    expect(result.ok).toBe(false);
    expect(result.errors).toContainEqual(
      expect.objectContaining({ code: 'resource_limit', recoverable: false }),
    );
  });

  it('rejects duplicate source ids before evidence can become ambiguous', () => {
    const duplicate = input({ id: 'vac-1' });
    const result = validateAnalysisRequest(request({ applicationInputs: [duplicate] }));
    expect(result.ok).toBe(false);
    expect(result.errors).toContainEqual(expect.objectContaining({ code: 'invalid_request', recoverable: false }));
  });

  it('warns about non-http links instead of trying to open them', () => {
    const result = validateAnalysisRequest(request({ suppliedUrls: ['file:///etc/passwd'] }));
    expect(result.warnings.join(' ')).toMatch(/http\/https/);
  });
});

describe('size helpers', () => {
  it('measures decoded base64 bytes and rejects malformed payloads', () => {
    expect(decodedByteLength(Buffer.from('hello').toString('base64'))).toBe(5);
    expect(decodedByteLength('%%%')).toBeNull();
    expect(decodedByteLength('')).toBeNull();
  });

  it('derives the media type from the file extension when not declared', () => {
    expect(mediaTypeOf(input({ displayName: 'cv.DOCX', mediaType: undefined }))).toBe(
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    );
    expect(mediaTypeOf(input({ displayName: 'notes', mediaType: undefined }))).toBeNull();
  });

  it('sums the package size', () => {
    expect(packageByteSize(request())).toBeGreaterThan(0);
  });
});

describe('extractDocument', () => {
  it('extracts inline text into blocks with a stable hash', async () => {
    const document = await extractDocument(input({ text: 'Первый абзац.\n\nВторой абзац.' }));
    expect(document.extractionStatus).toBe('extracted');
    expect(document.kind).toBe('inline_text');
    expect(document.textBlocks.map((block) => block.text)).toEqual(['Первый абзац.', 'Второй абзац.']);
    expect(document.textBlocks[0]?.id).toBe('doc-1-b1');
    expect(document.language).toBe('ru');
    expect(document.contentHash).toHaveLength(64);
  });

  it('marks an empty text input as empty', async () => {
    const document = await extractDocument(input({ text: '   ' }));
    expect(document.extractionStatus).toBe('empty');
    expect(document.warnings.length).toBeGreaterThan(0);
  });

  it('marks binary without a parser as unsupported', async () => {
    const document = await extractDocument(
      input({ displayName: 'cv.pdf', mediaType: 'application/pdf', contentBase64: Buffer.from('pdf').toString('base64'), text: undefined }),
    );
    expect(document.extractionStatus).toBe('unsupported_file');
  });

  it('surfaces parser failures as a readable reason', async () => {
    const extractor: DocumentExtractorPort = {
      supports: () => true,
      extract: () => Promise.reject(new Error('encrypted')),
    };
    const document = await extractDocument(
      input({ displayName: 'cv.pdf', mediaType: 'application/pdf', contentBase64: Buffer.from('pdf').toString('base64'), text: undefined }),
      { extractor },
    );
    expect(document.extractionStatus).toBe('unreadable_document');
    expect(document.warnings.join(' ')).toMatch(/encrypted/);
  });

  it('flags a scan without a text layer instead of pretending to read it', async () => {
    const extractor: DocumentExtractorPort = {
      supports: () => true,
      extract: () => Promise.resolve({ blocks: [] }),
    };
    const document = await extractDocument(
      input({ displayName: 'scan.pdf', mediaType: 'application/pdf', contentBase64: Buffer.from('pdf').toString('base64'), text: undefined }),
      { extractor },
    );
    expect(document.extractionStatus).toBe('unreadable_document');
    expect(document.warnings.join(' ')).toMatch(/OCR/);
  });

  it('stops extraction when the page or character budget is exceeded', async () => {
    const blocks = Array.from({ length: INPUT_LIMITS.maxPages + 1 }, (_, index) => ({
      id: `p-${index}`,
      page: index + 1,
      text: `Страница ${index + 1}`,
    }));
    const extractor: DocumentExtractorPort = {
      supports: () => true,
      extract: () => Promise.resolve({ blocks }),
    };
    const document = await extractDocument(
      input({ displayName: 'big.pdf', mediaType: 'application/pdf', contentBase64: Buffer.from('pdf').toString('base64'), text: undefined }),
      { extractor },
    );
    expect(document.extractionStatus).toBe('resource_limit');
  });

  it('counts distinct pages rather than paragraphs', async () => {
    const blocks = Array.from({ length: INPUT_LIMITS.maxPages + 1 }, (_, index) => ({
      id: `paragraph-${index}`,
      page: 1,
      text: `Абзац ${index}`,
    }));
    const extractor: DocumentExtractorPort = {
      supports: () => true,
      extract: () => Promise.resolve({ blocks }),
    };
    const document = await extractDocument(
      input({ displayName: 'many-paragraphs.pdf', mediaType: 'application/pdf', contentBase64: Buffer.from('pdf').toString('base64'), text: undefined }),
      { extractor },
    );
    expect(document.extractionStatus).toBe('extracted');
  });

  it('keeps parser warnings and marks the document partial', async () => {
    const extractor: DocumentExtractorPort = {
      supports: () => true,
      extract: () =>
        Promise.resolve({
          blocks: [{ id: 'b1', page: 1, text: 'Текст первой страницы.' }],
          warnings: ['Часть таблиц не распознана.'],
        }),
    };
    const document = await extractDocument(
      input({ displayName: 'cv.docx', mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', contentBase64: Buffer.from('docx').toString('base64'), text: undefined }),
      { extractor },
    );
    expect(document.extractionStatus).toBe('partial');
    expect(document.warnings).toContain('Часть таблиц не распознана.');
  });
});

describe('text helpers', () => {
  it('splits paragraphs and keeps them addressable', () => {
    expect(splitTextBlocks('A\n\n\nB', 'x').map((block) => block.id)).toEqual(['x-b1', 'x-b2']);
    expect(splitTextBlocks('   ', 'x')).toEqual([]);
  });

  it('detects language coarsely', () => {
    expect(detectLanguage('Привет мир')).toBe('ru');
    expect(detectLanguage('Hello world')).toBe('en');
    expect(detectLanguage('Hello мир')).toBe('mixed');
    expect(detectLanguage('12345')).toBeUndefined();
  });
});
