import type {
  AnalysisError,
  AnalysisRequest,
  InputKind,
  RawInput,
} from '../contracts/analysis.js';
import { SCHEMA_VERSION } from '../contracts/analysis.js';

/**
 * Hard limits from the agreed spec. Exceeding a limit is always reported,
 * never silently truncated.
 */
export const INPUT_LIMITS = {
  maxAttachments: 10,
  maxFileBytes: 20 * 1024 * 1024,
  maxPackageBytes: 50 * 1024 * 1024,
  maxPages: 100,
  maxExtractedChars: 200_000,
} as const;

export interface InputLimits {
  readonly maxAttachments: number;
  readonly maxFileBytes: number;
  readonly maxPackageBytes: number;
  readonly maxPages: number;
  readonly maxExtractedChars: number;
}

const MEDIA_TYPE_BY_EXTENSION: Readonly<Record<string, string>> = {
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  txt: 'text/plain',
  md: 'text/markdown',
  eml: 'message/rfc822',
  html: 'text/html',
  htm: 'text/html',
};

export const SUPPORTED_MEDIA_TYPES: readonly string[] = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
  'text/markdown',
  'message/rfc822',
];

export interface InputValidationResult {
  readonly ok: boolean;
  readonly errors: readonly AnalysisError[];
  readonly warnings: readonly string[];
}

export function extensionOf(displayName: string): string {
  const match = /\.([A-Za-z0-9]+)$/.exec(displayName.trim());
  return match === null ? '' : match[1]!.toLowerCase();
}

/** Best-effort media type from the declared type, falling back to the file name. */
export function mediaTypeOf(input: RawInput): string | null {
  if (input.mediaType !== undefined && input.mediaType.trim().length > 0) {
    return input.mediaType.trim().toLowerCase();
  }
  const byExtension = MEDIA_TYPE_BY_EXTENSION[extensionOf(input.displayName)];
  return byExtension ?? null;
}

export function isSupportedMediaType(mediaType: string | null): boolean {
  return mediaType !== null && SUPPORTED_MEDIA_TYPES.includes(mediaType);
}

/** Decoded byte length of a base64 payload, or null when the payload is not valid base64. */
export function decodedByteLength(contentBase64: string): number | null {
  const compact = contentBase64.replace(/\s+/g, '');
  if (compact.length === 0 || compact.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(compact)) {
    return null;
  }
  const padding = compact.endsWith('==') ? 2 : compact.endsWith('=') ? 1 : 0;
  return (compact.length / 4) * 3 - padding;
}

export function packageByteSize(request: AnalysisRequest): number {
  const all = [...request.vacancyInputs, ...request.applicationInputs];
  return all.reduce((total, input) => {
    if (input.contentBase64 === undefined) {
      return total + Buffer.byteLength(input.text ?? '', 'utf8');
    }
    return total + (decodedByteLength(input.contentBase64) ?? 0);
  }, 0);
}

function hasContent(input: RawInput): boolean {
  return (
    (input.text !== undefined && input.text.trim().length > 0) ||
    (input.contentBase64 !== undefined && input.contentBase64.trim().length > 0)
  );
}

/**
 * Validates the shape, size and file types of an incoming package. Structural
 * problems are recoverable per-input errors; they never abort the whole run on
 * their own.
 */
