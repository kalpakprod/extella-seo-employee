import type { CandidateFact, FactField, SourceDocument } from '../contracts/analysis.js';
import { makeEvidenceRef } from './evidence.js';
import { containsTerm, fold, normalizeText, splitSentences } from './text.js';

export interface SalaryInfo {
  readonly amount: number;
  readonly currency: 'RUB' | 'USD' | 'EUR' | 'unknown';
  readonly period: 'month' | 'year' | 'unknown';
  readonly quote: string;
}

export interface LanguageFact {
  readonly name: string;
  readonly level: string;
  readonly quote: string;
}

export interface SkillFact {
  readonly term: string;
  readonly quote: string;
}

export interface NormalizedFacts {
  readonly items: readonly CandidateFact[];
  readonly workPeriods: readonly {
    startYear: number;
    endYear: number | null;
    quote: string;
    evidenceRefs: readonly NonNullable<ReturnType<typeof makeEvidenceRef>>[];
  }[];
  readonly salary: SalaryInfo | null;
  readonly ageYears: number | null;
  readonly totalExperienceMonths: number;
  readonly name: string | null;
  readonly location: string | null;
  readonly languages: readonly LanguageFact[];
  readonly skills: readonly SkillFact[];
  readonly availability: string | null;
  readonly uncertainty: readonly string[];
}

const SKILL_VOCABULARY = [
  'node',
  'node.js',
  'typescript',
  'javascript',
  'postgresql',
  'mysql',
  'kubernetes',
  'docker',
  'react',
  'vue',
  'angular',
  'python',
  'django',
  'java',
  'spring',
  'kotlin',
  'golang',
  'rust',
  'sql',
  'mongodb',
  'redis',
  'kafka',
  'rabbitmq',
  'graphql',
  'rest',
  'grpc',
  'git',
  'linux',
  'aws',
  'gcp',
  'azure',
  'terraform',
  'ansible',
  'ci/cd',
  'excel',
  'figma',
  '1c',
];

const CURRENCY_BY_MARKER: readonly (readonly [RegExp, SalaryInfo['currency']])[] = [
  [/(руб|₽|\brub\b)/i, 'RUB'],
  [/(доллар|\busd\b|\$)/i, 'USD'],
  [/(евро|\beur\b|€)/i, 'EUR'],
];

const APPLICATION_KINDS = new Set(['resume', 'cover_letter', 'email_application', 'other']);

function applicationDocuments(documents: readonly SourceDocument[]): SourceDocument[] {
  return documents.filter((document) => APPLICATION_KINDS.has(document.inputKind));
}

function parseAmount(raw: string): number {
  return Number.parseInt(raw.replaceAll(/[^\d]/g, ''), 10);
}

function parseSalary(documents: readonly SourceDocument[]): SalaryInfo | null {
  for (const document of applicationDocuments(documents)) {
    for (const block of document.textBlocks) {
      for (const sentence of splitSentences(block.text)) {
        const folded = fold(sentence);
        if (!/(ожидани|зарплат|заработн|доход|оклад|планирую|рассчитываю)/.test(folded)) {
          continue;
        }
        const amountMatch = /(\d[\d\s]{2,})\s*(руб\w*|₽|\brub\b|доллар\w*|\busd\b|\$|евро|\beur\b|€)/i.exec(
          sentence,
        );
        if (amountMatch === null) {
          continue;
        }
        const marker = amountMatch[2] ?? '';
        const currency =
          CURRENCY_BY_MARKER.find(([pattern]) => pattern.test(marker))?.[1] ?? 'unknown';
        const period = /(в\s*год|год|annual|\/год)/i.test(sentence) ? 'year' : 'month';
        return {
          amount: parseAmount(amountMatch[1]!),
          currency,
          period,
          quote: sentence,
        };
      }
    }
  }
  return null;
}

