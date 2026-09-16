import type {
  EvidenceRef,
  JobRequirement,
  RequirementMatch,
  SourceDocument,
} from '../contracts/analysis.js';
import { makeEvidenceRef } from './evidence.js';
import type { NormalizedFacts } from './facts.js';
import { sentenceContaining, tokenize } from './text.js';

export interface SalaryRange {
  readonly min: number | null;
  readonly max: number | null;
  readonly currency: 'RUB' | 'USD' | 'EUR' | 'unknown';
}

const CURRENCY_BY_MARKER: readonly (readonly [RegExp, SalaryRange['currency']])[] = [
  [/(руб\w*|₽|\brub\b)/i, 'RUB'],
  [/(доллар\w*|\busd\b|\$)/i, 'USD'],
  [/(евро|\beur\b|€)/i, 'EUR'],
];

export function parseSalaryRange(text: string): SalaryRange | null {
  const amounts = [...text.matchAll(/(\d[\d\s]{2,})/g)]
    .map((match) => Number.parseInt(match[1]!.replaceAll(/\s/g, ''), 10))
    .filter((amount) => Number.isFinite(amount) && amount >= 1000);
  if (amounts.length === 0) {
    return null;
  }
  const currency =
    CURRENCY_BY_MARKER.find(([pattern]) => pattern.test(text))?.[1] ?? 'unknown';
  const first = amounts[0]!;
  const last = amounts.at(-1)!;
  if (amounts.length >= 2) {
    return { min: Math.min(first, last), max: Math.max(first, last), currency };
  }
  const hasFrom = /(от|from|не\s+менее)/i.test(text);
  const hasTo = /(до|not\s+more|не\s+более)/i.test(text);
  if (hasFrom) {
    return { min: first, max: null, currency };
  }
  if (hasTo) {
    return { min: null, max: first, currency };
  }
  return { min: first, max: first, currency };
}

function requiredYears(text: string): number | null {
  const match = /(\d{1,2})\s*(?:\+)?\s*(?:год|года|лет)/i.exec(text);
  if (match === null) {
    return null;
  }
  return Number.parseInt(match[1]!, 10);
}

