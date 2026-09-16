import type { EvidenceRef, SourceDocument, TextBlock } from '../contracts/analysis.js';
import { locateQuote } from './text.js';

export function findBlock(
  sources: readonly SourceDocument[],
  sourceId: string,
  blockId: string,
): { source: SourceDocument; block: TextBlock } | null {
  const source = sources.find((candidate) => candidate.id === sourceId);
  if (source === undefined) {
    return null;
  }
  const block = source.textBlocks.find((candidate) => candidate.id === blockId);
  if (block === undefined) {
    return null;
  }
  return { source, block };
}

/**
 * Build an evidence reference by locating `quote` verbatim in one of the
 * source blocks. Returns null when the quote does not occur, so callers never
 * emit an unverifiable citation.
 */
export function makeEvidenceRef(
  sources: readonly SourceDocument[],
  quote: string,
  preferredSourceIds: readonly string[] = [],
): EvidenceRef | null {
  const ordered = [
    ...sources.filter((source) => preferredSourceIds.includes(source.id)),
    ...sources.filter((source) => !preferredSourceIds.includes(source.id)),
  ];
  for (const source of ordered) {
    for (const block of source.textBlocks) {
      const location = locateQuote(block.text, quote);
      if (location === null) {
        continue;
      }
      return {
        sourceId: source.id,
        blockId: block.id,
        ...(block.page === undefined ? {} : { page: block.page }),
        start: location.start,
        end: location.end,
        quote: block.text.slice(location.start, location.end),
      };
    }
  }
  return null;
}

export function collectEvidenceRefs(report: {
  matches: readonly { evidenceRefs: readonly EvidenceRef[] }[];
  strengths: readonly { evidenceRefs: readonly EvidenceRef[] }[];
  gaps: readonly { evidenceRefs: readonly EvidenceRef[] }[];
  redFlags: readonly { evidenceRefs: readonly EvidenceRef[] }[];
  greenFlags: readonly { evidenceRefs: readonly EvidenceRef[] }[];
  features: readonly { evidenceRefs: readonly EvidenceRef[] }[];
  aiCheck: { observations: readonly { evidenceRefs: readonly EvidenceRef[] }[] };
  questions: readonly { evidenceRefs: readonly EvidenceRef[] }[];
}): EvidenceRef[] {
  const groups: (readonly EvidenceRef[])[] = [
    ...report.matches.map((item) => item.evidenceRefs),
    ...report.strengths.map((item) => item.evidenceRefs),
    ...report.gaps.map((item) => item.evidenceRefs),
    ...report.redFlags.map((item) => item.evidenceRefs),
    ...report.greenFlags.map((item) => item.evidenceRefs),
    ...report.features.map((item) => item.evidenceRefs),
    ...report.aiCheck.observations.map((item) => item.evidenceRefs),
    ...report.questions.map((item) => item.evidenceRefs),
  ];
  return groups.flat();
}
