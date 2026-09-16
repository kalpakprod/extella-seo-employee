import { createHash } from 'node:crypto';
import type {
  ExtractionStatus,
  RawInput,
  SourceDocument,
  TextBlock,
} from '../contracts/analysis.js';
import {
  INPUT_LIMITS,
  decodedByteLength,
  isSupportedMediaType,
  mediaTypeOf,
  type InputLimits,
} from './validate-input.js';

export interface ExtractedDocumentText {
  readonly blocks: readonly TextBlock[];
  readonly warnings?: readonly string[];
}

/**
 * Platform-agnostic document reader. PDF/DOCX parsing libraries are chosen at
 * the integration step; the pipeline only depends on this port so a missing or
 * failing parser degrades to an explicit extraction error.
 */
export interface DocumentExtractorPort {
  supports(mediaType: string): boolean;
  extract(input: RawInput, options: { readonly maxBytes: number }): Promise<ExtractedDocumentText>;
}

export interface ExtractDocumentOptions {
  readonly extractor?: DocumentExtractorPort;
  readonly limits?: InputLimits;
}

export function sha256Hex(value: string | Uint8Array): string {
  return createHash('sha256').update(value).digest('hex');
}

/** Rough language check: enough to warn about a mismatch, never to decide alone. */
export function detectLanguage(text: string): string | undefined {
  const cyrillic = (text.match(/[а-яё]/gi) ?? []).length;
  const latin = (text.match(/[a-z]/gi) ?? []).length;
  if (cyrillic === 0 && latin === 0) {
    return undefined;
  }
  if (cyrillic >= latin * 2) {
    return 'ru';
  }
  if (latin >= cyrillic * 2) {
    return 'en';
  }
  return 'mixed';
}

/**
 * Splits decoded text into paragraph blocks. Offsets inside an evidence quote
 * are always relative to the block text, so blocks can be re-chunked freely.
 */
export function splitTextBlocks(text: string, idPrefix: string, page?: number): TextBlock[] {
  const normalized = text.replace(/\r\n?/g, '\n').trim();
  if (normalized.length === 0) {
    return [];
  }
  const paragraphs = normalized
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0);
  const source = paragraphs.length > 0 ? paragraphs : [normalized];
  return source.map((paragraph, index) => ({
    id: `${idPrefix}-b${index + 1}`,
    ...(page === undefined ? {} : { page }),
    text: paragraph,
  }));
}

function failed(
  input: RawInput,
  status: ExtractionStatus,
  warning: string,
  mediaType: string | null,
): SourceDocument {
  return {
    id: input.id,
    kind: 'document',
    inputKind: input.kind,
    displayName: input.displayName,
    contentHash: sha256Hex(`${input.id}:${status}`),
    ...(mediaType === null ? {} : { mediaType }),
    extractionStatus: status,
    textBlocks: [],
    warnings: [warning],
  };
}

/**
 * Produces exactly one SourceDocument per input. Every failure path yields a
 * document with an explicit extraction status and a human-readable reason, so
 * callers never have to guess whether a source was read.
 */