function dedupeRefs(refs: readonly EvidenceRef[]): EvidenceRef[] {
  const seen = new Set<string>();
  const result: EvidenceRef[] = [];
  for (const ref of refs) {
    const key = `${ref.sourceId}:${ref.blockId}:${ref.start}:${ref.end}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(ref);
  }
  return result;
}

function applicationSources(
  sources: readonly SourceDocument[],
): SourceDocument[] {
  const kinds = new Set(['resume', 'cover_letter', 'email_application', 'other']);
  return sources.filter((source) => kinds.has(source.inputKind));
}

function keywordMatch(
  requirement: JobRequirement,
  sources: readonly SourceDocument[],
): RequirementMatch {
  const keywords = [...new Set(tokenize(requirement.text))];
  if (keywords.length === 0) {
    return {
      requirementId: requirement.id,
      status: 'unknown',
      factIds: [],
      evidenceRefs: [],
      explanation: `По требованию «${requirement.text}» не выделены проверяемые ключевые слова.`,
    };
  }
  const keywordsWithEvidence = keywords.map((keyword) => {
    const sentence = applicationSources(sources)
      .flatMap((source) => source.textBlocks.map((block) => sentenceContaining(block.text, keyword)))
      .find((candidate): candidate is string => candidate !== null);
    const ref =
      sentence === undefined ? null : makeEvidenceRef(sources, sentence, applicationSources(sources).map((s) => s.id));
    return { keyword, ref };
  });

  const matched = keywordsWithEvidence.filter((entry) => entry.ref !== null);
  const ratio = matched.length / keywords.length;
  const evidenceRefs = dedupeRefs(
    matched.map((entry) => entry.ref).filter((ref): ref is EvidenceRef => ref !== null),
  );

  if (ratio === 1 && evidenceRefs.length > 0) {
    return {
      requirementId: requirement.id,
      status: 'matched',
      factIds: [],
      evidenceRefs,
      explanation: `В материалах отклика подтверждено: ${keywords.join(', ')}.`,
    };
  }
  if (ratio > 0 && evidenceRefs.length > 0) {
    const missing = keywordsWithEvidence.filter((entry) => entry.ref === null).map((e) => e.keyword);
    return {
      requirementId: requirement.id,
      status: 'partial',
      factIds: [],
      evidenceRefs,
      explanation: `Частично подтверждено. Нет явного упоминания: ${missing.join(', ')}.`,
    };
  }
  return {
    requirementId: requirement.id,
    status: 'unknown',
    factIds: [],
    evidenceRefs: [],
    explanation: `В материалах отклика нет упоминания по «${requirement.text}» — это не доказанное несоответствие.`,
  };
}

function experienceMatch(
  requirement: JobRequirement,
  facts: NormalizedFacts,
  sources: readonly SourceDocument[],
): RequirementMatch {
  const years = requiredYears(requirement.text);
  if (facts.totalExperienceMonths === 0) {
    return {
      requirementId: requirement.id,
      status: 'unknown',
      factIds: [],
      evidenceRefs: [],
      explanation: 'Периоды работы не распознаны, поэтому стаж нельзя ни подтвердить, ни опровергнуть.',
    };
  }
  const actualYears = facts.totalExperienceMonths / 12;
  const workRefs = dedupeRefs(
    facts.workPeriods.flatMap((period) => period.evidenceRefs.filter((ref) => ref !== null)),
  );
  const formattedActual = actualYears.toFixed(1).replace('.0', '');
  if (years !== null && actualYears + 0.25 < years) {
    return {
      requirementId: requirement.id,
      status: 'missing',
      factIds: [],
      evidenceRefs: workRefs.slice(0, 3),
      explanation: `Требуется ${years} лет, распознанный стаж по периодам — ${formattedActual} лет.`,
    };
  }
  const target = years === null ? 'требуемый' : `${years} лет`;
  if (workRefs.length === 0) {
    return {
      requirementId: requirement.id,
      status: 'unknown',
      factIds: [],
      evidenceRefs: [],
      explanation: `Стаж ${formattedActual} лет вычислен, но цитата периода не найдена.`,
    };
  }
  return {
    requirementId: requirement.id,
    status: 'matched',
    factIds: [],
    evidenceRefs: workRefs.slice(0, 3),
    explanation: `Стаж по объединению периодов ${formattedActual} лет покрывает ${target}.`,
  };
}

function compensationMatch(
  requirement: JobRequirement,
  facts: NormalizedFacts,
  sources: readonly SourceDocument[],
): RequirementMatch {
  const range = parseSalaryRange(requirement.text);
  if (range === null) {
    return {
      requirementId: requirement.id,
      status: 'unknown',
      factIds: [],
      evidenceRefs: [],
      explanation: 'В требовании не распознана числовая вилка зарплаты.',
    };
  }
  if (facts.salary === null) {
    return {
      requirementId: requirement.id,
      status: 'unknown',
      factIds: [],
      evidenceRefs: [],
      explanation: 'Зарплатные ожидания кандидата не указаны — сравнение невозможно.',
    };
  }
  if (
    facts.salary.currency !== 'unknown' &&
    range.currency !== 'unknown' &&
    facts.salary.currency !== range.currency
  ) {
    return {
      requirementId: requirement.id,
      status: 'unknown',
      factIds: [],
      evidenceRefs: [],
      explanation: `Валюты не сопоставимы (${facts.salary.currency} против ${range.currency}).`,
    };
  }
  const ref = facts.salary === null ? null : makeEvidenceRef(sources, facts.salary.quote);
  const salaryRef = ref;
  if (range.max !== null && facts.salary.amount > range.max) {
    return {
      requirementId: requirement.id,
      status: 'missing',
      factIds: [],
      evidenceRefs: salaryRef === null ? [] : [salaryRef],
      explanation: `Ожидания ${facts.salary.amount} выше верхней границы ${range.max}.`,
    };
  }
  return {
    requirementId: requirement.id,
    status: 'matched',
    factIds: [],
    evidenceRefs: salaryRef === null ? [] : [salaryRef],
    explanation: `Ожидания ${facts.salary.amount} укладываются в вилку ${
      range.min === null ? '' : `${range.min}–`
    }${range.max ?? '∞'}.`,
  };
}

export function matchRequirements(
  requirements: readonly JobRequirement[],
  facts: NormalizedFacts,
  sources: readonly SourceDocument[],
): RequirementMatch[] {
  return requirements.map((requirement) => {
    if (requirement.eligibility === 'excluded_protected') {
      return {
        requirementId: requirement.id,
        status: 'excluded',
        factIds: [],
        evidenceRefs: requirement.evidenceRefs,
        explanation:
          'Защищённый признак исключён из профессионального сопоставления и не влияет на оценку.',
      } satisfies RequirementMatch;
    }
    if (requirement.category === 'compensation') {
      return compensationMatch(requirement, facts, sources);
    }
    if (requirement.category === 'experience') {
      return experienceMatch(requirement, facts, sources);
    }
    return keywordMatch(requirement, sources);
  });
}

export function requirementTextById(
  requirements: readonly JobRequirement[],
): ReadonlyMap<string, JobRequirement> {
  return new Map(requirements.map((requirement) => [requirement.id, requirement]));
}
