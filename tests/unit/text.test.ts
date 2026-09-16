import { describe, expect, it } from 'vitest';
import {
  containsTerm,
  fold,
  locateQuote,
  normalizeText,
  splitSentences,
  tokenize,
  truncate,
} from '../../src/analysis/text.js';

describe('text utilities', () => {
  it('folds ё to е and lowercases', () => {
    expect(fold('Всё ЁЖ')).toBe('все еж');
  });

  it('normalises whitespace but keeps paragraphs', () => {
    expect(normalizeText('a\t b\r\nc  d')).toBe('a b\nc d');
  });

  it('splits sentences on punctuation and newlines', () => {
    expect(splitSentences('Первое. Второе!\nТретье?')).toEqual([
      'Первое.',
      'Второе!',
      'Третье?',
    ]);
  });

  it('tokenizes dropping stopwords and bare numbers', () => {
    expect(tokenize('Опыт backend от 5 лет, Node.js')).toEqual(['backend', 'node', 'js']);
  });

  it('matches terms on token boundaries, not substrings', () => {
    expect(containsTerm('ООО Ромашка, backend-разработчик', 'backend')).toBe(true);
    expect(containsTerm('Начальник', 'чал')).toBe(false);
  });

  it('locates an exact quote and returns offsets', () => {
    const text = 'Иван Петров, 34 года, Москва.';
    expect(locateQuote(text, '34 года')).toEqual({ start: 13, end: 20 });
    expect(locateQuote(text, 'нет такого')).toBeNull();
  });

  it('locates a quote with different casing', () => {
    const text = 'Английский B2.';
    expect(locateQuote(text, 'английский b2')).toEqual({ start: 0, end: 13 });
  });

  it('returns original offsets when normalized whitespace is used to find a quote', () => {
    const text = 'Must-have:\tNode.js   и\r\nTypeScript';
    const quote = 'Node.js и\nTypeScript';
    const location = locateQuote(text, quote);
    expect(location).not.toBeNull();
    expect(normalizeText(text.slice(location!.start, location!.end))).toBe(quote);
  });

  it('truncates long text with an ellipsis', () => {
    expect(truncate('abcdefghij', 5)).toBe('abcd…');
  });
});
