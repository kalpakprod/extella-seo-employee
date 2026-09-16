import { describe, expect, it } from 'vitest';
import { SYNTHETIC_SOURCES } from '../fixtures/synthetic.js';
import { parseJob, parseVacancyRole } from '../../src/analysis/requirements.js';
import { normalizeFacts } from '../../src/analysis/facts.js';
import { matchRequirements } from '../../src/analysis/matching.js';
import { detectInconsistencies } from '../../src/analysis/integrity.js';
import { reviewTextPatterns } from '../../src/analysis/ai-patterns.js';
import { calculateScore } from '../../src/analysis/scoring.js';
import { composeReport } from '../../src/report/compose.js';
import { validateReport } from '../../src/report/validate.js';

const NOW = (): Date => new Date('2026-09-15T00:00:00Z');

function buildReport() {
  const requirements = parseJob(SYNTHETIC_SOURCES);
  const facts = normalizeFacts(SYNTHETIC_SOURCES, { now: NOW });
  const matches = matchRequirements(requirements, facts, SYNTHETIC_SOURCES);
  const signals = detectInconsistencies(facts, SYNTHETIC_SOURCES, { now: NOW });
  const aiCheck = reviewTextPatterns(SYNTHETIC_SOURCES);
  const scorecard = calculateScore(requirements, matches);
  return composeReport({
    requestId: 'req-compose-1',
    sources: SYNTHETIC_SOURCES,
    requirements,
    matches,
    facts,
    signals,
    aiCheck,
    scorecard,
    role: parseVacancyRole(SYNTHETIC_SOURCES),
    sourceLimitations: [],
  });
}

describe('composeReport', () => {
  const report = buildReport();

  it('passes schema and evidence-integrity validation', () => {
    const result = validateReport(report, SYNTHETIC_SOURCES);
    expect(result.issues).toEqual([]);
    expect(result.valid).toBe(true);
  });

  it('carries the request id, role and a non-empty verdict', () => {
    expect(report.requestId).toBe('req-compose-1');
    expect(report.header.role).toBe('Senior Backend Engineer');
    expect(report.header.name).toBe('Иван Петров');
    expect(report.header.verdict.length).toBeGreaterThan(0);
  });

  it('produces between five and eight interview questions', () => {
    expect(report.questions.length).toBeGreaterThanOrEqual(5);
    expect(report.questions.length).toBeLessThanOrEqual(8);
  });

  it('attaches evidence to every note and question', () => {
    const notes = [...report.strengths, ...report.gaps, ...report.greenFlags, ...report.features];
    expect(notes.length).toBeGreaterThan(0);
    for (const note of notes) {
      expect(note.evidenceRefs.length).toBeGreaterThan(0);
    }
    for (const question of report.questions) {
      expect(question.evidenceRefs.length).toBeGreaterThan(0);
    }
  });

  it('states that protected attributes never affect the score', () => {
    expect(report.limitations.join(' ')).toMatch(/не влияют на оценку/);
  });

  it('never mentions protected attributes in the scoring explanation', () => {
    expect(report.scorecard.explanation.join(' ')).not.toMatch(/возраст|пол|национальн/i);
  });

  it('is deterministic for identical input', () => {
    expect(buildReport()).toEqual(report);
  });
});
