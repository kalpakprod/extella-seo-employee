import type {
  AnalysisError,
  AnalysisRequest,
  AnalysisResult,
  AnalysisStatus,
  SourceDocument,
  StageName,
  StageStatus,
} from '../contracts/analysis.js';
import { SCHEMA_VERSION } from '../contracts/analysis.js';
import { analyzeText } from '../analysis/pipeline.js';
import { parseCandidateName } from '../analysis/facts.js';
import {
  extractDocuments,
  linkSourceDocument,
  type DocumentExtractorPort,
} from '../intake/extract-document.js';
import { validateAnalysisRequest, type InputLimits } from '../intake/validate-input.js';
import {
  assertRedirectAllowed,
  assertUrlShapeAllowed,
  type BrowserPort,
  type BrowserReadResult,
} from '../adapters/browser.js';
import type { ExtellaPort } from '../adapters/extella.js';

export interface AnalysisPorts {
  readonly browser?: BrowserPort;
  readonly extractor?: DocumentExtractorPort;
  readonly extella?: ExtellaPort;
  readonly limits?: InputLimits;
}

const STAGE_ORDER: readonly StageName[] = [
  'intake',
  'extraction',
  'normalization',
  'matching',
  'integrity',
  'ai_patterns',
  'scoring',
  'composition',
  'validation',
];

function initialStageStatuses(): Record<StageName, StageStatus> {
  return Object.fromEntries(STAGE_ORDER.map((stage) => [stage, 'pending'])) as Record<StageName, StageStatus>;
}

function emptyResult(request: AnalysisRequest, status: AnalysisStatus, errors: readonly AnalysisError[]): AnalysisResult {
  return {
    schemaVersion: SCHEMA_VERSION,
    requestId: request.requestId,
    status,
    errors,
    sourceStatuses: [],
    stageStatuses: initialStageStatuses(),
  };
}

function isAborted(signal?: AbortSignal): boolean {
  return signal?.aborted === true;
}

function failure(code: AnalysisError['code'], message: string, recoverable = false): AnalysisError {
  return { code, message, recoverable };
}

/**
 * Runs one analysis end to end. The model is optional: when the expert is
 * unavailable the deterministic results are still returned as a partial
 * report, with an explicit reason.
 */
