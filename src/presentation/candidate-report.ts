import type { CandidateReport } from '../contracts/analysis.js';

const HTML_ESCAPES: Readonly<Record<string, string>> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

/** Escapes untrusted resume/page text before it reaches an HTML surface. */
export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => HTML_ESCAPES[character] ?? character);
}

/** Neutralizes a quoted URL so a card can never emit an active link. */
export function sanitizeUrlForDisplay(value: string): string {
  return escapeHtml(value.replace(/[\u0000-\u001f\u007f]/g, ''));
}

function stars(overall: number | null): string {
  return overall === null ? '—' : `${overall}/5`;
}

/**
 * Plain markdown card for Extella. All source-derived text is passed through
 * `escapeHtml`-equivalent escaping of markdown control characters and no
 * active content is produced.
 */
export function renderCandidateReportMarkdown(report: CandidateReport): string {
  const lines: string[] = [];
  const escape = (value: string): string =>
    escapeHtml(value).replace(/[*_`[\]()#+\-.!|>\\]/g, (character) => `\\${character}`);

  lines.push(`# ${escape(report.header.name ?? 'Кандидат')} — ${escape(report.header.role ?? 'роль не указана')}`);
  lines.push('');
  lines.push(`**Оценка:** ${stars(report.scorecard.overall)} — ${escape(report.header.verdict)}`);
  lines.push('');
  lines.push('## Сильные стороны');
  lines.push(report.strengths.length === 0 ? '- Не подтверждено.' : report.strengths.map((note) => `- ${escape(note.label)}: ${escape(note.detail)}`).join('\n'));
  lines.push('');
  lines.push('## Слабые стороны и пробелы');
  lines.push(report.gaps.length === 0 ? '- Явных пробелов не выявлено.' : report.gaps.map((note) => `- ${escape(note.label)}: ${escape(note.detail)}`).join('\n'));
  lines.push('');
  lines.push('## Красные флаги');
  lines.push(report.redFlags.length === 0 ? '- Не выявлено.' : report.redFlags.map((signal) => `- ${escape(signal.observation)} (альтернатива: ${escape(signal.alternativeExplanation)})`).join('\n'));
  lines.push('');
  lines.push('## AI-чек');
  lines.push(report.aiCheck.assessable ? report.aiCheck.observations.map((observation) => `- ${escape(observation.observation)}`).join('\n') || '- Наблюдений нет.' : '- Данных мало, авторство установить нельзя.');
  lines.push('');
  lines.push('## Вопросы для интервью');
  lines.push(report.questions.map((question, index) => `${index + 1}. ${escape(question.question)}`).join('\n'));
  lines.push('');
  lines.push('## Ограничения');
  lines.push(report.limitations.map((limitation) => `- ${escape(limitation)}`).join('\n'));
  lines.push('');
  lines.push(`_Схема ${escape(report.schemaVersion)}, разбор ${escape(report.requestId)}. Решение принимает HR._`);
  return lines.join('\n');
}