function parseAge(text: string): { years: number; quote: string } | null {
  for (const sentence of splitSentences(text)) {
    const folded = fold(sentence);
    const explicit = /возраст[:\s]*(\d{1,2})(?!\d)/.exec(folded);
    if (explicit !== null) {
      const age = Number.parseInt(explicit[1]!, 10);
      if (age >= 14 && age <= 80) {
        return { years: age, quote: sentence };
      }
    }
    // `\b` is ASCII-only in JavaScript, so Cyrillic endings need a lookahead.
    for (const match of sentence.matchAll(/(\d{1,2})\s*(год|года|лет)(?![а-яё])/gi)) {
      const index = match.index ?? 0;
      const prefix = fold(sentence.slice(Math.max(0, index - 16), index));
      if (/(опыт|стаж|работ)/.test(prefix)) {
        continue;
      }
      const age = Number.parseInt(match[1]!, 10);
      if (age >= 14 && age <= 80) {
        return { years: age, quote: sentence };
      }
    }
  }
  return null;
}

/**
 * First line of a resume is conventionally the candidate name. Used to detect
 * packages that mix several people, not to infer identity attributes.
 */
export function parseCandidateName(text: string): string | null {
  const firstLine = normalizeText(text).split('\n')[0] ?? '';
  const match = /^([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s+([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)/.exec(
    firstLine.trim(),
  );
  if (match === null) {
    return null;
  }
  return `${match[1]} ${match[2]}`;
}

function parseName(text: string): string | null {
  return parseCandidateName(text);
}

function parseLocation(text: string): string | null {
  for (const block of text.split('\n')) {
    const labelled = /(?:локация|город|местоположение)\s*[:\-]\s*([^\n,.;]+)/i.exec(block);
    if (labelled !== null) {
      return labelled[1]!.trim();
    }
  }
  for (const sentence of splitSentences(text)) {
    if (!/(\d{1,2})\s*(?:год|года|лет)(?![а-яё])/i.test(sentence)) {
      continue;
    }
    const matches = [...sentence.matchAll(/,\s*([А-ЯЁ][а-яё-]{2,}(?:\s[А-ЯЁ][а-яё-]+)?)\s*(?:[.,]|$)/g)];
    const candidate = matches.at(-1)?.[1]?.trim();
    if (candidate !== undefined && candidate.length >= 2) {
      return candidate;
    }
  }
  return null;
}

function parseLanguageFacts(text: string): LanguageFact[] {
  const results: LanguageFact[] = [];
  const pattern =
    /(английск[а-яё]*|немецк[а-яё]*|французск[а-яё]*|испанск[а-яё]*|китайск[а-яё]*|русск[а-яё]*)\s*[—:\-–]?\s*(A1|A2|B1|B2|C1|C2|native|intermediate|upper|advanced|свободн[а-яё]*|продвинут[а-яё]*|разговорн[а-яё]*|начальн[а-яё]*)/i;
  for (const sentence of splitSentences(text)) {
    const match = pattern.exec(sentence);
    if (match === null) {
      continue;
    }
    results.push({
      name: match[1]!,
      level: match[2]!.toUpperCase(),
      quote: sentence,
    });
  }
  return results;
}

function parseSkillFacts(text: string): SkillFact[] {
  const results: SkillFact[] = [];
  const seen = new Set<string>();
  for (const term of SKILL_VOCABULARY) {
    const normalizedTerm = term === 'node.js' ? 'node' : term;
    if (seen.has(normalizedTerm)) {
      continue;
    }
    if (!containsTerm(text, normalizedTerm)) {
      continue;
    }
    const sentence = splitSentences(text).find((candidate) => containsTerm(candidate, normalizedTerm));
    if (sentence === undefined) {
      continue;
    }
    seen.add(normalizedTerm);
    results.push({ term: normalizedTerm, quote: sentence });
  }
  return results;
}

function parseAvailability(text: string): string | null {
  for (const sentence of splitSentences(text)) {
    if (/(?:^|[^а-яё])готов[а-яё]*\s+(?:выйти|начать|приступить|рассмотреть|к выходу)/i.test(sentence)) {
      return sentence;
    }
  }
  return null;
}

export function mergeExperienceMonths(
  periods: readonly { startYear: number; endYear: number | null }[],
  referenceYear: number,
): { months: number; intervals: { start: number; end: number }[] } {
  const intervals = periods
    .filter((period) => period.endYear === null || period.endYear >= period.startYear)
    .map((period) => ({
      start: period.startYear * 12,
      end: (period.endYear ?? referenceYear) * 12,
    }))
    .filter((interval) => interval.end > interval.start)
    .sort((left, right) => left.start - right.start);

  const merged: { start: number; end: number }[] = [];
  for (const interval of intervals) {
    const last = merged.at(-1);
    if (last === undefined || interval.start > last.end) {
      merged.push({ ...interval });
      continue;
    }
    merged[merged.length - 1] = { start: last.start, end: Math.max(last.end, interval.end) };
  }

  const months = merged.reduce((total, interval) => total + (interval.end - interval.start), 0);
  return { months, intervals: merged };
}

export function normalizeFacts(
  documents: readonly SourceDocument[],
  options: { now?: () => Date } = {},
): NormalizedFacts {
  const now = options.now ?? (() => new Date());
  const referenceYear = now().getUTCFullYear();
  const applicationDocs = applicationDocuments(documents);
  const applicationText = applicationDocs
    .flatMap((document) => document.textBlocks.map((block) => block.text))
    .join('\n');

  const items: CandidateFact[] = [];
  const uncertainty: string[] = [];
  const workPeriodRefs: {
    startYear: number;
    endYear: number | null;
    quote: string;
    evidenceRefs: readonly NonNullable<ReturnType<typeof makeEvidenceRef>>[];
  }[] = [];
  let factCounter = 0;

  const addFact = (
    field: FactField,
    value: string,
    quote: string | null,
    certainty: CandidateFact['certainty'] = 'stated',
  ): void => {
    factCounter += 1;
    const ref =
      quote === null ? null : makeEvidenceRef(documents, quote, applicationDocs.map((d) => d.id));
    items.push({
      id: `fact-${factCounter}`,
      field,
      value,
      certainty,
      evidenceRefs: ref === null ? [] : [ref],
    });
  };

  const name = parseName(applicationText);
  if (name !== null) {
    addFact('full_name', name, name);
  }

  const statedAge = parseAge(applicationText);
  let ageYears: number | null = null;
  if (statedAge !== null) {
    ageYears = statedAge.years;
    addFact('age', String(ageYears), statedAge.quote);
  } else {
    uncertainty.push('Возраст в отклике не указан — не угадывается.');
  }

  const location = parseLocation(applicationText);
  if (location !== null) {
    addFact('location', location, location);
  } else {
    uncertainty.push('Локация в отклике не указана.');
  }

  const salary = parseSalary(documents);
  if (salary !== null) {
    addFact('salary_expectation', `${salary.amount} ${salary.currency}`, salary.quote);
  } else {
    uncertainty.push('Зарплатные ожидания в отклике не указаны.');
  }

  const periodPattern =
    /\b(19\d{2}|20\d{2})\s*[-–—]\s*(19\d{2}|20\d{2}|наст\w*|по\s+настоящее\s+время|н\.?\s?в\.?)/gi;
  for (const document of applicationDocs) {
    for (const block of document.textBlocks) {
      for (const match of block.text.matchAll(periodPattern)) {
        const startYear = Number.parseInt(match[1]!, 10);
        const endRaw = match[2]!;
        const endYear = /^\d{4}$/.test(endRaw) ? Number.parseInt(endRaw, 10) : null;
        const quote = match[0]!;
        const ref = makeEvidenceRef(documents, quote, [document.id]);
        workPeriodRefs.push({
          startYear,
          endYear,
          quote,
          evidenceRefs: ref === null ? [] : [ref],
        });
      }
    }
  }
  const experience = mergeExperienceMonths(workPeriodRefs, referenceYear);
  const totalYears = Math.floor(experience.months / 12);
  if (experience.months > 0) {
    addFact('total_experience', `${totalYears} лет`, workPeriodRefs[0]?.quote ?? null);
  } else {
    uncertainty.push('Периоды работы в отклике не распознаны — стаж не рассчитан.');
  }
  for (const period of workPeriodRefs) {
    addFact('work_period', `${period.startYear}-${period.endYear ?? 'н.в.'}`, period.quote);
  }

  const languages = parseLanguageFacts(applicationText);
  for (const language of languages) {
    addFact('language', `${language.name} ${language.level}`, language.quote);
  }

  const skills = parseSkillFacts(applicationText);
  for (const skill of skills) {
    addFact('skill', skill.term, skill.quote);
  }

  const availability = parseAvailability(applicationText);
  if (availability !== null) {
    addFact('availability', availability, availability);
  }

  return {
    items,
    workPeriods: workPeriodRefs,
    salary,
    ageYears,
    totalExperienceMonths: experience.months,
    name,
    location,
    languages,
    skills,
    availability,
    uncertainty,
  };
}
