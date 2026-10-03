import rawPolicy from '../../config/scoring-policy.json' with { type: 'json' };
import type { RequirementCategory } from '../contracts/analysis.js';

export interface ScoringPolicy {
  readonly version: string;
  readonly weights: Readonly<Record<'must_have' | 'nice_to_have', number>>;
  readonly matchValue: Readonly<Record<'matched' | 'partial' | 'missing', number>>;
  readonly coverage: { readonly minimumForOverall: number };
  readonly overallBands: readonly { readonly below: number; readonly score: number }[];
  readonly caps: { readonly confirmedMustHaveMismatch: number };
  readonly subscoreGroups: Readonly<Record<'experience' | 'skills' | 'hrPreferences', readonly RequirementCategory[]>>;
}

function fail(message: string): never {
  throw new Error(`Некорректная политика оценивания: ${message}`);
}

export function validatePolicy(raw: unknown): ScoringPolicy {
  const candidate = raw as Partial<ScoringPolicy>;
  if (typeof candidate.version !== 'string' || candidate.version.length === 0) {
    fail('отсутствует version');
  }
  if (candidate.weights === undefined || candidate.matchValue === undefined) {
    fail('отсутствуют веса или значения совпадений');
  }
  if (candidate.coverage === undefined || typeof candidate.coverage.minimumForOverall !== 'number') {
    fail('отсутствует порог покрытия');
  }
  if (!Array.isArray(candidate.overallBands) || candidate.overallBands.length === 0) {
    fail('отсутствуют диапазоны оценок');
  }
  if (candidate.caps === undefined || typeof candidate.caps.confirmedMustHaveMismatch !== 'number') {
    fail('отсутствует ограничение по must-have');
  }
  if (candidate.subscoreGroups === undefined) {
    fail('отсутствуют группы под-оценок');
  }
  const statuses = ['matched', 'partial', 'missing'] as const;
  for (const status of statuses) {
    if (typeof candidate.matchValue[status] !== 'number') {
      fail(`нет значения для статуса ${status}`);
    }
  }
  return candidate as ScoringPolicy;
}

export const DEFAULT_SCORING_POLICY: ScoringPolicy = validatePolicy(rawPolicy);
