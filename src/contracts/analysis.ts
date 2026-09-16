export const SCHEMA_VERSION = '1.0.0' as const;

export type AnalysisStatus =
  | 'intake'
  | 'extracting'
  | 'analyzing'
  | 'waiting_for_user'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled';

export type StageName =
  | 'intake'
  | 'extraction'
  | 'normalization'
  | 'matching'
  | 'integrity'
  | 'ai_patterns'
  | 'scoring'
  | 'composition'
  | 'validation';

export type StageStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'failed' | 'cancelled';

export type AnalysisErrorCode =
  | 'unsupported_file'
  | 'unreadable_document'
  | 'resource_limit'
  | 'empty_input'
  | 'login_required'
  | 'source_unavailable'
  | 'integration_unavailable'
  | 'model_timeout'
  | 'invalid_model_output'
  | 'cancelled'
  | 'invalid_request'
  | 'internal_error';

export interface AnalysisError {
  readonly code: AnalysisErrorCode;
  readonly message: string;
  readonly sourceId?: string;
  readonly recoverable: boolean;
}

export type InputKind =
  | 'resume'
  | 'cover_letter'
  | 'email_application'
  | 'vacancy'
  | 'other';

/** A raw input handed to the pipeline by the Extella intake surface. */
export interface RawInput {
  readonly id: string;
  readonly kind: InputKind;
  readonly displayName: string;
  /** Media type when known, e.g. application/pdf or text/plain. */
  readonly mediaType?: string;
  /** Inline text (already decoded) when the platform supplies it directly. */
  readonly text?: string;
  /** Base64 payload for binary attachments provided through the bridge. */
  readonly contentBase64?: string;
  readonly sourceUri?: string;
}

export interface AnalysisRequest {
  readonly schemaVersion: string;
  readonly requestId: string;
  readonly locale: string;
  readonly vacancyInputs: readonly RawInput[];
  readonly applicationInputs: readonly RawInput[];
  readonly suppliedUrls: readonly string[];
}

export type ExtractionStatus =
  | 'extracted'
  | 'partial'
  | 'unsupported_file'
  | 'unreadable_document'
  | 'resource_limit'
  | 'empty';

export interface TextBlock {
  readonly id: string;
  readonly page?: number;
  readonly text: string;
}

export type SourceKind = 'document' | 'inline_text' | 'link';

export interface SourceDocument {
  readonly id: string;
  readonly kind: SourceKind;
  readonly inputKind: InputKind;
  readonly displayName: string;
  readonly contentHash: string;
  readonly mediaType?: string;
  readonly extractionStatus: ExtractionStatus;
  readonly language?: string;
  readonly textBlocks: readonly TextBlock[];
  readonly warnings: readonly string[];
}

export interface EvidenceRef {
  readonly sourceId: string;
  readonly blockId: string;
  readonly page?: number;
  readonly start: number;
  readonly end: number;
  readonly quote: string;
}

export type RequirementCategory =
  | 'skill'
  | 'experience'
  | 'education'
  | 'language'
  | 'location'
  | 'format'
  | 'compensation'
  | 'other';

export type RequirementPriority = 'must_have' | 'nice_to_have';

/**
 * `professional` requirements take part in matching and scoring.
 * `excluded_protected` requirements (age, gender, nationality, ...) are shown
 * to HR as facts but never influence scores or recommendations.
 */
export type RequirementEligibility = 'professional' | 'excluded_protected';

export interface JobRequirement {
  readonly id: string;
  readonly text: string;
  readonly category: RequirementCategory;
  readonly priority: RequirementPriority;
  readonly eligibility: RequirementEligibility;
  readonly evidenceRefs: readonly EvidenceRef[];
}

export type FactField =
  | 'full_name'
  | 'age'
  | 'location'
  | 'work_format'
  | 'language'
  | 'skill'
  | 'total_experience'
  | 'work_period'
  | 'salary_expectation'
  | 'availability'
  | 'education'
  | 'employment_gap'
  | 'other';

export type FactCertainty = 'stated' | 'inferred' | 'unknown';

export interface CandidateFact {
  readonly id: string;
  readonly field: FactField;
  readonly value: string;
  readonly certainty: FactCertainty;
  readonly evidenceRefs: readonly EvidenceRef[];
}

