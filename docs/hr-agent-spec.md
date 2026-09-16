# HR-агент Extella: спецификация реализации MVP

Реализовано поверх дорожной карты `docs/hr-agent-roadmap.md` (этапы M0–M1, частично M2–M3).
Документ описывает то, что есть в коде, а не желаемую архитектуру.

## Границы и инварианты

Полный список решений — `docs/decisions/0001-hr-analysis-boundary.md`. Ключевое:

- детерминированный слой считает факты, соответствие требованиям, сигналы и оценку;
- модель пока не подключена к конвейеру; заготовка `ExpertPort` не меняет балл и статус;
- защищённые признаки не влияют ни на одно число;
- нет цитаты — нет утверждения; отсутствие упоминания — `unknown`, а не `missing`;
- ссылки читаются по SSRF-политике, логины/пароли агент не принимает;
- один пакет — один кандидат.

## Публичный API (`src/index.ts`)

Основной вход:

```ts
import { analyzeApplication, createAnalysisSession } from './src/index.js';

const result = await analyzeApplication(request, { browser, extractor, extella }, signal);
```

- `analyzeApplication(request: AnalysisRequest, ports: AnalysisPorts, signal?: AbortSignal): Promise<AnalysisResult>`
- `createAnalysisSession(ports): AnalysisSession` — идемпотентность по `requestId`:
  `analyze()` присоединяется к уже идущему запуску и отдаёт кэш завершённого;
  `activeCount()`, `clear(requestId)`.

`AnalysisPorts`:

| Порт | Назначение | Заглушки |
| --- | --- | --- |
| `browser?: BrowserPort` | чтение присланных ссылок | `createInMemoryBrowserPort`, `createManualBrowserPort`, `createUnavailableBrowserPort` |
| `extractor?: DocumentExtractorPort` | извлечение текста из PDF/DOCX/EML | реализация зависит от доверенного контура Extella |
| `extella?: ExtellaPort` | прогресс, карточка, запрос действия HR | `createInMemoryExtellaPort`, `createUnavailableExtellaPort` |

`ExpertPort` и его тестовые реализации экспортируются как заготовка будущей интеграции,
но в `AnalysisPorts` не входят и текущим конвейером не вызываются.

## Конвейер

`src/core/analyze-application.ts` выполняет стадии в фиксированном порядке, отмечая каждую
в `stageStatuses` (`pending|running|completed|skipped|failed|cancelled`):

`intake → extraction → normalization → matching → integrity → ai_patterns → scoring → composition → validation`

Основной детерминированный расчёт — `analyzeText(requestId, sources)` в
`src/analysis/pipeline.ts`: `requirements → facts → matching → integrity → ai-patterns →
scoring → compose → validate`.

### Источники и извлечение (`src/intake/`)

- `validateAnalysisRequest` — лимиты `INPUT_LIMITS`: 10 вложений, 20 MiB на файл,
  50 MiB на пакет, 100 страниц, 200 000 извлечённых символов; проверка media type,
  base64 и заявленных размеров. Пакет из одних ссылок допустим (warning), пустой — нет.
- `extractDocument(s)` — извлечение через `DocumentExtractorPort`, статусы
  `extracted | partial | unsupported_file | unreadable_document | resource_limit | empty`,
  цитаты привязываются к `blockId`, сохраняется `sha256`.
- Поддерживаются текстовые форматы; PDF/DOCX требуют порта извлечения.

### Приватность (`src/privacy/`)

- `redactForModel` / `redactSourcesForModel` — минимизация PII перед моделью, но **не**
  анонимизация: контакты нужны HR. Redacted-копии нельзя использовать как evidence.
- `createTemporaryInputStore` — временные вложения в `mkdtemp` с `mode 0o600`, TTL
  (по умолчанию 1 час), `cleanupExpired()` / `cleanupAll()`.

### Анализ (`src/analysis/`)

