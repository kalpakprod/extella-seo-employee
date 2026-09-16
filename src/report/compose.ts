import type {
  AiPatternReview,
  CandidateFact,
  CandidateReport,
  EvidenceRef,
  FeatureNote,
  InterviewQuestion,
  JobRequirement,
  RequirementMatch,
  ReviewSignal,
  Scorecard,
  SourceDocument,
} from '../contracts/analysis.js';
import { SCHEMA_VERSION } from '../contracts/analysis.js';
import type { NormalizedFacts } from '../analysis/facts.js';
import { parseSalaryRange } from '../analysis/matching.js';

export interface ComposeInput {
  readonly requestId: string;
  readonly sources: readonly SourceDocument[];
  readonly requirements: readonly JobRequirement[];
  readonly matches: readonly RequirementMatch[];
  readonly facts: NormalizedFacts;
  readonly signals: readonly ReviewSignal[];
  readonly aiCheck: AiPatternReview;
  readonly scorecard: Scorecard;
  readonly role: string | null;
  readonly sourceLimitations: readonly string[];
}

function verdictFor(scorecard: Scorecard): string {
  if (scorecard.insufficientData || scorecard.overall === null) {
    return 'Недостаточно данных для оценки соответствия.';
  }
  switch (scorecard.overall) {
    case 5:
      return 'Смотреть срочно.';
    case 4:
      return 'Смотреть, есть мелкие вопросы.';
    case 3:
      return 'Решение после ответов на вопросы.';
    case 2:
      return 'Слабый отклик, нужны сильные аргументы.';
    default:
      return 'Не подходит — причины перечислены ниже.';
  }
}

function factByField(facts: NormalizedFacts, field: CandidateFact['field']): CandidateFact | null {
  return facts.items.find((item) => item.field === field) ?? null;
}

function agePreference(requirements: readonly JobRequirement[]): { text: string; min: number | null; max: number | null; refs: EvidenceRef[] } | null {
  const requirement = requirements.find(
    (candidate) =>
      candidate.eligibility === 'excluded_protected' &&
      (/возраст|\bage\b/i.test(candidate.text) ||
        (/\d{2}\s*(?:[-–—]\s*\d{2}\s*)?(?:год|года|лет)/i.test(candidate.text) &&
          !/(опыт|стаж|работ)/i.test(candidate.text))),
  );
  if (requirement === undefined) {
    return null;
  }
  const numbers = [...requirement.text.matchAll(/(\d{1,2})/g)].map((match) =>
    Number.parseInt(match[1]!, 10),
  );
  return {
    text: requirement.text,
    min: numbers.length >= 2 ? Math.min(numbers[0]!, numbers[1]!) : null,
    max: numbers.length >= 2 ? Math.max(numbers[0]!, numbers[1]!) : numbers[0] ?? null,
    refs: [...requirement.evidenceRefs],
  };
}

function compensationPreference(requirements: readonly JobRequirement[]): {
  min: number | null;
  max: number | null;
  refs: EvidenceRef[];
} | null {
  const requirement = requirements.find(
    (candidate) =>
      candidate.eligibility === 'professional' && candidate.category === 'compensation',
  );
  if (requirement === undefined) {
    return null;
  }
  const range = parseSalaryRange(requirement.text);
  if (range === null) {
    return null;
  }
  return { min: range.min, max: range.max, refs: [...requirement.evidenceRefs] };
}

