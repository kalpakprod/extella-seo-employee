import { createHash } from 'node:crypto';
import type { SourceDocument } from '../../src/contracts/analysis.js';

export function sha256(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

export function makeDocument(overrides: Partial<SourceDocument> & { text: string }): SourceDocument {
  const text = overrides.text;
  const id = overrides.id ?? 'src-1';
  const blockId = overrides.textBlocks?.[0]?.id ?? `${id}-b0`;
  return {
    id,
    kind: overrides.kind ?? 'inline_text',
    inputKind: overrides.inputKind ?? 'other',
    displayName: overrides.displayName ?? 'document.txt',
    contentHash: overrides.contentHash ?? sha256(text),
    extractionStatus: overrides.extractionStatus ?? 'extracted',
    textBlocks: overrides.textBlocks ?? [{ id: blockId, text }],
    warnings: overrides.warnings ?? [],
    ...(overrides.mediaType === undefined ? {} : { mediaType: overrides.mediaType }),
    ...(overrides.language === undefined ? {} : { language: overrides.language }),
  };
}

export const VACANCY_TEXT = [
  'Роль: Senior Backend Engineer',
  'Задачи: проектировать сервисы на Node.js, развивать архитектуру.',
  'Must-have: опыт backend от 5 лет, Node.js, TypeScript, английский B2.',
  'Nice-to-have: PostgreSQL, Kubernetes.',
  'Локация: Москва.',
  'Формат: удалённо.',
  'Вилка: 300 000 - 350 000 руб.',
  'Пожелания HR: хотим возраст 30-40 лет, стаж от 5 лет, деньги до 350 000 руб.',
].join('\n');

export const RESUME_TEXT = [
  'Иван Петров, 34 года, Москва.',
  'Опыт работы:',
  '2016-2021 ООО Ромашка, backend-разработчик, Node.js, TypeScript, PostgreSQL.',
  '2021-2026 ООО Лютик, senior backend, Node.js, TypeScript, Kubernetes.',
  'Английский B2.',
  'Ожидания по зарплате: 320 000 руб.',
].join('\n');

export const COVER_LETTER_TEXT = [
  'Здравствуйте! Откликаюсь на вакансию Senior Backend Engineer.',
  'За время работы я спроектировал 12 сервисов и ускорил выдачу на 40%.',
  'Готов выйти на работу с 1 октября.',
].join('\n');

export const SYNTHETIC_SOURCES: readonly SourceDocument[] = [
  makeDocument({ id: 'vac-1', inputKind: 'vacancy', displayName: 'vacancy.txt', text: VACANCY_TEXT }),
  makeDocument({
    id: 'res-1',
    inputKind: 'resume',
    displayName: 'resume.txt',
    text: RESUME_TEXT,
  }),
  makeDocument({
    id: 'cov-1',
    inputKind: 'cover_letter',
    displayName: 'cover.txt',
    text: COVER_LETTER_TEXT,
  }),
];

export const OVERLAP_RESUME_TEXT = [
  'Мария Сидорова, 29 лет.',
  '2018-2024 ООО Альфа, аналитик.',
  '2020-2022 ООО Бета, ведущий аналитик.',
  '2024-2020 ООО Гамма, консультант.',
].join('\n');
