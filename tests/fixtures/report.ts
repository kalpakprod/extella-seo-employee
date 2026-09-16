import type { CandidateReport } from '../../src/contracts/analysis.js';
import { SCHEMA_VERSION } from '../../src/contracts/analysis.js';

export function makeValidReport(overrides: Partial<CandidateReport> = {}): CandidateReport {
  const base: CandidateReport = {
    schemaVersion: SCHEMA_VERSION,
    requestId: 'req-1',
    header: { name: 'Иван Петров', role: 'Senior Backend Engineer', verdict: 'Подходит' },
    matches: [
      {
        requirementId: 'req-node',
        status: 'matched',
        factIds: ['fact-1'],
        evidenceRefs: [
          {
            sourceId: 'res-1',
            blockId: 'res-1-b0',
            start: 0,
            end: 3,
            quote: 'Ива',
          },
        ],
        explanation: 'Node.js подтверждён.',
      },
    ],
    strengths: [],
    gaps: [],
    redFlags: [],
    greenFlags: [],
    features: [],
    aiCheck: {
      assessable: true,
      observations: [],
      hypothesis: 'undetermined',
      limitations: ['Авторство по тексту не устанавливается.'],
    },
    scorecard: {
      overall: 4,
      subscores: { experience: 4, skills: 5, hrPreferences: 4, evidenceQuality: 3 },
      coverage: 1,
      policyVersion: '1.0.0',
      explanation: ['Покрытие требований 100%.'],
      insufficientData: false,
    },
    questions: [],
    limitations: [],
  };
  return { ...base, ...overrides };
}
