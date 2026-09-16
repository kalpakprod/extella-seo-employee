import { createRequire } from 'node:module';
import type { ErrorObject, ValidateFunction } from 'ajv';
import type { FormatsPlugin } from 'ajv-formats';
import { candidateReportSchema } from './analysis.schema.js';
import type {
  AnalysisResult,
  CandidateReport,
  ValidationIssue,
  ValidationResult,
} from './analysis.js';

type AjvConstructor = typeof import('ajv').default;

// Ajv and ajv-formats ship CommonJS entry points whose default export is the
// constructor/plugin itself; loading them through createRequire keeps the
// runtime and the types aligned under NodeNext.
const require = createRequire(import.meta.url);
const Ajv = require('ajv') as AjvConstructor;
const addFormats = require('ajv-formats') as FormatsPlugin;

const ajv = new Ajv({ allErrors: true, strict: true });
addFormats(ajv);

const validateCandidateReportPayload = ajv.compile<CandidateReport>(
  candidateReportSchema,
) as ValidateFunction<CandidateReport>;

function formatAjvPath(instancePath: string): string {
  return instancePath.length > 0 ? instancePath.slice(1).replaceAll('/', '.') : '(root)';
}

/** Structural validation of a report produced by any producer (code or model). */
export function validateReportSchema(report: unknown): ValidationResult {
  const valid = validateCandidateReportPayload(report);
  if (valid) {
    return { valid: true, issues: [] };
  }
  const errors: ErrorObject[] = validateCandidateReportPayload.errors ?? [];
  const issues: ValidationIssue[] = errors.map((error) => {
    const path = formatAjvPath(error.instancePath);
    return {
      code: 'schema_violation',
      path,
      message: `${path}: ${error.message ?? 'schema violation'}`,
    };
  });
  return { valid: false, issues };
}

export function validateAnalysisResultSchema(result: AnalysisResult): ValidationResult {
  if (result.report === undefined) {
    return { valid: true, issues: [] };
  }
  return validateReportSchema(result.report);
}
