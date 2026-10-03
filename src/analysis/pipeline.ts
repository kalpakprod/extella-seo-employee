import type {
  AiPatternReview,
  CandidateReport,
  JobRequirement,
  RequirementMatch,
  ReviewSignal,
  Scorecard,
  SourceDocument,
  ValidationResult,
} from '../contracts/analysis.js';
import { normalizeFacts, type NormalizedFacts } from './facts.js';
import { matchRequirements } from './matching.js';
import { parseJob, parseVacancyRole } from './requirements.js';
import { detectInconsistencies } from './integrity.js';
import { reviewTextPatterns } from './ai-patterns.js';
import { calculateScore } from './scoring.js';
import { composeReport } from '../report/compose.js';
import { validateReport } from '../report/validate.js';

export interface PipelineOutput {
  readonly requirements: readonly JobRequirement[];
  readonly role: string | null;
  readonly facts: NormalizedFacts;
  readonly matches: readonly RequirementMatch[];
  readonly signals: readonly ReviewSignal[];
  readonly aiCheck: AiPatternReview;
  readonly scorecard: Scorecard;
  readonly report: CandidateReport;
  readonly validation: ValidationResult;
  readonly sourceLimitations: readonly string[];
}

/** Human-readable limitations derived from what could not be read. */
export function limitationsFromSources(sources: readonly SourceDocument[]): string[] {
  const limitations: string[] = [];
  for (const source of sources) {
    if (source.extractionStatus === 'extracted') {
      continue;
    }
    const reason = source.warnings.join(' ');
    if (source.extractionStatus === 'partial') {
      limitations.push(`«${source.displayName}» прочитан частично: ${reason}`);
      continue;
    }
    const label =
      source.kind === 'link' ? 'Ссылка не открыта' : 'Источник не разобран';
    limitations.push(`${label} («${source.displayName}»): ${reason}`);
  }
  const unreadable = sources.filter(
    (source) => source.extractionStatus !== 'extracted' && source.extractionStatus !== 'partial',
  ).length;
  if (unreadable > 0 && sources.length - unreadable === 0) {
    limitations.push('Ни один источник не удалось прочитать: оценка не может быть выведена.');
  }
  return limitations;
}

/**
 * The deterministic core. It is pure with respect to the outside world: given
 * the same sources it produces the same facts, matches, score and report.
 */
export function analyzeText(requestId: string, sources: readonly SourceDocument[]): PipelineOutput {
  const requirements = parseJob(sources);
  const role = parseVacancyRole(sources);
  const facts = normalizeFacts(sources);
  const matches = matchRequirements(requirements, facts, sources);
  const signals = detectInconsistencies(facts, sources);
  const aiCheck = reviewTextPatterns(sources);
  const scorecard = calculateScore(requirements, matches);
  const sourceLimitations = limitationsFromSources(sources);
  const report = composeReport({
    requestId,
    sources,
    requirements,
    matches,
    facts,
    signals,
    aiCheck,
    scorecard,
    role,
    sourceLimitations,
  });
  const validation = validateReport(report, sources);
  return { requirements, role, facts, matches, signals, aiCheck, scorecard, report, validation, sourceLimitations };
}
