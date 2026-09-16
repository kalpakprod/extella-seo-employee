import type {
  JobRequirement,
  RequirementCategory,
  RequirementEligibility,
  RequirementPriority,
  SourceDocument,
} from '../contracts/analysis.js';
import { makeEvidenceRef } from './evidence.js';
import { normalizeText } from './text.js';

const PROTECTED_PATTERN =
  /(возраст|национальн|гражданств|семейн\w*\s+положен|религи|вероисповедан|инвалид|этнич|рас[ауы]|мужчин|женщин|девушк|(?<![а-яё])пол[ау]?(?![а-яё])|\bage\b|\bgender\b|\bnationality\b|\bcitizenship\b|\breligion\b|\bdisability\b|\brace\b)/i;

interface LabeledValue {
  readonly label: string;
  readonly value: string;
}

function joinContinuations(lines: readonly string[]): LabeledValue[] {
  const entries: LabeledValue[] = [];
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (line.length === 0) {
      continue;
    }
    const match = /^([A-Za-zА-Яа-яЁё][^:]{0,40}?)\s*:\s*(.*)$/.exec(line);
    if (match !== null) {
      entries.push({ label: match[1]!.trim(), value: match[2]!.trim() });
      continue;
    }
    const previous = entries.at(-1);
    if (previous !== undefined) {
      entries[entries.length - 1] = {
        label: previous.label,
        value: `${previous.value} ${line}`.trim(),
      };
    }
  }
  return entries;
}

type LabelKind = 'role' | 'must' | 'nice' | 'tasks' | 'location' | 'format' | 'compensation' | 'preferences' | 'other';

function classifyLabel(label: string): LabelKind {
  const folded = label.toLowerCase();
  if (/(роль|должность|позиция|вакансия)/.test(folded)) return 'role';
  if (/(must[\s-]?have|обязател|требовани)/.test(folded)) return 'must';
  if (/(nice[\s-]?to[\s-]?have|желательн|будет\s+плюсом|приветству)/.test(folded)) return 'nice';
  if (/(задач|обязанност)/.test(folded)) return 'tasks';
  if (/(локац|город|местополож)/.test(folded)) return 'location';
  if (/(формат|график|удален|офис)/.test(folded)) return 'format';
  if (/(вилк|зарплат|оклад|доход|деньг)/.test(folded)) return 'compensation';
  if (/(пожелани|хотим|ожидани)/.test(folded)) return 'preferences';
  return 'other';
}

function categoryForText(text: string, fallback: RequirementCategory): RequirementCategory {
  const folded = text.toLowerCase();
  if (/(руб|₽|rub|доллар|\$|евро|€)/.test(folded)) return 'compensation';
  if (/(локац|город|москв|питер|санкт|регион)/.test(folded)) return 'location';
  if (/(удален|офис|гибрид|формат|смен)/.test(folded)) return 'format';
  if (/(англ|немец|француз|испан|китайск|язык)/.test(folded)) return 'language';
  if (/(образован|вуз|университет|институт|диплом)/.test(folded)) return 'education';
  if (/(опыт|стаж|\d+\s*(?:год|года|лет))/.test(folded)) return 'experience';
  return fallback;
}

function splitItems(value: string): string[] {
  return value
    .split(/[,;•·]|\s+—\s+/)
    .map((item) => item.trim().replace(/^[-–—*]\s*/, '').replace(/[.;]+$/, '').trim())
    .filter((item) => item.length >= 2)
    .filter((item) => !/^(хотим|нужно|требуется|минимум|плюсом)$/i.test(item));
}

function eligibilityForText(text: string): RequirementEligibility {
  if (PROTECTED_PATTERN.test(text)) {
    return 'excluded_protected';
  }
  const ageRange = /(?:^|\D)(\d{2})\s*(?:[-–—]\s*(\d{2})\s*)?(?:год|года|лет)(?![а-яё])/i.exec(text);
  if (ageRange !== null && !/(опыт|стаж|работ)/i.test(text)) {
    const ages = [ageRange[1], ageRange[2]]
      .filter((value): value is string => value !== undefined)
      .map((value) => Number.parseInt(value, 10));
    if (ages.every((age) => age >= 14 && age <= 80)) {
      return 'excluded_protected';
    }
  }
  return 'professional';
}

/**
 * Parses a labelled vacancy description into explicit requirements. Only
 * labelled input is interpreted; free-form guessing is deliberately avoided.
 */
export function parseJob(documents: readonly SourceDocument[]): JobRequirement[] {
  const vacancyDocuments = documents.filter((document) => document.inputKind === 'vacancy');
  const requirements: JobRequirement[] = [];
  let counter = 0;

  const push = (
    text: string,
    category: RequirementCategory,
    priority: RequirementPriority,
  ): void => {
    const clean = text.trim();
    if (clean.length < 2) {
      return;
    }
    counter += 1;
    const eligibility = eligibilityForText(clean);
    const ref = makeEvidenceRef(vacancyDocuments, clean);
    requirements.push({
      id: `job-${counter}`,
      text: clean,
      category: eligibility === 'excluded_protected' ? 'other' : category,
      priority,
      eligibility,
      evidenceRefs: ref === null ? [] : [ref],
    });
  };

  for (const document of vacancyDocuments) {
    const lines = document.textBlocks.flatMap((block) =>
      normalizeText(block.text).split('\n').map((line) => line),
    );
    for (const entry of joinContinuations(lines)) {
      const kind = classifyLabel(entry.label);
      if (kind === 'role') {
        continue;
      }
      if (kind === 'preferences') {
        for (const item of splitItems(entry.value)) {
          if (eligibilityForText(item) === 'excluded_protected') {
            push(item, 'other', 'nice_to_have');
            continue;
          }
          const category = categoryForText(item, 'other');
          push(item, category, 'nice_to_have');
        }
        continue;
      }
      if (kind === 'tasks') {
        for (const item of splitItems(entry.value)) {
          push(item, 'other', 'nice_to_have');
        }
        continue;
      }
      const priority: RequirementPriority =
        kind === 'must' ? 'must_have' : 'nice_to_have';
      const fallback: RequirementCategory =
        kind === 'location'
          ? 'location'
          : kind === 'format'
            ? 'format'
            : kind === 'compensation'
              ? 'compensation'
              : 'skill';
      const items =
        kind === 'must' || kind === 'nice'
          ? splitItems(entry.value)
          : [entry.value.replace(/[.;,\s]+$/, '').trim()];
      for (const item of items) {
        if (item.length < 2) {
          continue;
        }
        push(item, categoryForText(item, fallback), priority);
      }
    }
  }

  return requirements;
}

export function parseVacancyRole(documents: readonly SourceDocument[]): string | null {
  const vacancyDocuments = documents.filter((document) => document.inputKind === 'vacancy');
  for (const document of vacancyDocuments) {
    for (const block of document.textBlocks) {
      for (const entry of joinContinuations(normalizeText(block.text).split('\n'))) {
        if (classifyLabel(entry.label) === 'role' && entry.value.length > 0) {
          return entry.value;
        }
      }
    }
  }
  return null;
}
