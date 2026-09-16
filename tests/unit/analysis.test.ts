import { describe, expect, it } from 'vitest';
import { OVERLAP_RESUME_TEXT, SYNTHETIC_SOURCES, makeDocument } from '../fixtures/synthetic.js';
import { locateQuote } from '../../src/analysis/text.js';
import { parseJob, parseVacancyRole } from '../../src/analysis/requirements.js';
import { normalizeFacts, mergeExperienceMonths } from '../../src/analysis/facts.js';
import { matchRequirements } from '../../src/analysis/matching.js';
import { detectInconsistencies } from '../../src/analysis/integrity.js';
import { reviewTextPatterns } from '../../src/analysis/ai-patterns.js';
import { calculateScore } from '../../src/analysis/scoring.js';

const NOW = (): Date => new Date('2026-09-15T00:00:00Z');

function assertRefsResolve(refs: readonly { sourceId: string; blockId: string; start: number; end: number; quote: string }[], label: string): void {
  for (const ref of refs) {
    const source = SYNTHETIC_SOURCES.find((document) => document.id === ref.sourceId);
    expect(source, `${label}: source ${ref.sourceId} must exist`).toBeDefined();
    const block = source!.textBlocks.find((candidate) => candidate.id === ref.blockId);
    expect(block, `${label}: block ${ref.blockId} must exist`).toBeDefined();
    expect(locateQuote(block!.text, ref.quote), `${label}: quote must resolve`).toEqual({
      start: ref.start,
      end: ref.end,
    });
  }
}

describe('parseJob', () => {
  const requirements = parseJob(SYNTHETIC_SOURCES);

  it('extracts the role from the vacancy', () => {
    expect(parseVacancyRole(SYNTHETIC_SOURCES)).toBe('Senior Backend Engineer');
  });

  it('splits must/nice/misc lists into individual requirements', () => {
    const texts = requirements.map((requirement) => requirement.text);
    expect(texts).toContain('Node.js');
    expect(texts).toContain('TypeScript');
    expect(texts).toContain('Kubernetes');
    expect(texts).toContain('Москва');
    expect(texts).toContain('стаж от 5 лет');
    expect(texts).toContain('300 000 - 350 000 руб');
    expect(texts).toContain('деньги до 350 000 руб');
  });

  it('classifies priority, category and evidence for each requirement', () => {
    const node = requirements.find((requirement) => requirement.text === 'Node.js');
    expect(node).toMatchObject({ priority: 'must_have', category: 'skill', eligibility: 'professional' });

    const experience = requirements.find((requirement) => requirement.text === 'опыт backend от 5 лет');
    expect(experience).toMatchObject({ priority: 'must_have', category: 'experience' });

    const salary = requirements.find((requirement) => requirement.text === '300 000 - 350 000 руб');
    expect(salary).toMatchObject({ priority: 'nice_to_have', category: 'compensation' });
  });

  it('marks protected attributes as excluded instead of scoring them', () => {
    const protectedRequirement = requirements.find((requirement) =>
      /возраст/i.test(requirement.text),
    );
    expect(protectedRequirement?.eligibility).toBe('excluded_protected');
  });

  it('excludes an age range even when HR omits the word age', () => {
    const requirements = parseJob([
      makeDocument({
        id: 'age-pref',
        inputKind: 'vacancy',
        text: 'Пожелания HR: 30-40 лет, опыт от 5 лет.',
      }),
    ]);
    expect(requirements.find((requirement) => requirement.text === '30-40 лет')).toMatchObject({
      eligibility: 'excluded_protected',
    });
    expect(requirements.find((requirement) => requirement.text === 'опыт от 5 лет')).toMatchObject({
      eligibility: 'professional',
    });
  });

  it('gives every requirement at least one resolvable evidence quote', () => {
    expect(requirements.length).toBeGreaterThan(0);
    for (const requirement of requirements) {
      expect(requirement.evidenceRefs.length).toBeGreaterThan(0);
      assertRefsResolve(requirement.evidenceRefs, requirement.text);
    }
  });
});

