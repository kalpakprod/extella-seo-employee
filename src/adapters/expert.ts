import type { AnalysisError, SourceDocument } from '../contracts/analysis.js';

/**
 * The expert (model) receives minimized text and a schema, and returns a
 * payload. It never gets tools for sending messages, writing to an ATS or
 * browsing; those capabilities live only behind explicit user actions.
 */
export const EXPERT_BOUNDARY_NOTICE =
  'Эксперт получает только обезличенный текст и схему ответа. Инструменты отправки сообщений, записи в ATS и самостоятельного браузинга ему не предоставляются.';

export interface ExpertRequest {
  readonly task: 'review' | 'extract';
  readonly locale: string;
  readonly instructions: string;
  readonly documents: readonly SourceDocument[];
  /** JSON schema the payload must satisfy. */
  readonly schema: Readonly<Record<string, unknown>>;
}

export type ExpertResult =
  | { readonly ok: true; readonly payload: unknown; readonly model?: string }
  | { readonly ok: false; readonly error: AnalysisError };

export interface ExpertPort {
  analyze(request: ExpertRequest, signal?: AbortSignal): Promise<ExpertResult>;
}

export function expertUnavailable(reason: string): ExpertResult {
  return {
    ok: false,
    error: { code: 'integration_unavailable', message: `Эксперт недоступен: ${reason}`, recoverable: true },
  };
}

export function expertTimeout(ms: number): ExpertResult {
  return {
    ok: false,
    error: { code: 'model_timeout', message: `Эксперт не ответил за ${ms} мс.`, recoverable: true },
  };
}

export function invalidModelOutput(detail: string): ExpertResult {
  return {
    ok: false,
    error: { code: 'invalid_model_output', message: `Ответ эксперта не соответствует схеме: ${detail}`, recoverable: true },
  };
}

export function createUnavailableExpertPort(reason: string): ExpertPort {
  return { analyze: () => Promise.resolve(expertUnavailable(reason)) };
}

/**
 * Deterministic stand-in: returns the configured payload or typed error, with
 * an optional delay to exercise timeout and cancellation paths.
 */
export function createScriptedExpertPort(
  handler: (request: ExpertRequest) => ExpertResult | Promise<ExpertResult>,
  options: { readonly delayMs?: number } = {},
): ExpertPort {
  return {
    analyze: async (request) => {
      if (options.delayMs !== undefined && options.delayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, options.delayMs));
      }
      return handler(request);
    },
  };
}

/** Wraps a port so a hung provider always ends in a typed timeout error. */
export function withExpertTimeout(port: ExpertPort, timeoutMs: number): ExpertPort {
  return {
    analyze: async (request, signal) => {
      let timer: NodeJS.Timeout | undefined;
      const timeout = new Promise<ExpertResult>((resolve) => {
        timer = setTimeout(() => resolve(expertTimeout(timeoutMs)), timeoutMs);
      });
      try {
        return await Promise.race([port.analyze(request, signal), timeout]);
      } finally {
        if (timer !== undefined) {
          clearTimeout(timer);
        }
      }
    },
  };
}
