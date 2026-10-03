import type {
  CandidateReport,
  EvidenceRef,
  SourceDocument,
  ValidationIssue,
  ValidationResult,
} from '../contracts/analysis.js';
import { collectEvidenceRefs, findBlock } from '../analysis/evidence.js';
import { validateReportSchema } from '../contracts/validate-schema.js';

function checkEvidenceRef(
  ref: EvidenceRef,
  sources: readonly SourceDocument[],
  issues: ValidationIssue[],
): void {
  const found = findBlock(sources, ref.sourceId, ref.blockId);
  if (found === null) {
    issues.push({
      code: 'unknown_source',
      message: `Ссылка на неизвестный источник ${ref.sourceId}/${ref.blockId}.`,
    });
    return;
  }
  const { block } = found;
  if (ref.start < 0 || ref.end <= ref.start || ref.end > block.text.length) {
    issues.push({
      code: 'invalid_range',
      message: `Диапазон ${ref.start}..${ref.end} выходит за границы блока ${block.id}.`,
      path: ref.blockId,
    });
    return;
  }
  const actual = block.text.slice(ref.start, ref.end);
  if (actual !== ref.quote) {
    issues.push({
      code: 'quote_not_found',
      message: `Цитата не совпадает с текстом источника в ${ref.blockId}.`,
      path: ref.blockId,
    });
  }
}

function requireEvidence(
  refs: readonly EvidenceRef[],
  label: string,
  issues: ValidationIssue[],
): void {
  if (refs.length === 0) {
    issues.push({
      code: 'missing_evidence',
      message: `Утверждение «${label}» не имеет подтверждающей цитаты или пометки «неизвестно».`,
    });
  }
}

/**
 * Validates a composed report against its sources: structural schema plus
 * evidence integrity. Semantic correctness still requires human/reference
 * review; a matching quote alone does not prove a correct conclusion.
 */
export function validateReport(
  report: CandidateReport,
  sources: readonly SourceDocument[],
): ValidationResult {
  const issues: ValidationIssue[] = [...validateReportSchema(report).issues];

  for (const ref of collectEvidenceRefs(report)) {
    checkEvidenceRef(ref, sources, issues);
  }

  for (const match of report.matches) {
    if (match.status === 'matched' || match.status === 'partial') {
      requireEvidence(match.evidenceRefs, `соответствие ${match.requirementId}`, issues);
    }
  }
  for (const note of [...report.strengths, ...report.gaps, ...report.greenFlags, ...report.features]) {
    requireEvidence(note.evidenceRefs, note.label, issues);
  }
  for (const signal of report.redFlags) {
    requireEvidence(signal.evidenceRefs, signal.observation, issues);
  }
  if (report.aiCheck.assessable) {
    for (const observation of report.aiCheck.observations) {
      requireEvidence(observation.evidenceRefs, observation.observation, issues);
    }
  }
  for (const question of report.questions) {
    requireEvidence(question.evidenceRefs, question.question, issues);
  }

  return { valid: issues.length === 0, issues };
}
