import type {
  AiPatternObservation,
  AiPatternReview,
  EvidenceRef,
  SourceDocument,
} from '../contracts/analysis.js';
import { makeEvidenceRef } from './evidence.js';
import { fold, normalizeText, splitSentences } from './text.js';

const TEMPLATE_PHRASES = [
  'командный игрок',
  'стрессоустойчив',
  'ориентирован на результат',
  'нацелен на результат',
  'легко обучаюсь',
  'быстро адаптируюсь',
  'ответственный подход',
  'многозадачность',
  'активная жизненная позиция',
];

const HUMANIZER_MARKERS = [
  'честно говоря',
  'если честно',
  'на самом деле',
  'короче',
  'в общем-то',
  'по-простому',
  'без воды',
  'если по делу',
  'скажу так',
];

const APPLICATION_KINDS = new Set(['resume', 'cover_letter', 'email_application', 'other']);

const LIMITATIONS = [
  'По тексту нельзя достоверно установить, написан ли он человеком, моделью или отредактирован человеком.',
  'Шаблонная структура, хороший язык и отсутствие цифр сами по себе не доказывают AI-авторство.',
  'Наблюдения — это сигнал для интервью, а не приговор; возможны ложные срабатывания.',
];

function applicationDocuments(sources: readonly SourceDocument[]): SourceDocument[] {
  return sources.filter((source) => APPLICATION_KINDS.has(source.inputKind));
}

function sentencesOf(sources: readonly SourceDocument[]): { sentence: string; sourceId: string }[] {
  return applicationDocuments(sources).flatMap((source) =>
    source.textBlocks.flatMap((block) =>
      splitSentences(block.text).map((sentence) => ({ sentence, sourceId: source.id })),
    ),
  );
}

function averageSentenceLength(sentences: readonly string[]): number {
  if (sentences.length === 0) {
    return 0;
  }
  const words = sentences.map((sentence) => sentence.split(/\s+/).length);
  return words.reduce((total, count) => total + count, 0) / words.length;
}

function collectRefs(
  sources: readonly SourceDocument[],
  quotes: readonly { sentence: string; sourceId: string }[],
): EvidenceRef[] {
  const refs: EvidenceRef[] = [];
  for (const entry of quotes) {
    const ref = makeEvidenceRef(sources, entry.sentence, [entry.sourceId]);
    if (ref !== null) {
      refs.push(ref);
    }
  }
  return refs;
}

export function reviewTextPatterns(sources: readonly SourceDocument[]): AiPatternReview {
  const documents = applicationDocuments(sources);
  const allText = documents
    .flatMap((document) => document.textBlocks.map((block) => block.text))
    .map(normalizeText)
    .join('\n');
  const sentences = sentencesOf(sources);
  const assessable = allText.length >= 200;
  const observations: AiPatternObservation[] = [];

  if (!assessable) {
    return {
      assessable: false,
      observations: [],
      hypothesis: 'undetermined',
      limitations: [
        'Материалов отклика слишком мало для наблюдений о стиле; авторство установить нельзя.',
        ...LIMITATIONS,
      ],
    };
  }

  const foundTemplates = TEMPLATE_PHRASES.filter((phrase) => fold(allText).includes(phrase));
  if (foundTemplates.length > 0) {
    const quotes = sentences.filter((entry) =>
      foundTemplates.some((phrase) => fold(entry.sentence).includes(phrase)),
    );
    observations.push({
      category: 'template_phrases',
      observation: `Найдены шаблонные формулировки: ${foundTemplates.join(', ')}.`,
      evidenceRefs: collectRefs(sources, quotes),
      confidence: 'low',
    });
  }

  const achievementSentences = sentences.filter(
    (entry) =>
      /(ответственн|результат|команд|обуча|адапт|мотивирован|проект)/i.test(entry.sentence) &&
      !/\d/.test(entry.sentence),
  );
  if (achievementSentences.length >= 2) {
    observations.push({
      category: 'no_concrete_metrics',
      observation:
        'Есть утверждения о качествах и результатах без измеримых цифр (примеры приведены в цитатах).',
      evidenceRefs: collectRefs(sources, achievementSentences.slice(0, 3)),
      confidence: 'low',
    });
  }

  const resumeSentences = documents
    .filter((document) => document.inputKind === 'resume')
    .flatMap((document) => document.textBlocks.flatMap((block) => splitSentences(block.text)));
  const coverSentences = documents
    .filter((document) => document.inputKind === 'cover_letter')
    .flatMap((document) => document.textBlocks.flatMap((block) => splitSentences(block.text)));
  if (resumeSentences.length > 0 && coverSentences.length > 0) {
    const difference = Math.abs(
      averageSentenceLength(resumeSentences) - averageSentenceLength(coverSentences),
    );
    if (difference < 3) {
      observations.push({
        category: 'uniform_style_across_documents',
        observation: 'Резюме и сопроводительное письмо имеют почти одинаковую длину и ритм предложений.',
        evidenceRefs: collectRefs(sources, [
          { sentence: resumeSentences[0]!, sourceId: documents.find((d) => d.inputKind === 'resume')!.id },
          { sentence: coverSentences[0]!, sourceId: documents.find((d) => d.inputKind === 'cover_letter')!.id },
        ]),
        confidence: 'low',
      });
    }
  }

  const shingleCounts = new Map<string, { count: number; entry: { sentence: string; sourceId: string } }>();
  for (const entry of sentences) {
    const words = fold(entry.sentence).split(/\s+/);
    for (let index = 0; index + 5 <= words.length; index += 1) {
      const shingle = words.slice(index, index + 5).join(' ');
      const existing = shingleCounts.get(shingle);
      if (existing === undefined) {
        shingleCounts.set(shingle, { count: 1, entry });
      } else {
        shingleCounts.set(shingle, { count: existing.count + 1, entry: existing.entry });
      }
    }
  }
  const repeated = [...shingleCounts.values()].filter((value) => value.count > 1);
  if (repeated.length > 0) {
    observations.push({
      category: 'repetition',
      observation: 'В тексте повторяются одинаковые фразы из пяти слов.',
      evidenceRefs: collectRefs(sources, [repeated[0]!.entry]),
      confidence: 'low',
    });
  }

  const foundHumanizers = HUMANIZER_MARKERS.filter((marker) => fold(allText).includes(marker));
  if (foundHumanizers.length > 0) {
    const quotes = sentences.filter((entry) =>
      foundHumanizers.some((marker) => fold(entry.sentence).includes(marker)),
    );
    observations.push({
      category: 'humanizer_markers',
      observation: `Найдены разговорные вставки, характерные для «хьюманайзеров»: ${foundHumanizers.join(', ')}.`,
      evidenceRefs: collectRefs(sources, quotes),
      confidence: 'low',
    });
  }

  const categories = new Set(observations.map((observation) => observation.category));
  const hasHumanizer = categories.has('humanizer_markers');
  const hypothesis: AiPatternReview['hypothesis'] =
    observations.length === 0
      ? 'undetermined'
      : hasHumanizer && categories.size >= 2
        ? 'likely_ai_humanized'
        : categories.size >= 3
          ? 'likely_ai'
          : 'mixed';

  return {
    assessable: true,
    observations,
    hypothesis,
    limitations: LIMITATIONS,
  };
}