export type RequirementMatchStatus =
  | 'matched'
  | 'partial'
  | 'missing'
  | 'unknown'
  | 'excluded';

export interface RequirementMatch {
  readonly requirementId: string;
  readonly status: RequirementMatchStatus;
  readonly factIds: readonly string[];
  readonly evidenceRefs: readonly EvidenceRef[];
  readonly explanation: string;
}

export type ReviewSignalKind =
  | 'impossible_date'
  | 'period_overlap'
  | 'employment_gap'
  | 'cross_document_discrepancy'
  | 'level_mismatch'
  | 'unverified_claim'
  | 'suspicious_contact';

export interface ReviewSignal {
  readonly id: string;
  readonly kind: ReviewSignalKind;
  readonly observation: string;
  readonly evidenceRefs: readonly EvidenceRef[];
  readonly alternativeExplanation: string;
  readonly interviewQuestion: string;
  readonly severity: 'info' | 'attention';
}

export type AiPatternCategory =
  | 'template_phrases'
  | 'no_concrete_metrics'
  | 'uniform_style_across_documents'
  | 'repetition'
  | 'style_shift'
  | 'humanizer_markers'
  | 'language_level_mismatch';

export interface AiPatternObservation {
  readonly category: AiPatternCategory;
  readonly observation: string;
  readonly evidenceRefs: readonly EvidenceRef[];
  readonly confidence: 'low' | 'medium';
}

/**
 * Authorship cannot be established from text, so this review reports only
 * observable patterns. It never lowers the candidate score.
 */
export interface AiPatternReview {
  readonly assessable: boolean;
  readonly observations: readonly AiPatternObservation[];
  readonly hypothesis: 'likely_human' | 'mixed' | 'likely_ai' | 'likely_ai_humanized' | 'undetermined';
  readonly limitations: readonly string[];
}

export interface SubScores {
  readonly experience: number | null;
  readonly skills: number | null;
  readonly hrPreferences: number | null;
  readonly evidenceQuality: number | null;
}

export interface Scorecard {
  readonly overall: number | null;
  readonly subscores: SubScores;
  readonly coverage: number;
  readonly policyVersion: string;
  readonly explanation: readonly string[];
  readonly insufficientData: boolean;
}

export interface CandidateHeader {
  readonly name: string | null;
  readonly role: string | null;
  readonly verdict: string;
}

export interface FeatureNote {
  readonly label: string;
  readonly detail: string;
  readonly evidenceRefs: readonly EvidenceRef[];
}

export type InterviewQuestionSource = 'fact' | 'requirement' | 'signal' | 'ai_pattern';

export interface InterviewQuestion {
  readonly id: string;
  readonly question: string;
  readonly source: InterviewQuestionSource;
  readonly evidenceRefs: readonly EvidenceRef[];
}

export interface CandidateReport {
  readonly schemaVersion: string;
  readonly requestId: string;
  readonly header: CandidateHeader;
  readonly matches: readonly RequirementMatch[];
  readonly strengths: readonly FeatureNote[];
  readonly gaps: readonly FeatureNote[];
  readonly redFlags: readonly ReviewSignal[];
  readonly greenFlags: readonly FeatureNote[];
  readonly features: readonly FeatureNote[];
  readonly aiCheck: AiPatternReview;
  readonly scorecard: Scorecard;
  readonly questions: readonly InterviewQuestion[];
  readonly limitations: readonly string[];
}

export interface ValidationIssue {
  readonly code: 'unknown_source' | 'quote_not_found' | 'invalid_range' | 'missing_evidence' | 'schema_violation';
  readonly message: string;
  readonly path?: string;
}

export interface ValidationResult {
  readonly valid: boolean;
  readonly issues: readonly ValidationIssue[];
}

export interface AnalysisResult {
  readonly schemaVersion: string;
  readonly requestId: string;
  readonly status: AnalysisStatus;
  readonly report?: CandidateReport;
  readonly errors: readonly AnalysisError[];
  readonly sourceStatuses: readonly SourceDocument[];
  readonly stageStatuses: Readonly<Record<StageName, StageStatus>>;
}