describe('normalizeFacts', () => {
  const facts = normalizeFacts(SYNTHETIC_SOURCES, { now: NOW });

  it('extracts identity and contact-adjacent facts only when stated', () => {
    expect(facts.name).toBe('Иван Петров');
    expect(facts.ageYears).toBe(34);
    expect(facts.location).toBe('Москва');
    expect(facts.salary).toMatchObject({ amount: 320000, currency: 'RUB', period: 'month' });
  });

  it('merges work periods without double counting', () => {
    expect(facts.totalExperienceMonths).toBe(120);
    expect(facts.workPeriods).toHaveLength(2);
  });

  it('extracts languages and skills mentioned in the application', () => {
    expect(facts.languages).toEqual([expect.objectContaining({ name: 'Английский', level: 'B2' })]);
    expect(facts.skills.map((skill) => skill.term)).toEqual(
      expect.arrayContaining(['node', 'typescript', 'postgresql', 'kubernetes']),
    );
  });

  it('records availability when the candidate states it', () => {
    expect(facts.availability).toContain('Готов выйти');
  });

  it('reports no uncertainty when all key facts are present', () => {
    expect(facts.uncertainty).toEqual([]);
  });

  it('attaches resolvable evidence to every fact', () => {
    expect(facts.items.length).toBeGreaterThan(0);
    for (const item of facts.items) {
      expect(item.evidenceRefs.length).toBeGreaterThan(0);
      assertRefsResolve(item.evidenceRefs, item.field);
    }
  });

  it('flags missing facts as unknown instead of inventing values', () => {
    const sparse = normalizeFacts(
      [makeDocument({ id: 'sparse', inputKind: 'resume', displayName: 'sparse.txt', text: 'Отклик без деталей.' })],
      { now: NOW },
    );
    expect(sparse.name).toBeNull();
    expect(sparse.ageYears).toBeNull();
    expect(sparse.salary).toBeNull();
    expect(sparse.uncertainty.join(' ')).toMatch(/не указан|не указана|не найдены|не найден/);
  });

  it('extracts an explicitly labelled age without requiring a year suffix', () => {
    const facts = normalizeFacts(
      [makeDocument({ id: 'labelled-age', inputKind: 'resume', text: 'Иван Петров. Возраст: 35.' })],
      { now: NOW },
    );
    expect(facts.ageYears).toBe(35);
    expect(facts.items.find((item) => item.field === 'age')?.value).toBe('35');
  });
});

describe('mergeExperienceMonths', () => {
  it('unions overlapping intervals', () => {
    const result = mergeExperienceMonths(
      [
        { startYear: 2018, endYear: 2024 },
        { startYear: 2020, endYear: 2022 },
      ],
      2026,
    );
    expect(result.months).toBe(72);
    expect(result.intervals).toHaveLength(1);
  });
});

describe('matchRequirements', () => {
  const requirements = parseJob(SYNTHETIC_SOURCES);
  const facts = normalizeFacts(SYNTHETIC_SOURCES, { now: NOW });
  const matches = matchRequirements(requirements, facts, SYNTHETIC_SOURCES);
  const byText = (text: string) =>
    matches.find((match) => {
      const requirement = requirements.find((candidate) => candidate.id === match.requirementId);
      return requirement?.text === text;
    })!;

  it('marks evidenced requirements as matched', () => {
    expect(byText('Node.js').status).toBe('matched');
    expect(byText('Node.js').evidenceRefs.length).toBeGreaterThan(0);
  });

  it('treats unreferenced requirements as unknown, never as a proven mismatch', () => {
    const unknown = byText('развивать архитектуру');
    expect(unknown.status).toBe('unknown');
    expect(unknown.evidenceRefs).toEqual([]);
    expect(unknown.explanation).toMatch(/не доказанное несоответствие/);
  });

  it('keeps protected requirements out of professional matching', () => {
    const excluded = matches.filter((match) => match.status === 'excluded');
    expect(excluded).toHaveLength(1);
    const requirement = requirements.find((candidate) => candidate.id === excluded[0]!.requirementId);
    expect(requirement?.eligibility).toBe('excluded_protected');
  });

  it('matches experience and compensation through their dedicated rules', () => {
    expect(byText('опыт backend от 5 лет').status).toBe('matched');
    expect(byText('300 000 - 350 000 руб').status).toBe('matched');
  });
});

