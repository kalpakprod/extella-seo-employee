/**
 * Public entry point of the Extella HR analysis module.
 *
 * Deterministic by design: parsing, date arithmetic, scoring and evidence
 * validation all run in code. The expert/model adapter is exported for a
 * future integration but is not part of the current analysis pipeline.
 */
export { SCHEMA_VERSION } from './contracts/analysis.js';
export type {
  AiPatternObservation,
  AiPatternReview,
  AnalysisError,
  AnalysisErrorCode,
  AnalysisRequest,
  AnalysisResult,
  AnalysisStatus,
  CandidateFact,
  CandidateReport,
  EvidenceRef,
  InterviewQuestion,
  JobRequirement,
  RawInput,
  RequirementMatch,
  RequirementMatchStatus,
  ReviewSignal,
  Scorecard,
  SourceDocument,
  StageName,
  StageStatus,
  ValidationIssue,
  ValidationResult,
} from './contracts/analysis.js';

export { validateAnalysisResultSchema, validateReportSchema } from './contracts/validate-schema.js';

export { INPUT_LIMITS, validateAnalysisRequest, isSupportedMediaType, mediaTypeOf } from './intake/validate-input.js';
export type { InputLimits, InputValidationResult } from './intake/validate-input.js';
export { extractDocument, extractDocuments, splitTextBlocks, detectLanguage, sha256Hex } from './intake/extract-document.js';
export type { DocumentExtractorPort, ExtractedDocumentText, ExtractDocumentOptions } from './intake/extract-document.js';

export { redactForModel, redactSourcesForModel, MODEL_INPUT_NOTICE } from './privacy/redact.js';
export type { RedactionKind, RedactionResult, RedactedSources } from './privacy/redact.js';
export { createTemporaryInputStore } from './privacy/temporary-inputs.js';
export type { TemporaryInputStore, TemporaryInputStoreOptions, StoredTemporaryInput } from './privacy/temporary-inputs.js';

export { parseJob, parseVacancyRole } from './analysis/requirements.js';
export { normalizeFacts, mergeExperienceMonths, parseCandidateName } from './analysis/facts.js';
export type { NormalizedFacts } from './analysis/facts.js';
export { matchRequirements, parseSalaryRange } from './analysis/matching.js';
export { detectInconsistencies } from './analysis/integrity.js';
export { reviewTextPatterns } from './analysis/ai-patterns.js';
export { calculateScore } from './analysis/scoring.js';
export { DEFAULT_SCORING_POLICY, validatePolicy } from './analysis/policy.js';
export { analyzeText, limitationsFromSources } from './analysis/pipeline.js';
export type { PipelineOutput } from './analysis/pipeline.js';

export { composeReport } from './report/compose.js';
export type { ComposeInput } from './report/compose.js';
export { validateReport } from './report/validate.js';

export { analyzeApplication, createAnalysisSession } from './core/analyze-application.js';
export type { AnalysisPorts, AnalysisSession } from './core/analyze-application.js';

export {
  assertRedirectAllowed,
  assertUrlAllowed,
  assertUrlShapeAllowed,
  createInMemoryBrowserPort,
  createManualBrowserPort,
  createUnavailableBrowserPort,
  isForbiddenHost,
  UrlBlockedError,
} from './adapters/browser.js';
export type { BrowserPort, BrowserReadResult, BrowserTab, BrowserTarget } from './adapters/browser.js';

export {
  createScriptedExpertPort,
  createUnavailableExpertPort,
  EXPERT_BOUNDARY_NOTICE,
  withExpertTimeout,
} from './adapters/expert.js';
export type { ExpertPort, ExpertRequest, ExpertResult } from './adapters/expert.js';

export { createInMemoryExtellaPort, createUnavailableExtellaPort } from './adapters/extella.js';
export type { ExtellaPort, ProgressUpdate, UserActionRequest } from './adapters/extella.js';

export { escapeHtml, renderCandidateReportMarkdown, sanitizeUrlForDisplay } from './presentation/candidate-report.js';