export async function extractDocument(
  input: RawInput,
  options: ExtractDocumentOptions = {},
): Promise<SourceDocument> {
  const limits = options.limits ?? INPUT_LIMITS;
  const mediaType = mediaTypeOf(input);
  const warnings: string[] = [];

  if (input.text !== undefined && input.text.trim().length > 0) {
    const blocks = splitTextBlocks(input.text, input.id);
    if (blocks.length === 0) {
      return failed(input, 'empty', 'Текст передан, но после нормализации не осталось содержимого.', mediaType);
    }
    if (input.text.length > limits.maxExtractedChars) {
      return failed(
        input,
        'resource_limit',
        `Текст ${input.text.length} символов превышает лимит ${limits.maxExtractedChars} символов.`,
        mediaType,
      );
    }
    const language = detectLanguage(input.text);
    return {
      id: input.id,
      kind: 'inline_text',
      inputKind: input.kind,
      displayName: input.displayName,
      contentHash: sha256Hex(input.text),
      ...(mediaType === null ? {} : { mediaType }),
      extractionStatus: 'extracted',
      ...(language === undefined ? {} : { language }),
      textBlocks: blocks,
      warnings,
    };
  }

  const base64 = input.contentBase64;
  if (base64 === undefined || base64.trim().length === 0) {
    return failed(input, 'empty', 'Не передан ни текст, ни вложение.', mediaType);
  }

  const bytes = decodedByteLength(base64);
  if (bytes === null) {
    return failed(input, 'unreadable_document', 'Вложение не является корректным base64.', mediaType);
  }
  if (bytes > limits.maxFileBytes) {
    return failed(
      input,
      'resource_limit',
      `Файл ${bytes} Б превышает лимит ${limits.maxFileBytes} Б на файл.`,
      mediaType,
    );
  }
  if (!isSupportedMediaType(mediaType)) {
    return failed(
      input,
      'unsupported_file',
      `Тип ${mediaType ?? 'не определён'} не поддерживается. Допустимо: PDF, DOCX, TXT, MD, EML.`,
      mediaType,
    );
  }

  const extractor = options.extractor;
  if (extractor === undefined || !extractor.supports(mediaType!)) {
    return failed(
      input,
      'unsupported_file',
      `Для типа ${mediaType} не подключён парсер.`,
      mediaType,
    );
  }

  let extracted: ExtractedDocumentText;
  try {
    extracted = await extractor.extract(input, { maxBytes: limits.maxFileBytes });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return failed(input, 'unreadable_document', `Не удалось извлечь текст: ${reason}`, mediaType);
  }

  const blocks = extracted.blocks
    .filter((block) => block.text.trim().length > 0)
    .map((block, index) => ({ ...block, id: `${input.id}-b${index + 1}` }));
  warnings.push(...(extracted.warnings ?? []));
  if (blocks.length === 0) {
    return failed(
      input,
      'unreadable_document',
      'В документе не найден текстовый слой. Скан без OCR не разбирается.',
      mediaType,
    );
  }

  const totalChars = blocks.reduce((total, block) => total + block.text.length, 0);
  if (totalChars > limits.maxExtractedChars) {
    return failed(
      input,
      'resource_limit',
      `Извлечено ${totalChars} символов, лимит ${limits.maxExtractedChars}.`,
      mediaType,
    );
  }
  const pages = new Set(
    blocks.map((block) => block.page).filter((page): page is number => page !== undefined),
  );
  if (pages.size > limits.maxPages) {
    return failed(
      input,
      'resource_limit',
      `Извлечено ${pages.size} страниц, лимит ${limits.maxPages}.`,
      mediaType,
    );
  }

  const joined = blocks.map((block) => block.text).join('\n');
  const status: ExtractionStatus = warnings.length > 0 ? 'partial' : 'extracted';
  const language = detectLanguage(joined);
  return {
    id: input.id,
    kind: 'document',
    inputKind: input.kind,
    displayName: input.displayName,
    contentHash: sha256Hex(joined),
    ...(mediaType === null ? {} : { mediaType }),
    extractionStatus: status,
    ...(language === undefined ? {} : { language }),
    textBlocks: blocks,
    warnings,
  };
}

export async function extractDocuments(
  inputs: readonly RawInput[],
  options: ExtractDocumentOptions = {},
): Promise<SourceDocument[]> {
  return Promise.all(inputs.map((input) => extractDocument(input, options)));
}

/** A link is only a source once its content was actually read. */
export function linkSourceDocument(target: {
  readonly id: string;
  readonly url: string;
  readonly status: ExtractionStatus;
  readonly blocks?: readonly TextBlock[];
  readonly warning: string;
}): SourceDocument {
  const blocks = (target.blocks ?? []).map((block, index) => ({
    ...block,
    id: `${target.id}-b${index + 1}`,
  }));
  return {
    id: target.id,
    kind: 'link',
    inputKind: 'other',
    displayName: target.url,
    contentHash: sha256Hex(target.url),
    extractionStatus: target.status,
    textBlocks: blocks,
    warnings: [target.warning],
  };
}
