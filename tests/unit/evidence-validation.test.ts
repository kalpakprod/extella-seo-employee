import { describe, expect, it } from 'vitest';
import { validateReport } from '../../src/report/validate.js';
import { makeEvidenceRef } from '../../src/analysis/evidence.js';
import { makeDocument } from '../fixtures/synthetic.js';
import { makeValidReport } from '../fixtures/report.js';
import { SYNTHETIC_SOURCES } from '../fixtures/synthetic.js';

describe('report evidence validation', () => {
  it('stores the verbatim source slice when matching normalized whitespace', () => {
    const sources = [makeDocument({ id: 'spaces', text: 'Node.js   и\tTypeScript' })];
    const ref = makeEvidenceRef(sources, 'Node.js и TypeScript');
    expect(ref?.quote).toBe('Node.js   и\tTypeScript');
    expect(ref).toMatchObject({ start: 0, end: sources[0]!.textBlocks[0]!.text.length });
  });

  it('accepts a report whose citations resolve to source text', () => {
    const result = validateReport(makeValidReport(), SYNTHETIC_SOURCES);
    expect(result.valid).toBe(true);
    expect(result.issues).toEqual([]);
  });

  it('rejects a quote that does not match the source text', () => {
    const report = makeValidReport();
    const changed = {
      ...report,
      matches: [
        {
          ...report.matches[0]!,
          evidenceRefs: [{ ...report.matches[0]!.evidenceRefs[0]!, quote: 'Совсем другой текст' }],
        },
      ],
    };
    const result = validateReport(changed, SYNTHETIC_SOURCES);
    expect(result.valid).toBe(false);
    expect(result.issues.map((issue) => issue.code)).toContain('quote_not_found');
  });

  it('rejects a reference to a non-existent source', () => {
    const report = makeValidReport();
    const changed = {
      ...report,
      matches: [
        {
          ...report.matches[0]!,
          evidenceRefs: [{ ...report.matches[0]!.evidenceRefs[0]!, sourceId: 'ghost' }],
        },
      ],
    };
    const result = validateReport(changed, SYNTHETIC_SOURCES);
    expect(result.issues.map((issue) => issue.code)).toContain('unknown_source');
  });

  it('rejects an out-of-range offset', () => {
    const report = makeValidReport();
    const changed = {
      ...report,
      matches: [
        {
          ...report.matches[0]!,
          evidenceRefs: [{ ...report.matches[0]!.evidenceRefs[0]!, start: 0, end: 10_000 }],
        },
      ],
    };
    const result = validateReport(changed, SYNTHETIC_SOURCES);
    expect(result.issues.map((issue) => issue.code)).toContain('invalid_range');
  });

  it('requires evidence for a matched requirement', () => {
    const report = makeValidReport();
    const changed = {
      ...report,
      matches: [{ ...report.matches[0]!, evidenceRefs: [] }],
    };
    const result = validateReport(changed, SYNTHETIC_SOURCES);
    expect(result.issues.map((issue) => issue.code)).toContain('missing_evidence');
  });

  it('reports an invalid schema and bad evidence together', () => {
    const report = makeValidReport();
    const changed = {
      ...report,
      scorecard: { ...report.scorecard, overall: 9 },
      matches: [{ ...report.matches[0]!, evidenceRefs: [] }],
    };
    const result = validateReport(changed, SYNTHETIC_SOURCES);
    const codes = result.issues.map((issue) => issue.code);
    expect(codes).toContain('schema_violation');
    expect(codes).toContain('missing_evidence');
  });
});
