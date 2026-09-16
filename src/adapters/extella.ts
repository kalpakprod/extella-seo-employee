import type {
  AnalysisStatus,
  CandidateReport,
  StageName,
} from '../contracts/analysis.js';

export interface ProgressUpdate {
  readonly requestId: string;
  readonly status: AnalysisStatus;
  readonly stage: StageName;
  readonly message: string;
  /** 0..1 when the platform can show a determinate progress bar. */
  readonly progress?: number;
}

export interface UserActionRequest {
  readonly kind: 'login';
  readonly url: string;
  readonly instructions: string;
}

/**
 * Presentation boundary. Extella shows the card, progress, partial results and
 * reasons; the agent never contacts candidates and never writes to an ATS.
 */
export interface ExtellaPort {
  publishProgress(update: ProgressUpdate): Promise<void>;
  publishReport(requestId: string, report: CandidateReport): Promise<void>;
  requestUserAction(requestId: string, action: UserActionRequest): Promise<void>;
  publishFailure(requestId: string, message: string, recoverable: boolean): Promise<void>;
}

export interface InMemoryExtellaPort extends ExtellaPort {
  readonly progress: readonly ProgressUpdate[];
  readonly reports: ReadonlyMap<string, CandidateReport>;
  readonly actions: readonly { requestId: string; action: UserActionRequest }[];
  readonly failures: readonly { requestId: string; message: string; recoverable: boolean }[];
}

export function createInMemoryExtellaPort(): InMemoryExtellaPort {
  const progress: ProgressUpdate[] = [];
  const reports = new Map<string, CandidateReport>();
  const actions: { requestId: string; action: UserActionRequest }[] = [];
  const failures: { requestId: string; message: string; recoverable: boolean }[] = [];
  return {
    progress,
    reports,
    actions,
    failures,
    publishProgress: (update) => {
      progress.push(update);
      return Promise.resolve();
    },
    publishReport: (requestId, report) => {
      reports.set(requestId, report);
      return Promise.resolve();
    },
    requestUserAction: (requestId, action) => {
      actions.push({ requestId, action });
      return Promise.resolve();
    },
    publishFailure: (requestId, message, recoverable) => {
      failures.push({ requestId, message, recoverable });
      return Promise.resolve();
    },
  };
}

/** Placeholder until the real bridge contract is confirmed; never throws. */
export function createUnavailableExtellaPort(reason: string): ExtellaPort {
  void reason;
  const noop = (): Promise<void> => Promise.resolve();
  return { publishProgress: noop, publishReport: noop, requestUserAction: noop, publishFailure: noop };
}
