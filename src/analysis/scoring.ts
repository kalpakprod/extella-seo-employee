import type {
  JobRequirement,
  RequirementCategory,
  RequirementMatch,
  Scorecard,
  SubScores,
} from '../contracts/analysis.js';
import { DEFAULT_SCORING_POLICY, type ScoringPolicy } from './policy.js';

const ASSESSABLE: ReadonlySet<RequirementMatch['status']> = new Set([
  'matched',
  'partial',
  'missing',
]);

function clampScore(value: number): number {
  return Math.min(5, Math.max(1, value));
}

function bandFor(ratio: number, policy: ScoringPolicy): number {
  const band = policy.overallBands.find((candidate) => ratio < candidate.below);
  if (band !== undefined) {
    return band.score;
  }
  return policy.overallBands.at(-1)?.score ?? 1;
}

function weightedRatio(
  requirements: readonly JobRequirement[],
  matches: readonly RequirementMatch[],
  categories: readonly RequirementCategory[],
  policy: ScoringPolicy,
): { ratio: number; assessable: number } {
  const matchById = new Map(matches.map((match) => [match.requirementId, match]));
  const selected = requirements.filter(
    (requirement) =>
      requirement.eligibility === 'professional' && categories.includes(requirement.category),
  );
  let sum = 0;
  let max = 0;
  let assessable = 0;
  for (const requirement of selected) {
    const match = matchById.get(requirement.id);
    if (match === undefined || !ASSESSABLE.has(match.status)) {
      continue;
    }
    const weight = policy.weights[requirement.priority];
    sum += weight * policy.matchValue[match.status as 'matched' | 'partial' | 'missing'];
    max += weight;
    assessable += 1;
  }
  return { ratio: max === 0 ? 0 : sum / max, assessable };
}

/**
 * Deterministic, reproducible scoring. Protected attributes never appear in
 * the inputs, and evidence-quality describes how well claims are sourced, not
 * whether the person is honest.
 */
export function calculateScore(
  requirements: readonly JobRequirement[],
  matches: readonly RequirementMatch[],
  policy: ScoringPolicy = DEFAULT_SCORING_POLICY,
): Scorecard {
  const explanation: string[] = [];
  const professional = requirements.filter(
    (requirement) => requirement.eligibility === 'professional',
  );
  const matchById = new Map(matches.map((match) => [match.requirementId, match]));

  if (professional.length === 0) {
    explanation.push('Профессиональные требования не переданы — итоговая оценка не выводится.');
    return {
      overall: null,
      subscores: { experience: null, skills: null, hrPreferences: null, evidenceQuality: null },
      coverage: 0,
      policyVersion: policy.version,
      explanation,
      insufficientData: true,
    };
  }

  const assessable = professional.filter((requirement) => {
    const match = matchById.get(requirement.id);
    return match !== undefined && ASSESSABLE.has(match.status);
  });
  const coverage = assessable.length / professional.length;

  const mustHaveUnknown = professional.some((requirement) => {
    if (requirement.priority !== 'must_have') {
      return false;
    }
    const match = matchById.get(requirement.id);
    return match === undefined || match.status === 'unknown';
  });
  const mustHaveMissing = professional.some((requirement) => {
    if (requirement.priority !== 'must_have') {
      return false;
    }
    return matchById.get(requirement.id)?.status === 'missing';
  });

  const overall0 = weightedRatio(professional, matches, [
    'skill',
    'experience',
    'education',
    'language',
    'location',
    'format',
    'compensation',
    'other',
  ], policy);

  explanation.push(
    `Покрытие требований: ${Math.round(coverage * 100)}% (${assessable.length} из ${professional.length}).`,
  );

  let overall: number | null = null;
  let insufficientData = false;
  if (coverage < policy.coverage.minimumForOverall) {
    insufficientData = true;
    explanation.push(
      `Покрытие ниже ${Math.round(
        policy.coverage.minimumForOverall * 100,
      )}% — оценка соответствия не выводится.`,
    );
  } else if (mustHaveUnknown) {
    insufficientData = true;
    explanation.push('Есть must-have без подтверждения — оценка соответствия не выводится.');
  } else {
    overall = bandFor(overall0.ratio, policy);
    explanation.push(`Взвешенное соответствие: ${Math.round(overall0.ratio * 100)}%.`);
    if (mustHaveMissing) {
      const capped = Math.min(overall, policy.caps.confirmedMustHaveMismatch);
      if (capped !== overall) {
        explanation.push(
          `Подтверждено несоответствие must-have — оценка ограничена ${policy.caps.confirmedMustHaveMismatch}.`,
        );
      }
      overall = capped;
    }
  }

  const subscores: SubScores = {
    experience: subscore(professional, matches, policy.subscoreGroups.experience, policy),
    skills: subscore(professional, matches, policy.subscoreGroups.skills, policy),
    hrPreferences: subscore(professional, matches, policy.subscoreGroups.hrPreferences, policy),
    evidenceQuality: evidenceQuality(professional, matches),
  };

  return {
    overall,
    subscores,
    coverage,
    policyVersion: policy.version,
    explanation,
    insufficientData,
  };
}

function subscore(
  requirements: readonly JobRequirement[],
  matches: readonly RequirementMatch[],
  categories: readonly RequirementCategory[],
  policy: ScoringPolicy,
): number | null {
  const { ratio, assessable } = weightedRatio(requirements, matches, categories, policy);
  if (assessable === 0) {
    return null;
  }
  return clampScore(bandFor(ratio, policy));
}

function evidenceQuality(
  requirements: readonly JobRequirement[],
  matches: readonly RequirementMatch[],
): number | null {
  if (requirements.length === 0) {
    return null;
  }
  const matchById = new Map(matches.map((match) => [match.requirementId, match]));
  const withEvidence = requirements.filter(
    (requirement) => (matchById.get(requirement.id)?.evidenceRefs.length ?? 0) > 0,
  );
  const ratio = withEvidence.length / requirements.length;
  return clampScore(1 + Math.round(4 * ratio));
}
