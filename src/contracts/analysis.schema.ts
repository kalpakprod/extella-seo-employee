/**
 * JSON Schema for the structured candidate report.
 *
 * Kept as a TypeScript module (rather than a separately loaded .json file) so
 * that the schema and the contract types are validated together by `tsc` and
 * no runtime file IO is required from either `vitest` or the compiled build.
 */
export const candidateReportSchema = {
  $id: 'https://extella.ai/hr/candidate-report.schema.json',
  type: 'object',
  additionalProperties: false,
  required: [
    'schemaVersion',
    'requestId',
    'header',
    'matches',
    'strengths',
    'gaps',
    'redFlags',
    'greenFlags',
    'features',
    'aiCheck',
    'scorecard',
    'questions',
    'limitations',
  ],
  properties: {
    schemaVersion: { type: 'string', minLength: 1 },
    requestId: { type: 'string', minLength: 1 },
    header: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'role', 'verdict'],
      properties: {
        name: { type: ['string', 'null'] },
        role: { type: ['string', 'null'] },
        verdict: { type: 'string', minLength: 1 },
      },
    },
    matches: { type: 'array', items: { $ref: '#/$defs/requirementMatch' } },
    strengths: { type: 'array', items: { $ref: '#/$defs/featureNote' } },
    gaps: { type: 'array', items: { $ref: '#/$defs/featureNote' } },
    redFlags: { type: 'array', items: { $ref: '#/$defs/reviewSignal' } },
    greenFlags: { type: 'array', items: { $ref: '#/$defs/featureNote' } },
    features: { type: 'array', items: { $ref: '#/$defs/featureNote' } },
    aiCheck: {
      type: 'object',
      additionalProperties: false,
      required: ['assessable', 'observations', 'hypothesis', 'limitations'],
      properties: {
        assessable: { type: 'boolean' },
        observations: {
          type: 'array',
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['category', 'observation', 'evidenceRefs', 'confidence'],
            properties: {
              category: {
                enum: [
                  'template_phrases',
                  'no_concrete_metrics',
                  'uniform_style_across_documents',
                  'repetition',
                  'style_shift',
                  'humanizer_markers',
                  'language_level_mismatch',
                ],
              },
              observation: { type: 'string', minLength: 1 },
              evidenceRefs: { type: 'array', items: { $ref: '#/$defs/evidenceRef' } },
              confidence: { enum: ['low', 'medium'] },
            },
          },
        },
        hypothesis: {
          enum: ['likely_human', 'mixed', 'likely_ai', 'likely_ai_humanized', 'undetermined'],
        },
        limitations: { type: 'array', items: { type: 'string' } },
      },
    },
    scorecard: {
      type: 'object',
      additionalProperties: false,
      required: ['overall', 'subscores', 'coverage', 'policyVersion', 'explanation', 'insufficientData'],
      properties: {
        overall: { anyOf: [{ type: 'integer', minimum: 1, maximum: 5 }, { type: 'null' }] },
        subscores: {
          type: 'object',
          additionalProperties: false,
          required: ['experience', 'skills', 'hrPreferences', 'evidenceQuality'],
          properties: {
            experience: { anyOf: [{ type: 'integer', minimum: 1, maximum: 5 }, { type: 'null' }] },
            skills: { anyOf: [{ type: 'integer', minimum: 1, maximum: 5 }, { type: 'null' }] },
            hrPreferences: { anyOf: [{ type: 'integer', minimum: 1, maximum: 5 }, { type: 'null' }] },
            evidenceQuality: { anyOf: [{ type: 'integer', minimum: 1, maximum: 5 }, { type: 'null' }] },
          },
        },
        coverage: { type: 'number', minimum: 0, maximum: 1 },
        policyVersion: { type: 'string', minLength: 1 },
        explanation: { type: 'array', items: { type: 'string' } },
        insufficientData: { type: 'boolean' },
      },
    },
    questions: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'question', 'source', 'evidenceRefs'],
        properties: {
          id: { type: 'string', minLength: 1 },
          question: { type: 'string', minLength: 1 },
          source: { enum: ['fact', 'requirement', 'signal', 'ai_pattern'] },
          evidenceRefs: { type: 'array', items: { $ref: '#/$defs/evidenceRef' } },
        },
      },
    },
    limitations: { type: 'array', items: { type: 'string' } },
  },
  $defs: {
    evidenceRef: {
      type: 'object',
      additionalProperties: false,
      required: ['sourceId', 'blockId', 'start', 'end', 'quote'],
      properties: {
        sourceId: { type: 'string', minLength: 1 },
        blockId: { type: 'string', minLength: 1 },
        page: { type: 'integer', minimum: 1 },
        start: { type: 'integer', minimum: 0 },
        end: { type: 'integer', minimum: 1 },
        quote: { type: 'string', minLength: 1 },
      },
    },
    featureNote: {
      type: 'object',
      additionalProperties: false,
      required: ['label', 'detail', 'evidenceRefs'],
      properties: {
        label: { type: 'string', minLength: 1 },
        detail: { type: 'string', minLength: 1 },
        evidenceRefs: { type: 'array', items: { $ref: '#/$defs/evidenceRef' } },
      },
    },
    requirementMatch: {
      type: 'object',
      additionalProperties: false,
      required: ['requirementId', 'status', 'factIds', 'evidenceRefs', 'explanation'],
      properties: {
        requirementId: { type: 'string', minLength: 1 },
        status: { enum: ['matched', 'partial', 'missing', 'unknown', 'excluded'] },
        factIds: { type: 'array', items: { type: 'string' } },
        evidenceRefs: { type: 'array', items: { $ref: '#/$defs/evidenceRef' } },
        explanation: { type: 'string', minLength: 1 },
      },
    },
    reviewSignal: {
      type: 'object',
      additionalProperties: false,
      required: [
        'id',
        'kind',
        'observation',
        'evidenceRefs',
        'alternativeExplanation',
        'interviewQuestion',
        'severity',
      ],
      properties: {
        id: { type: 'string', minLength: 1 },
        kind: {
          enum: [
            'impossible_date',
            'period_overlap',
            'employment_gap',
            'cross_document_discrepancy',
            'level_mismatch',
            'unverified_claim',
            'suspicious_contact',
          ],
        },
        observation: { type: 'string', minLength: 1 },
        evidenceRefs: { type: 'array', items: { $ref: '#/$defs/evidenceRef' } },
        alternativeExplanation: { type: 'string', minLength: 1 },
        interviewQuestion: { type: 'string', minLength: 1 },
        severity: { enum: ['info', 'attention'] },
      },
    },
  },
} as const;

export type CandidateReportSchema = typeof candidateReportSchema;