- `requirements.ts` — `parseJob`: must/nice-to-have, зарплата, локация, формат, язык;
  защищённые признаки отделяются в `eligibility: excluded_protected`.
- `facts.ts` — навыки, опыт (`mergeExperienceMonths` не считает параллельные периоды дважды),
  языки, локация, доступность, зарплатные ожидания, `parseCandidateName`.
- `matching.ts` — статусы `matched | partial | missing | unknown` и `parseSalaryRange`.
- `integrity.ts` — накладки периодов, невозможные даты, пробелы и неподтверждённые метрики;
  каждый сигнал с цитатой и уровнем уверенности, без обвинений и по защищённым признакам.
- `ai-patterns.ts` — `reviewTextPatterns` возвращает `{ assessable, observations, hypothesis,
  limitations }`; авторство не устанавливается, балл не меняется.
- `policy.ts` / `scoring.ts` — политика из `config/scoring-policy.json` (v1.0.0):
  покрытие ниже 0.7 или отсутствие вакансии ⇒ `overall = null`, `insufficientData = true`;
  подтверждённое несоответствие must-have ограничивает балл (`caps`), а не понижает молча.
- `report/compose.ts` + `report/validate.ts` — карточка и проверка каждой цитаты против
  реальных блоков источников. Полный пакет обычно даёт 5–8 вопросов; при нехватке
  подтверждённых фактов конвейер не добивает количество выдуманными вопросами.

### Контракты (`src/contracts/`)

Типы и `SCHEMA_VERSION = '1.0.0'` в `analysis.ts`, JSON Schema в `analysis.schema.ts`
(TS-модуль, см. ADR), валидация Ajv в `validate-schema.ts`.

Коды ошибок: `unsupported_file`, `unreadable_document`, `resource_limit`, `empty_input`,
`login_required`, `source_unavailable`, `integration_unavailable`, `model_timeout`,
`invalid_model_output`, `cancelled`, `invalid_request`, `internal_error`.

### Статусы результата

`completed` — карточка валидна и все источники прочитаны; `partial` — карточка есть, но часть
источников не прочитана (`recoverable: true`); `waiting_for_user` — читать нечего и нужен вход
HR (вызывается `requestUserAction`); `failed` — карточки нет; `cancelled` — отмена по сигналу.

Карточка (`CandidateReport`) содержит соответствие требованиям, сильные/слабые стороны,
красные/зелёные флаги, AI-чек, 5–8 вопросов интервью, оценку 1–5 с покрытием и ограничения.
`renderCandidateReportMarkdown` экранирует недоверенный Markdown/HTML; отдельный
`sanitizeUrlForDisplay` предназначен для неактивного отображения URL.

## Конфигурация

- `config/scoring-policy.json` — версия, веса, значения match, `coverage.minimumForOverall`,
  диапазоны `overallBands`, `caps`, подгруппы subscores.

## Тесты и проверки

```bash
npm run lint        # tsc --noEmit
npm test            # vitest run
```

Покрытие (121 тест, 9 файлов):

- `tests/contracts/schema.test.ts` — контракт и схема;
- `tests/unit/{text,evidence-validation,analysis,compose,intake,privacy,adapters}.test.ts` —
  ядро, карточка, intake/лимиты, приватность, порты браузера/эксперта/Extella;
- `tests/integration/analyze-application.test.ts` — полный конвейер: completed, partial,
  waiting_for_user, failed, cancelled, ссылки readable/needs_login/запрещены, отсутствие
  браузера, вакансия без требований, несколько кандидатов, детерминизм, идемпотентность.

## Что не реализовано (осознанно)

- реальные интеграции Extella bridge/очереди и парсеры PDF/DOCX (только порты и заглушки);
- UI/panel, multi-tenant авторизация, retention/audit-хранилище;
- модель как источник объяснений (промпт `prompts/hr-review.md` готов, порт — заглушка);
- M4–M5: HR-workflow, ATS-экшены, пилот и fairness-аудит.