export async function analyzeApplication(
  request: AnalysisRequest,
  ports: AnalysisPorts,
  signal?: AbortSignal,
): Promise<AnalysisResult> {
  const stages = initialStageStatuses();
  const errors: AnalysisError[] = [];

  stages.intake = 'running';
  const validation = validateAnalysisRequest(request, ports.limits);
  errors.push(...validation.errors);
  if (!validation.ok) {
    stages.intake = 'failed';
    const result = emptyResult(request, 'failed', errors);
    return { ...result, stageStatuses: stages };
  }
  stages.intake = 'completed';
  await ports.extella?.publishProgress({
    requestId: request.requestId,
    status: 'extracting',
    stage: 'intake',
    message: 'Пакет принят, читаем источники.',
    progress: 0.1,
  });

  if (isAborted(signal)) {
    stages.extraction = 'cancelled';
    return {
      ...emptyResult(request, 'cancelled', [failure('cancelled', 'Разбор отменён до начала извлечения.', true)]),
      stageStatuses: stages,
    };
  }

  stages.extraction = 'running';
  const extracted = await extractDocuments([...request.vacancyInputs, ...request.applicationInputs], {
    ...(ports.extractor === undefined ? {} : { extractor: ports.extractor }),
    ...(ports.limits === undefined ? {} : { limits: ports.limits }),
  });
  for (const document of extracted) {
    if (document.extractionStatus !== 'extracted' && document.extractionStatus !== 'partial') {
      errors.push(
        failure(
          document.extractionStatus === 'unsupported_file'
            ? 'unsupported_file'
            : document.extractionStatus === 'resource_limit'
              ? 'resource_limit'
              : document.extractionStatus === 'empty'
                ? 'empty_input'
                : 'unreadable_document',
          `${document.displayName}: ${document.warnings.join(' ') || 'источник не прочитан.'}`,
          true,
        ),
      );
    }
  }

  const linkResults = await readLinks(request, ports, errors, signal);
  const sourceDocuments: SourceDocument[] = [...extracted, ...linkResults.documents];
  const needsLogin = linkResults.needsLogin;
  stages.extraction = errors.some((error) => error.code === 'resource_limit') ? 'failed' : 'completed';

  if (isAborted(signal)) {
    stages.normalization = 'cancelled';
    return {
      ...emptyResult(request, 'cancelled', [...errors, failure('cancelled', 'Разбор отменён.', true)]),
      sourceStatuses: sourceDocuments,
      stageStatuses: stages,
    };
  }

  const hasUsableApplication = sourceDocuments.some(
    (document) =>
      document.inputKind !== 'vacancy' &&
      (document.extractionStatus === 'extracted' || document.extractionStatus === 'partial') &&
      document.textBlocks.length > 0,
  );

  const candidateNames = new Set(
    sourceDocuments
      .filter(
        (document) =>
          document.inputKind === 'resume' &&
          (document.extractionStatus === 'extracted' || document.extractionStatus === 'partial'),
      )
      .map((document) => parseCandidateName(document.textBlocks.map((block) => block.text).join('\n')))
      .filter((name): name is string => name !== null),
  );
  if (candidateNames.size > 1) {
    stages.normalization = 'failed';
    for (const stage of ['matching', 'integrity', 'ai_patterns', 'scoring', 'composition', 'validation'] as const) {
      stages[stage] = 'skipped';
    }
    return {
      schemaVersion: SCHEMA_VERSION,
      requestId: request.requestId,
      status: 'failed',
      errors: [
        ...errors,
        failure(
          'invalid_request',
          `В пакете найдено несколько кандидатов (${[...candidateNames].join(', ')}). Разделите отклики: факты разных людей нельзя смешивать.`,
        ),
      ],
      sourceStatuses: sourceDocuments,
      stageStatuses: stages,
    };
  }

  if (!hasUsableApplication && needsLogin.length > 0) {
    stages.normalization = 'skipped';
    stages.matching = 'skipped';
    stages.integrity = 'skipped';
    stages.ai_patterns = 'skipped';
    stages.scoring = 'skipped';
    stages.composition = 'skipped';
    stages.validation = 'skipped';
    const waiting: AnalysisResult = {
      schemaVersion: SCHEMA_VERSION,
      requestId: request.requestId,
      status: 'waiting_for_user',
      errors: [...errors, failure('login_required', 'Нужен вход HR, чтобы прочитать присланную страницу.', true)],
      sourceStatuses: sourceDocuments,
      stageStatuses: stages,
    };
    await publishOutcome(ports.extella, waiting);
    return waiting;
  }

  stages.normalization = 'running';
  const analysis = analyzeText(request.requestId, sourceDocuments);
  stages.normalization = 'completed';
  stages.matching = 'completed';
  stages.integrity = 'completed';
  stages.ai_patterns = 'completed';
  stages.scoring = 'completed';
  stages.composition = 'completed';
  stages.validation = analysis.validation.valid ? 'completed' : 'failed';

  if (!analysis.validation.valid) {
    errors.push(
      failure(
        'internal_error',
        `Карточка не прошла проверку цитат: ${analysis.validation.issues.map((issue) => issue.message).join('; ')}`,
        false,
      ),
    );
  }
  if (!analysis.scorecard.insufficientData && analysis.report.scorecard.overall === null) {
    errors.push(failure('internal_error', 'Оценка не выведена без явной причины.', false));
  }

  const status: AnalysisStatus =
    analysis.validation.valid && needsLogin.length === 0 && errors.length === 0
      ? 'completed'
      : 'partial';

  const result: AnalysisResult = {
    schemaVersion: SCHEMA_VERSION,
    requestId: request.requestId,
    status,
    report: analysis.report,
    errors,
    sourceStatuses: sourceDocuments,
    stageStatuses: stages,
  };
  await publishOutcome(ports.extella, result);
  return result;
}

async function publishOutcome(
  extella: ExtellaPort | undefined,
  result: AnalysisResult,
): Promise<void> {
  if (extella === undefined) {
    return;
  }
  const { requestId, status } = result;
  await extella.publishProgress({
    requestId,
    status,
    stage: 'composition',
    message: `Разбор завершён со статусом ${status}.`,
    progress: status === 'completed' || status === 'partial' ? 1 : undefined,
  });
  if (status === 'waiting_for_user') {
    await extella.requestUserAction(requestId, {
      kind: 'login',
      url: result.sourceStatuses.find((source) => source.kind === 'link')?.displayName ?? '',
      instructions: 'Откройте присланную страницу в браузере агента и войдите под своей учётной записью. Логины и пароли агент не принимает.',
    });
    return;
  }
  if (result.report !== undefined) {
    await extella.publishReport(requestId, result.report);
  }
  if (status === 'failed') {
    await extella.publishFailure(
      requestId,
      result.errors.map((error) => error.message).join(' ') || 'Разбор не удалось выполнить.',
      false,
    );
  }
}

interface LinkReadOutcome {
  readonly documents: readonly SourceDocument[];
  readonly needsLogin: readonly string[];
}