describe('detectInconsistencies', () => {
  it('flags quantified claims that carry no proof', () => {
    const facts = normalizeFacts(SYNTHETIC_SOURCES, { now: NOW });
    const signals = detectInconsistencies(facts, SYNTHETIC_SOURCES, { now: NOW });
    expect(signals.map((signal) => signal.kind)).toContain('unverified_claim');
    for (const signal of signals) {
      expect(signal.evidenceRefs.length).toBeGreaterThan(0);
      assertRefsResolve(signal.evidenceRefs, signal.kind);
      expect(signal.interviewQuestion.length).toBeGreaterThan(0);
    }
  });

  it('detects impossible dates and overlapping periods', () => {
    const sources = [
      makeDocument({ id: 'res-overlap', inputKind: 'resume', displayName: 'overlap.txt', text: OVERLAP_RESUME_TEXT }),
    ];
    const facts = normalizeFacts(sources, { now: NOW });
    const kinds = detectInconsistencies(facts, sources, { now: NOW }).map((signal) => signal.kind);
    expect(kinds).toContain('impossible_date');
    expect(kinds).toContain('period_overlap');
  });
});

describe('reviewTextPatterns', () => {
  it('only reports observable patterns and keeps the AI-authorship limitation', () => {
    const review = reviewTextPatterns(SYNTHETIC_SOURCES);
    expect(review.assessable).toBe(true);
    expect(review.observations.length).toBeGreaterThan(0);
    expect(review.limitations.join(' ')).toMatch(/авторств/i);
    for (const signal of review.observations) {
      expect(signal.evidenceRefs.length).toBeGreaterThan(0);
      assertRefsResolve(signal.evidenceRefs, 'ai-pattern');
    }
  });
});

describe('calculateScore', () => {
  const requirements = parseJob(SYNTHETIC_SOURCES);
  const facts = normalizeFacts(SYNTHETIC_SOURCES, { now: NOW });
  const matches = matchRequirements(requirements, facts, SYNTHETIC_SOURCES);

  it('produces a deterministic scorecard with subscores and coverage', () => {
    const scorecard = calculateScore(requirements, matches);
    expect(scorecard).toEqual(calculateScore(requirements, matches));
    expect(scorecard.overall).toBe(5);
    expect(scorecard.policyVersion).toBe('1.0.0');
    expect(scorecard.insufficientData).toBe(false);
    expect(scorecard.subscores.evidenceQuality).toBe(4);
    expect(scorecard.coverage).toBeCloseTo(0.846, 3);
  });

  it('caps the overall score when a confirmed must-have is not met', () => {
    const withoutMustHave = matches.map((match) => {
      const requirement = requirements.find((candidate) => candidate.id === match.requirementId);
      if (requirement?.text === 'Node.js') {
        return { ...match, status: 'missing' as const, evidenceRefs: [] };
      }
      return match;
    });
    const scorecard = calculateScore(requirements, withoutMustHave);
    expect(scorecard.overall).not.toBeNull();
    expect(scorecard.overall!).toBeLessThanOrEqual(3);
  });

  it('withholds the overall score when coverage is too low', () => {
    const allUnknown = matches.map((match) => ({
      ...match,
      status: 'unknown' as const,
      evidenceRefs: [],
    }));
    const scorecard = calculateScore(requirements, allUnknown);
    expect(scorecard.overall).toBeNull();
    expect(scorecard.insufficientData).toBe(true);
  });
});
