const RU_STOPWORDS = new Set([
  'и',
  'в',
  'во',
  'на',
  'от',
  'до',
  'по',
  'с',
  'со',
  'для',
  'или',
  'а',
  'но',
  'не',
  'что',
  'как',
  'это',
  'же',
  'ли',
  'у',
  'о',
  'об',
  'про',
  'при',
  'из',
  'за',
  'над',
  'под',
  'без',
  'лет',
  'год',
  'года',
  'году',
  'опыт',
  'опыта',
  'работы',
  'работа',
  'знание',
  'знания',
  'умение',
  'умения',
  'владение',
  'уровень',
  'навыки',
  'навык',
  'роль',
  'задачи',
  'формат',
  'локация',
  'вилка',
  'пожелания',
  'hr',
  'хотим',
  'нужно',
  'требуется',
  'минимум',
  'более',
  'менее',
]);

const EN_STOPWORDS = new Set([
  'and',
  'or',
  'the',
  'a',
  'an',
  'of',
  'to',
  'in',
  'on',
  'for',
  'with',
  'at',
  'by',
  'from',
  'is',
  'are',
  'be',
  'as',
  'experience',
  'years',
  'year',
  'skills',
  'skill',
  'knowledge',
  'role',
  'tasks',
  'requirements',
  'must',
  'have',
  'nice',
  'plus',
  'at',
  'least',
  'more',
  'than',
]);

/** Fold ё→е and lowercase so that lexical comparisons are stable. */
export function fold(text: string): string {
  return text.toLowerCase().replaceAll('ё', 'е');
}

/** Collapse horizontal whitespace and normalise line endings. */
export function normalizeText(text: string): string {
  return text.replaceAll('\r\n', '\n').replaceAll('\r', '\n').replace(/[ \t]+/g, ' ').trim();
}

/** Split a block into sentences, preserving order and trimming each sentence. */
export function splitSentences(text: string): string[] {
  const normalized = normalizeText(text);
  const parts = normalized
    .split(/(?<=[.!?…])\s+|\n+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  return parts;
}

export function tokenize(text: string): string[] {
  return fold(text)
    .split(/[^\p{L}\p{N}]+/u)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2)
    .filter((token) => !RU_STOPWORDS.has(token) && !EN_STOPWORDS.has(token))
    .filter((token) => !/^\d+$/.test(token));
}

/** True when `term` occurs in `haystack` on a token boundary. */
export function containsTerm(haystack: string, term: string): boolean {
  const foldedHaystack = fold(haystack);
  const foldedTerm = fold(term);
  if (foldedTerm.length === 0) {
    return false;
  }
  const escaped = foldedTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`(^|[^\\p{L}\\p{N}])${escaped}([^\\p{L}\\p{N}]|$)`, 'u');
  return pattern.test(foldedHaystack);
}

/**
 * Locate an exact quote inside a text block. Returns offsets relative to the
 * block text, or null when the quote is not present verbatim.
 */
export function locateQuote(
  blockText: string,
  quote: string,
): { start: number; end: number } | null {
  if (quote.length === 0) {
    return null;
  }
  const direct = blockText.indexOf(quote);
  if (direct >= 0) {
    return { start: direct, end: direct + quote.length };
  }
  const foldedBlock = fold(blockText);
  const foldedQuote = fold(quote);
  const foldedIndex = foldedBlock.indexOf(foldedQuote);
  if (foldedIndex >= 0 && foldedQuote.length === quote.length) {
    return { start: foldedIndex, end: foldedIndex + quote.length };
  }
  const collapsed = normalizeTextWithOffsets(blockText);
  const collapsedIndex = fold(collapsed.text).indexOf(fold(normalizeText(quote)));
  if (collapsedIndex >= 0) {
    const collapsedEnd = collapsedIndex + normalizeText(quote).length;
    const start = collapsed.starts[collapsedIndex];
    const end = collapsed.ends[collapsedEnd - 1];
    if (start !== undefined && end !== undefined) {
      return { start, end };
    }
  }
  return null;
}

interface NormalizedTextOffsets {
  readonly text: string;
  readonly starts: readonly number[];
  readonly ends: readonly number[];
}

/** Normalizes text while retaining offsets into the original block. */
function normalizeTextWithOffsets(text: string): NormalizedTextOffsets {
  const characters: string[] = [];
  const starts: number[] = [];
  const ends: number[] = [];

  for (let index = 0; index < text.length;) {
    const start = index;
    const character = text[index]!;
    if (character === '\r') {
      index += text[index + 1] === '\n' ? 2 : 1;
      characters.push('\n');
    } else if (character === ' ' || character === '\t') {
      do {
        index += 1;
      } while (text[index] === ' ' || text[index] === '\t');
      characters.push(' ');
    } else {
      index += 1;
      characters.push(character);
    }
    starts.push(start);
    ends.push(index);
  }

  let first = 0;
  let last = characters.length;
  while (first < last && /\s/u.test(characters[first]!)) first += 1;
  while (last > first && /\s/u.test(characters[last - 1]!)) last -= 1;
  return {
    text: characters.slice(first, last).join(''),
    starts: starts.slice(first, last),
    ends: ends.slice(first, last),
  };
}

/** First sentence containing `needle`, used to build evidence quotes. */
export function sentenceContaining(text: string, needle: string): string | null {
  const foldedNeedle = fold(needle);
  for (const sentence of splitSentences(text)) {
    if (fold(sentence).includes(foldedNeedle)) {
      return sentence;
    }
  }
  return null;
}

export function truncate(text: string, maxLength = 240): string {
  const normalized = normalizeText(text);
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
}