async function readLinks(
  request: AnalysisRequest,
  ports: AnalysisPorts,
  errors: AnalysisError[],
  signal?: AbortSignal,
): Promise<LinkReadOutcome> {
  const browser = ports.browser;
  const documents: SourceDocument[] = [];
  const needsLogin: string[] = [];
  const occupiedIds = new Set(
    [...request.vacancyInputs, ...request.applicationInputs].map((input) => input.id),
  );
  let linkIdCounter = 0;
  const nextLinkId = (): string => {
    let id: string;
    do {
      linkIdCounter += 1;
      id = `link-${linkIdCounter}`;
    } while (occupiedIds.has(id));
    occupiedIds.add(id);
    return id;
  };
  if (request.suppliedUrls.length === 0) {
    return { documents, needsLogin };
  }
  if (browser === undefined) {
    for (const url of request.suppliedUrls) {
      errors.push(failure('integration_unavailable', `Ссылка «${url}» не открыта: браузер агента не подключён.`, true));
      documents.push(
        linkSourceDocument({ id: nextLinkId(), url, status: 'unreadable_document', warning: 'Браузер агента не подключён.' }),
      );
    }
    return { documents, needsLogin };
  }

  for (const url of request.suppliedUrls) {
    const id = nextLinkId();
    try {
      assertUrlShapeAllowed(url);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      errors.push(failure('source_unavailable', reason, true));
      documents.push(linkSourceDocument({ id, url, status: 'unreadable_document', warning: reason }));
      continue;
    }
    if (isAborted(signal)) {
      documents.push(linkSourceDocument({ id, url, status: 'empty', warning: 'Чтение ссылки отменено.' }));
      continue;
    }
    let result: BrowserReadResult;
    try {
      result = await browser.readSubmittedPage({ url, userOpened: true }, signal);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      errors.push(failure('source_unavailable', `Ссылка «${url}»: ${reason}`, true));
      documents.push(linkSourceDocument({ id, url, status: 'unreadable_document', warning: reason }));
      continue;
    }
    switch (result.status) {
      case 'readable': {
        try {
          assertRedirectAllowed(url, result.url);
        } catch (error) {
          const reason = error instanceof Error ? error.message : String(error);
          errors.push(failure('source_unavailable', reason, true));
          documents.push(linkSourceDocument({ id, url, status: 'unreadable_document', warning: reason }));
          break;
        }
        const source = await linkReadableSource(id, result);
        documents.push(source);
        break;
      }
      case 'needs_login':
        needsLogin.push(url);
        errors.push(failure('login_required', `Ссылка «${url}»: ${result.reason}`, true));
        documents.push(linkSourceDocument({ id, url, status: 'unreadable_document', warning: result.reason }));
        break;
      case 'blocked':
        errors.push(failure('source_unavailable', `Ссылка «${url}» заблокирована: ${result.reason}`, true));
        documents.push(linkSourceDocument({ id, url, status: 'unreadable_document', warning: result.reason }));
        break;
      default:
        errors.push(failure('source_unavailable', `Ссылка «${url}» недоступна: ${result.reason}`, true));
        documents.push(linkSourceDocument({ id, url, status: 'unreadable_document', warning: result.reason }));
        break;
    }
  }
  return { documents, needsLogin };
}

async function linkReadableSource(id: string, result: Extract<BrowserReadResult, { status: 'readable' }>): Promise<SourceDocument> {
  const source = linkSourceDocument({
    id,
    url: result.url,
    status: 'extracted',
    blocks: result.blocks,
    warning: `Страница прочитана${result.title === undefined ? '' : `: ${result.title}`}.`,
  });
  return { ...source, warnings: [...source.warnings, ...result.warnings] };
}

export interface AnalysisSession {
  analyze(request: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResult>;
  /** Releases a cached result so the same requestId can run again. */
  clear(requestId: string): void;
  activeCount(): number;
}

/**
 * Session-scoped idempotency: a repeated requestId joins the in-flight run or
 * returns the finished result instead of starting a second parallel analysis.
 */
export function createAnalysisSession(ports: AnalysisPorts): AnalysisSession {
  const inFlight = new Map<string, Promise<AnalysisResult>>();
  const finished = new Map<string, AnalysisResult>();
  return {
    analyze(request, signal) {
      const existing = inFlight.get(request.requestId);
      if (existing !== undefined) {
        return existing;
      }
      const cached = finished.get(request.requestId);
      if (cached !== undefined) {
        return Promise.resolve(cached);
      }
      let run: Promise<AnalysisResult>;
      run = analyzeApplication(request, ports, signal)
        .then((result) => {
          if (inFlight.get(request.requestId) === run && result.status === 'completed') {
            finished.set(request.requestId, result);
          }
          return result;
        })
        .finally(() => {
          if (inFlight.get(request.requestId) === run) {
            inFlight.delete(request.requestId);
          }
        });
      inFlight.set(request.requestId, run);
      return run;
    },
    clear(requestId) {
      inFlight.delete(requestId);
      finished.delete(requestId);
    },
    activeCount() {
      return inFlight.size;
    },
  };
}
