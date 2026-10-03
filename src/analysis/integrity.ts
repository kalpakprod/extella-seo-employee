import type { ReviewSignal, SourceDocument } from '../contracts/analysis.js';
import type { NormalizedFacts } from './facts.js';
import { mergeExperienceMonths } from './facts.js';
import { makeEvidenceRef } from './evidence.js';
import { splitSentences } from './text.js';

const ACHIEVEMENT_VERB =
  /(увеличил\w*|ускорил\w*|выросл\w*|сократил\w*|сэкономил\w*|оптимизировал\w*|улучшил\w*|спроектировал\w*|запустил\w*|привлек\w*)/i;

function dedupeRefs<T extends { sourceId: string; blockId: string; start: number; end: number }>(
  refs: readonly T[],
): T[] {
  const seen = new Set<string>();
  return refs.filter((ref) => {
    const key = `${ref.sourceId}:${ref.blockId}:${ref.start}:${ref.end}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

export function detectInconsistencies(
  facts: NormalizedFacts,
  sources: readonly SourceDocument[],
  options: { now?: () => Date } = {},
): ReviewSignal[] {
  const now = options.now ?? (() => new Date());
  const referenceYear = now().getUTCFullYear();
  const signals: ReviewSignal[] = [];
  let counter = 0;
  const nextId = (): string => `signal-${(counter += 1)}`;

  for (const period of facts.workPeriods) {
    if (period.endYear !== null && period.endYear < period.startYear) {
      signals.push({
        id: nextId(),
        kind: 'impossible_date',
        observation: `Период «${period.quote}» заканчивается раньше, чем начинается.`,
        evidenceRefs: period.evidenceRefs,
        alternativeExplanation: 'Возможна опечатка в датах или нестандартный формат записи.',
        interviewQuestion: `Уточните период «${period.quote}»: какие годы начала и окончания верны?`,
        severity: 'attention',
      });
    }
    if (period.endYear !== null && period.endYear > referenceYear) {
      signals.push({
        id: nextId(),
        kind: 'impossible_date',
        observation: `Период «${period.quote}» заканчивается в будущем (${referenceYear} год).`,
        evidenceRefs: period.evidenceRefs,
        alternativeExplanation: 'Возможно, указан планируемый год окончания или опечатка.',
        interviewQuestion: `Поясните, пожалуйста, дату окончания в «${period.quote}».`,
        severity: 'attention',
      });
    }
  }

  const concretePeriods = facts.workPeriods
    .filter((period) => period.endYear === null || period.endYear >= period.startYear)
    .map((period) => ({ startYear: period.startYear, endYear: period.endYear }));
  for (let index = 0; index < concretePeriods.length; index += 1) {
    for (let other = index + 1; other < concretePeriods.length; other += 1) {
      const left = concretePeriods[index]!;
      const right = concretePeriods[other]!;
      const leftStart = left.startYear;
      const leftEnd = left.endYear ?? referenceYear;
      const rightStart = right.startYear;
      const rightEnd = right.endYear ?? referenceYear;
      const overlaps = leftStart < rightEnd && rightStart < leftEnd;
      if (!overlaps) {
        continue;
      }
      const refs = dedupeRefs([
        ...(facts.workPeriods[index]?.evidenceRefs ?? []),
        ...(facts.workPeriods[other]?.evidenceRefs ?? []),
      ]);
      signals.push({
        id: nextId(),
        kind: 'period_overlap',
        observation: `Периоды ${leftStart}–${leftEnd} и ${rightStart}–${rightEnd} пересекаются.`,
        evidenceRefs: refs,
        alternativeExplanation:
          'Совмещение допустимо при параллельной работе, проектной занятости или фрилансе.',
        interviewQuestion:
          'Расскажите, как вы совмещали эти роли: были ли они параллельными и на какую ставку?',
        severity: 'info',
      });
    }
  }

  const merged = mergeExperienceMonths(concretePeriods, referenceYear).intervals;
  for (let index = 1; index < merged.length; index += 1) {
    const gapMonths = merged[index]!.start - merged[index - 1]!.end;
    if (gapMonths <= 12) {
      continue;
    }
    const gapStart = merged[index]!.start;
    const laterPeriod =
      facts.workPeriods
        .filter((period) => period.startYear * 12 <= gapStart)
        .sort((left, right) => right.startYear - left.startYear)[0] ?? facts.workPeriods[index];
    if (laterPeriod === undefined || laterPeriod.evidenceRefs.length === 0) {
      continue;
    }
    signals.push({
      id: nextId(),
      kind: 'employment_gap',
      observation: `Перерыв около ${Math.round(gapMonths / 12)} лет между периодами работы.`,
      evidenceRefs: laterPeriod.evidenceRefs,
      alternativeExplanation:
        'Перерыв может быть связан с обучением, уходом за ребёнком, здоровьем или осознанной паузой — сам по себе он не является проблемой.',
      interviewQuestion: 'Чем был заполнен перерыв между местами работы и почему он возник?',
      severity: 'info',
    });
  }

  for (const source of sources) {
    if (!['resume', 'cover_letter', 'email_application', 'other'].includes(source.inputKind)) {
      continue;
    }
    for (const block of source.textBlocks) {
      for (const sentence of splitSentences(block.text)) {
        if (!ACHIEVEMENT_VERB.test(sentence) || !/\d/.test(sentence)) {
          continue;
        }
        const ref = makeEvidenceRef(sources, sentence, [source.id]);
        if (ref === null) {
          continue;
        }
        signals.push({
          id: nextId(),
          kind: 'unverified_claim',
          observation: `Заявлен измеримый результат без подтверждения: «${sentence}».`,
          evidenceRefs: [ref],
          alternativeExplanation: 'Результат может быть достоверным, но требует подтверждения на интервью.',
          interviewQuestion:
            'Как вы измеряли этот результат, с какой базой сравнивали и кто может его подтвердить?',
          severity: 'info',
        });
      }
    }
  }

  return signals;
}
