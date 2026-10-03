import type { AnalysisRequest, RawInput } from '../../src/contracts/analysis.js';
import { SCHEMA_VERSION } from '../../src/contracts/analysis.js';
import { COVER_LETTER_TEXT, RESUME_TEXT, SYNTHETIC_SOURCES, VACANCY_TEXT } from './synthetic.js';

function rawFrom(document: (typeof SYNTHETIC_SOURCES)[number]): RawInput {
  return {
    id: document.id,
    kind: document.inputKind,
    displayName: document.displayName,
    mediaType: 'text/plain',
    text: document.textBlocks.map((block) => block.text).join('\n\n'),
  };
}

export function makeRequest(overrides: Partial<AnalysisRequest> = {}): AnalysisRequest {
  const [vacancy, resume, cover] = SYNTHETIC_SOURCES;
  return {
    schemaVersion: SCHEMA_VERSION,
    requestId: 'req-int-1',
    locale: 'ru-RU',
    vacancyInputs: [rawFrom(vacancy!)],
    applicationInputs: [rawFrom(resume!), rawFrom(cover!)],
    suppliedUrls: [],
    ...overrides,
  };
}

export const RAW_TEXTS = { VACANCY_TEXT, RESUME_TEXT, COVER_LETTER_TEXT } as const;