export function validateAnalysisRequest(
  request: AnalysisRequest,
  limits: InputLimits = INPUT_LIMITS,
): InputValidationResult {
  const errors: AnalysisError[] = [];
  const warnings: string[] = [];
  const pushError = (error: AnalysisError): void => {
    errors.push(error);
  };

  if (request.requestId.trim().length === 0) {
    pushError({ code: 'invalid_request', message: 'Не передан идентификатор разбора (requestId).', recoverable: false });
  }
  if (request.schemaVersion !== SCHEMA_VERSION) {
    pushError({
      code: 'invalid_request',
      message: `Неизвестная версия схемы запроса: ${request.schemaVersion}. Ожидается ${SCHEMA_VERSION}.`,
      recoverable: false,
    });
  }
  if (request.locale.trim().length === 0) {
    warnings.push('Локаль не указана: формат вывода и разбор дат международной вакансии могут быть неточными.');
  }

  const allInputs: readonly RawInput[] = [...request.vacancyInputs, ...request.applicationInputs];
  const seenIds = new Set<string>();
  if (allInputs.length === 0 && request.suppliedUrls.length === 0) {
    pushError({ code: 'empty_input', message: 'Пакет пуст: не передано ни вакансии, ни материалов отклика.', recoverable: false });
    return { ok: false, errors, warnings };
  }
  if (allInputs.length === 0) {
    warnings.push('Вложений нет: разбор опирается только на присланные ссылки.');
  }
  if (allInputs.length > limits.maxAttachments) {
    pushError({
      code: 'resource_limit',
      message: `Вложений ${allInputs.length}, лимит ${limits.maxAttachments} на разбор.`,
      recoverable: false,
    });
  }

  for (const input of allInputs) {
    if (input.id.trim().length === 0) {
      pushError({
        code: 'invalid_request',
        message: `«${input.displayName}»: идентификатор источника не может быть пустым.`,
        recoverable: true,
        sourceId: input.id,
      });
    } else if (seenIds.has(input.id)) {
      pushError({
        code: 'invalid_request',
        message: `Источник «${input.id}» передан более одного раза. Идентификаторы должны быть уникальны.`,
        sourceId: input.id,
        recoverable: false,
      });
    }
    seenIds.add(input.id);
    if (!hasContent(input)) {
      pushError({
        code: 'empty_input',
        message: `«${input.displayName}»: нет ни текста, ни вложения.`,
        sourceId: input.id,
        recoverable: true,
      });
      continue;
    }
    if (input.contentBase64 !== undefined) {
      const bytes = decodedByteLength(input.contentBase64);
      if (bytes === null) {
        pushError({
          code: 'unreadable_document',
          message: `«${input.displayName}»: вложение не является корректным base64.`,
          sourceId: input.id,
          recoverable: true,
        });
        continue;
      }
      if (bytes > limits.maxFileBytes) {
        pushError({
          code: 'resource_limit',
          message: `«${input.displayName}»: ${bytes} Б превышает лимит ${limits.maxFileBytes} Б на файл.`,
          sourceId: input.id,
          recoverable: true,
        });
      }
      const mediaType = mediaTypeOf(input);
      if (!isSupportedMediaType(mediaType)) {
        pushError({
          code: 'unsupported_file',
          message: `«${input.displayName}»: тип ${mediaType ?? 'не определён'} не поддерживается. Допустимо: PDF, DOCX, TXT, MD, EML.`,
          sourceId: input.id,
          recoverable: true,
        });
      }
    } else if (Buffer.byteLength(input.text ?? '', 'utf8') > limits.maxFileBytes) {
      pushError({
        code: 'resource_limit',
        message: `«${input.displayName}»: текст превышает лимит ${limits.maxFileBytes} Б на файл.`,
        sourceId: input.id,
        recoverable: true,
      });
    }
  }

  const totalBytes = packageByteSize(request);
  if (totalBytes > limits.maxPackageBytes) {
    pushError({
      code: 'resource_limit',
      message: `Пакет ${totalBytes} Б превышает лимит ${limits.maxPackageBytes} Б.`,
      recoverable: false,
    });
  }

  for (const url of request.suppliedUrls) {
    if (!/^https?:\/\//i.test(url.trim())) {
      warnings.push(`Ссылка «${url}» не будет открыта: поддерживаются только http/https.`);
    }
  }

  const hasBlocking = errors.some((error) => !error.recoverable);
  return { ok: !hasBlocking, errors, warnings };
}

export function isVacancyInput(kind: InputKind): boolean {
  return kind === 'vacancy';
}
