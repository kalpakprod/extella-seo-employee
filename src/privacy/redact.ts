import type { SourceDocument } from '../contracts/analysis.js';

/**
 * Direct identifiers that are removed before any text leaves the trusted
 * session for a model provider. Redaction is a minimization step, not an
 * anonymisation guarantee: it cannot make a resume unlinkable on its own.
 */
export type RedactionKind =
  | 'email'
  | 'phone'
  | 'messenger'
  | 'social_url'
  | 'government_id';

export interface RedactionCount {
  readonly kind: RedactionKind;
  readonly count: number;
}

export interface RedactionResult {
  readonly text: string;
  readonly total: number;
  readonly counts: readonly RedactionCount[];
}

export const MODEL_INPUT_NOTICE =
  'Перед передачей модели прямые идентификаторы (почта, телефон, мессенджеры, ссылки на профили, гос. номера) заменены метками. Это минимизация, а не гарантия анонимности резюме.';

const PLACEHOLDERS: Readonly<Record<RedactionKind, string>> = {
  email: '[почта]',
  phone: '[телефон]',
  messenger: '[мессенджер]',
  social_url: '[ссылка на профиль]',
  government_id: '[гос. номер]',
};

const PATTERNS: readonly { kind: RedactionKind; pattern: RegExp }[] = [
  { kind: 'email', pattern: /[\w.+-]+@[\w-]+\.[\w.-]+/g },
  {
    kind: 'phone',
    pattern: /(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)/g,
  },
  { kind: 'phone', pattern: /\+\d{1,3}[\s(.-]*\d{2,4}[\s).-]*\d{2,4}[\s.-]*\d{2,4}(?!\d)/g },
  { kind: 'government_id', pattern: /СНИЛС[\s:№]*\d{3}[-\s]?\d{3}[-\s]?\d{3}[\s-]?\d{2}/gi },
  { kind: 'government_id', pattern: /(?:ИНН|ОГРН|ОГРНИП|паспорт|серия|№\s*паспорта)[\s:№]*[\d\s-]{5,}/gi },
  { kind: 'social_url', pattern: /https?:\/\/(?:www\.)?(?:vk\.com|vkontakte\.ru|linkedin\.com|github\.com|hh\.ru|facebook\.com|instagram\.com|t\.me|telegram\.me)\/[^\s)»"']+/gi },
  { kind: 'messenger', pattern: /(?:t\.me|telegram\.me|wa\.me|t\.do|vk\.me)\/[\w.@+-]+/gi },
  { kind: 'messenger', pattern: /(?:telegram|телеграм|whatsapp|ватсап|viber|вайбер|skype|скайп|signal)\s*[:=-]?\s*@?[\w.@+-]{3,}/gi },
  { kind: 'messenger', pattern: /@[A-Za-z0-9_]{4,}/g },
];

/**
 * Removes direct identifiers from a single text. Deterministic and
 * order-stable so the same input always yields the same payload.
 */
export function redactForModel(text: string): RedactionResult {
  const counts = new Map<RedactionKind, number>();
  let redacted = text;
  for (const { kind, pattern } of PATTERNS) {
    redacted = redacted.replace(pattern, () => {
      counts.set(kind, (counts.get(kind) ?? 0) + 1);
      return PLACEHOLDERS[kind];
    });
  }
  const ordered = [...counts.entries()]
    .map(([kind, count]) => ({ kind, count }))
    .sort((left, right) => left.kind.localeCompare(right.kind));
  const total = ordered.reduce((sum, entry) => sum + entry.count, 0);
  return { text: redacted, total, counts: ordered };
}

export interface RedactedSources {
  /**
   * Redacted copies for the model boundary only. They must never be used as
   * evidence sources: their offsets no longer match the original documents.
   */
  readonly documents: readonly SourceDocument[];
  readonly counts: readonly RedactionCount[];
  readonly total: number;
  readonly notice: string;
}

/**
 * Builds the minimized payload handed to a model provider. The returned
 * documents are presentation-free copies; evidence refs keep pointing at the
 * original sources held in the trusted session.
 */
export function redactSourcesForModel(
  sources: readonly SourceDocument[],
): RedactedSources {
  const counts = new Map<RedactionKind, number>();
  const documents = sources.map((source) => {
    const textBlocks = source.textBlocks.map((block) => {
      const result = redactForModel(block.text);
      for (const entry of result.counts) {
        counts.set(entry.kind, (counts.get(entry.kind) ?? 0) + entry.count);
      }
      return { ...block, text: result.text };
    });
    return {
      ...source,
      displayName: redactForModel(source.displayName).text,
      textBlocks,
    };
  });
  const ordered = [...counts.entries()]
    .map(([kind, count]) => ({ kind, count }))
    .sort((left, right) => left.kind.localeCompare(right.kind));
  return {
    documents,
    counts: ordered,
    total: ordered.reduce((sum, entry) => sum + entry.count, 0),
    notice: MODEL_INPUT_NOTICE,
  };
}
