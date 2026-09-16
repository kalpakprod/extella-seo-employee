import { describe, expect, it } from 'vitest';
import { validateReportSchema } from '../../src/contracts/validate-schema.js';
import { makeValidReport } from '../fixtures/report.js';

describe('candidate report schema', () => {
  it('accepts a well-formed report', () => {
    const result = validateReportSchema(makeValidReport());
    expect(result.valid).toBe(true);
    expect(result.issues).toEqual([]);
  });

  it('rejects an out-of-range overall score', () => {
    const report = makeValidReport();
    const broken = { ...report, scorecard: { ...report.scorecard, overall: 6 } };
    const result = validateReportSchema(broken);
    expect(result.valid).toBe(false);
    expect(result.issues.some((issue) => issue.path === 'scorecard.overall')).toBe(true);
  });

  it('rejects an unknown match status', () => {
    const report = makeValidReport();
    const broken = {
      ...report,
      matches: [{ ...report.matches[0], status: 'definitely_not_a_status' }],
    };
    expect(validateReportSchema(broken).valid).toBe(false);
  });

  it('rejects an evidence ref without a quote', () => {
    const report = makeValidReport();
    const ref = report.matches[0]!.evidenceRefs[0]!;
    const { quote: _quote, ...withoutQuote } = ref;
    const broken = {
      ...report,
      matches: [{ ...report.matches[0], evidenceRefs: [withoutQuote] }],
    };
    expect(validateReportSchema(broken).valid).toBe(false);
  });

  it('rejects an unknown ai-check hypothesis', () => {
    const report = makeValidReport();
    const broken = {
      ...report,
      aiCheck: { ...report.aiCheck, hypothesis: 'definitely_ai' },
    };
    expect(validateReportSchema(broken).valid).toBe(false);
  });
});