export function composeReport(input: ComposeInput): CandidateReport {
  const { matches, requirements, facts, scorecard, signals, aiCheck } = input;
  const requirementById = new Map(requirements.map((requirement) => [requirement.id, requirement]));

  const strengths: FeatureNote[] = [];
  const gaps: FeatureNote[] = [];
  for (const match of matches) {
    const requirement = requirementById.get(match.requirementId);
    if (requirement === undefined || requirement.eligibility !== 'professional') {
      continue;
    }
    if (match.status === 'matched' && match.evidenceRefs.length > 0) {
      strengths.push({
        label: requirement.text,
        detail: match.explanation,
        evidenceRefs: [...match.evidenceRefs],
      });
    } else if ((match.status === 'partial' || match.status === 'missing') && match.evidenceRefs.length > 0) {
      gaps.push({
        label: requirement.text,
        detail: match.explanation,
        evidenceRefs: [...match.evidenceRefs],
      });
    }
  }

  const greenFlags: FeatureNote[] = [];
  const periodFacts = facts.items.filter((item) => item.field === 'work_period');
  if (periodFacts.length >= 2) {
    const firstRefs = periodFacts[0]!.evidenceRefs;
    if (firstRefs.length > 0) {
      greenFlags.push({
        label: 'Опыт на нескольких местах работы',
        detail: `В отклике указано ${periodFacts.length} периодов занятости.`,
        evidenceRefs: [...firstRefs],
      });
    }
  }
  if (facts.skills.length >= 4) {
    const skillRefs = facts.items
      .filter((item) => item.field === 'skill')
      .flatMap((item) => [...item.evidenceRefs]);
    const uniqueSkillRefs = skillRefs.filter(
      (ref, index) =>
        skillRefs.findIndex(
          (other) => other.sourceId === ref.sourceId && other.start === ref.start,
        ) === index,
    );
    if (uniqueSkillRefs.length > 0) {
      greenFlags.push({
        label: 'Релевантный стек',
        detail: `Подтверждены навыки: ${facts.skills.map((skill) => skill.term).join(', ')}.`,
        evidenceRefs: uniqueSkillRefs.slice(0, 3),
      });
    }
  }
  const achievement = input.sources
    .filter((source) => source.inputKind === 'cover_letter' || source.inputKind === 'resume')
    .flatMap((source) => source.textBlocks.flatMap((block) => block.text.split(/(?<=[.!?])\s+|\n/)))
    .find(
      (sentence) =>
        /\d/.test(sentence) &&
        /(ускорил|увеличил|выросл|сократил|сэкономил|оптимизировал|спроектировал)/i.test(sentence),
    );
  if (achievement !== undefined) {
    const ref = input.sources
      .flatMap((source) => source.textBlocks.map((block) => ({ source, block })))
      .map(({ source, block }) => {
        const start = block.text.indexOf(achievement.trim());
        if (start < 0) {
          return null;
        }
        const quote = achievement.trim();
        return {
          sourceId: source.id,
          blockId: block.id,
          ...(block.page === undefined ? {} : { page: block.page }),
          start,
          end: start + quote.length,
          quote,
        } satisfies EvidenceRef;
      })
      .find((candidate): candidate is EvidenceRef => candidate !== null);
    if (ref !== undefined) {
      greenFlags.push({
        label: 'Конкретика с цифрами',
        detail: 'В материалах есть измеримый результат (подлежит проверке на интервью).',
        evidenceRefs: [ref],
      });
    }
  }

  const features: FeatureNote[] = [];
  const age = factByField(facts, 'age');
  const preference = agePreference(requirements);
  if (age !== null && age.evidenceRefs.length > 0) {
    let detail = `Кандидат указал возраст: ${age.value}.`;
    if (preference !== null && facts.ageYears !== null) {
      const relation =
        preference.min !== null && facts.ageYears < preference.min
          ? 'младше пожелания HR'
          : preference.max !== null && facts.ageYears > preference.max
            ? 'старше пожелания HR'
            : 'совпадает с пожеланием HR';
      detail = `${detail} Пожелание HR: «${preference.text}» — ${relation}. Возраст не влияет на оценку.`;
    }
    features.push({
      label: 'Возраст',
      detail,
      evidenceRefs: [...age.evidenceRefs, ...(preference?.refs ?? [])],
    });
  }

  const experience = factByField(facts, 'total_experience');
  if (experience !== null && experience.evidenceRefs.length > 0) {
    features.push({
      label: 'Стаж по периодам',
      detail: `Распознанный стаж: ${experience.value} (по объединению периодов, без двойного учёта).`,
      evidenceRefs: [...experience.evidenceRefs],
    });
  }

  const salary = factByField(facts, 'salary_expectation');
  if (salary !== null && salary.evidenceRefs.length > 0) {
    const preferenceRange = compensationPreference(requirements);
    let detail = `Ожидания кандидата: ${salary.value}.`;
    if (preferenceRange !== null) {
      const rangeText = `${preferenceRange.min ?? ''}${preferenceRange.min !== null ? '–' : ''}${preferenceRange.max ?? ''}`;
      detail = `${detail} Вилка вакансии: ${rangeText}. Сравнение — факт, решение за HR.`;
    }
    features.push({
      label: 'Ожидания по зарплате',
      detail,
      evidenceRefs: [...salary.evidenceRefs, ...(preferenceRange?.refs ?? [])],
    });
  }

  const location = factByField(facts, 'location');
  if (location !== null && location.evidenceRefs.length > 0) {
    features.push({
      label: 'Локация',
      detail: `Указана в отклике: ${location.value}.`,
      evidenceRefs: [...location.evidenceRefs],
    });
  }

  const languageFacts = facts.items.filter((item) => item.field === 'language');
  if (languageFacts.length > 0 && languageFacts[0]!.evidenceRefs.length > 0) {
    features.push({
      label: 'Языки',
      detail: `Указано: ${languageFacts.map((item) => item.value).join('; ')}.`,
      evidenceRefs: languageFacts.flatMap((item) => [...item.evidenceRefs]),
    });
  }

  const questions: InterviewQuestion[] = [];
  const pushQuestion = (
    question: string,
    source: InterviewQuestion['source'],
    refs: readonly EvidenceRef[],
  ): void => {
    if (refs.length === 0) {
      return;
    }
    if (questions.some((candidate) => candidate.question === question)) {
      return;
    }
    questions.push({
      id: `q-${questions.length + 1}`,
      question,
      source,
      evidenceRefs: [...refs],
    });
  };

  for (const signal of signals) {
    pushQuestion(signal.interviewQuestion, 'signal', signal.evidenceRefs);
  }
  const orderedMatches = [...matches].sort((left, right) => {
    const leftRequirement = requirementById.get(left.requirementId);
    const rightRequirement = requirementById.get(right.requirementId);
    const leftPriority = leftRequirement?.priority === 'must_have' ? 0 : 1;
    const rightPriority = rightRequirement?.priority === 'must_have' ? 0 : 1;
    return leftPriority - rightPriority;
  });
  for (const match of orderedMatches) {
    const requirement = requirementById.get(match.requirementId);
    if (requirement === undefined || requirement.eligibility !== 'professional') {
      continue;
    }
    if (match.status === 'unknown') {
      pushQuestion(
        `Уточните на интервью: «${requirement.text}» в отклике не подтверждено. Что именно вы делали по этой части?`,
        'requirement',
        requirement.evidenceRefs,
      );
    } else if (match.status === 'partial') {
      pushQuestion(
        `По требованию «${requirement.text}» подтверждена только часть. Приведите конкретный пример из практики.`,
        'requirement',
        match.evidenceRefs.length > 0 ? match.evidenceRefs : requirement.evidenceRefs,
      );
    }
  }
  for (const fact of periodFacts.slice(0, 1)) {
    pushQuestion(
      'Опишите самый крупный проект из последнего места работы: ваша роль, масштаб и результат.',
      'fact',
      fact.evidenceRefs,
    );
  }
  const availability = factByField(facts, 'availability');
  if (availability !== null) {
    pushQuestion('Когда вы готовы приступить и есть ли ограничения по графику?', 'fact', availability.evidenceRefs);
  }
  if (salary !== null) {
    pushQuestion('Обсуждаемы ли условия по зарплате и от чего они зависят?', 'fact', salary.evidenceRefs);
  }
  for (const fact of facts.items.filter((item) => item.field === 'skill').slice(0, 1)) {
    pushQuestion('Приведите пример задачи, где вы применяли ключевой для вакансии навык.', 'fact', fact.evidenceRefs);
  }

  const limitations = new Set<string>(input.sourceLimitations);
  for (const item of facts.uncertainty) {
    limitations.add(item);
  }
  for (const item of aiCheck.limitations) {
    limitations.add(item);
  }
  if (input.requirements.length === 0) {
    limitations.add('Вакансия не передана: соответствие профессиональным требованиям не оценивалось.');
  }
  if (salary !== null) {
    limitations.add('Рыночная оценка зарплаты не выполнялась: данных о рынке нет.');
  }
  limitations.add(
    'Возраст, пол, национальность и другие защищённые признаки не влияют на оценку и рекомендации.',
  );
  limitations.add('Разбор носит рекомендательный характер; финальное решение принимает HR.');

  return {
    schemaVersion: SCHEMA_VERSION,
    requestId: input.requestId,
    header: {
      name: facts.name,
      role: input.role,
      verdict: verdictFor(scorecard),
    },
    matches: [...matches],
    strengths,
    gaps,
    redFlags: [...signals],
    greenFlags,
    features,
    aiCheck,
    scorecard,
    questions: questions.slice(0, 8),
    limitations: [...limitations],
  };
}
