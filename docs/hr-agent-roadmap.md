# Дорожная карта HR-агента Extella (исследование 2026-09-15)

> **Статус.** Документ описывает исследование и план. Реализованный объём MVP — в
> `docs/hr-agent-spec.md`, принятые границы и отклонения — в
> `docs/decisions/0001-hr-analysis-boundary.md`. Этапы M4–M5 и реальные интеграции остаются
> невыполненными.

## Объём исследования и граница

Изучены исходники `/home/kukuruza/orca/extella-seo-employee` (Extella SEO Employee 2.0.3), его контракты, queue/state/service/server, automation passport и bridge. Локальный OfferClawProject как каталог исходников не найден. Найден полный архив `/home/kukuruza/.cache/offerclaw-migration-20260913/offerclaw-ct165.tar.zst`; индекс содержит 306925 строк и подтверждает эксплуатационный контур CT165 (`/opt/offerclaw-next`), но архив без извлечения исходников не позволяет утверждать детали реализации. По owner memory это Telegram-ассистент поиска вакансий, сборщик, модератор, браузерный автоотклик, PostgreSQL и OmniRoute; старый CT160-контур остановлен и удалён. Это не доказательство, что архив — названный пользователем OfferClawProject.

## Что переиспользовать из Extella

- Extella bridge H62: `etb_run_expert`/`etb_expert_result`, allowlist экспертов, device/agent binding, timeout и нормализация обёрток.
- Детерминированный backend до модели: versioned schemas, atomic JSON state, FIFO queue, bounded body/response, fixed routes, rate limit, audit log.
- Модель получает только очищенные факты; результат модели валидируется контрактом, а недоступность модели не уничтожает детерминированный результат.
- UI должен показывать coverage, источник каждого факта, ограничения, статус очереди, aria-live, ru/en и запрет внешних действий по умолчанию.

## Целевой пользовательский поток

1. HR создаёт вакансию (описание, must/nice-to-have, зарплата, локация, формат, язык, legal policy).
2. Система принимает отклик (резюме, письмо, ссылки, metadata источника), вычисляет hash и сохраняет оригинал с retention policy.
3. Детерминированный слой извлекает факты из CV, нормализует навыки/даты/работодателей, сравнивает требования и выявляет противоречия.
4. Слой проверки честности формирует проверяемые сигналы: временные накладки, невозможные даты, дубли текстов, несоответствие уровня, подозрительные ссылки/контакты, неподтверждённые claims. Он не объявляет ложь по стилю или защищённому признаку.
5. Опциональные источники (с согласованным правовым основанием) проверяют публичные профили, домены, документы или тестовые ответы; каждый сигнал имеет source, evidence, confidence и способ ручной проверки.
6. Модель объясняет только подтверждённые сигналы и выдаёт structured verdict: `advance`, `manual_review`, `reject` с причинами и вопросами интервью.
7. HR видит карточку кандидата, evidence, ограничения и следующий шаг; любые сообщения, тесты и запись в ATS — отдельный action proposal с явным подтверждением.

## Версия MVP и этапы

**M0 — контракты и политика (1 неделя).** Зафиксировать роли HR/агентство/админ, tenant isolation, retention/deletion, consent/legal basis, запрещённые признаки, критерии fairness, schema candidate/job/application/verdict/evidence/action-proposal, threat model и набор gold cases.

**M1 — intake и deterministic screening (2–3 недели).** Extella panel + bridge; загрузка PDF/DOCX/текста с malware/size limits; PII classification/redaction; CV parser; job parser; skill/date normalization; weighted match score; explainable missing/matched requirements; queue/state/retry/idempotency; audit envelope.

**M2 — integrity checks (2–3 недели).** Rules engine противоречий (даты, overlap, title/seniority, geography), duplicate/plagiarism similarity, URL/domain checks, metadata provenance. Ввести `signal` вместо «обмана», confidence bands и mandatory human review для adverse decisions. Создать negative controls и adversarial fixtures.

**M3 — model explanation (1–2 недели).** Agent Zero expert с жёстким JSON schema, очищенным входом и prompt-injection isolation. Model failure → verdict сохраняется без explanation. Модель не меняет статус и не отправляет коммуникации.

**M4 — HR workflow (2 недели).** Search/filter, shortlist, comparison, interview question generator grounded in evidence, notes, export; proposals для ATS/email только после подтверждения. Multi-tenant authorization и immutable audit trail.

**M5 — pilot and validation (2–4 недели).** Shadow run на обезличенных исторических откликах, calibration precision/recall, false-positive rate, subgroup fairness audit, latency/cost, human override rate, red-team privacy/security, backup/restore, full E2E на выбранной модели и Extella device.

## Разделение на субагентов исполнения (после отдельного одобрения реализации)

1. **contracts-policy (full-cycle/Astra):** schemas, legal/fairness policy, threat model, acceptance matrix; не писать runtime.
2. **intake-parser (worker/Luna):** upload/API, PDF/DOCX extraction, PII redaction, provenance; не решать честность.
3. **screening-engine (worker/Luna):** deterministic matching, normalization, scoring and evidence; fixtures/gold tests.
4. **integrity-signals (full-cycle/Astra):** contradiction/duplicate/public verification adapters, signal schema and adversarial tests; no auto-reject.
5. **extella-ui-bridge (worker/Luna):** panel, H62 bridge, bilingual accessibility, queue/status/evidence views.
6. **model-enrichment (worker/Luna):** isolated Agent Zero expert, JSON validator, prompt-injection boundary, unavailable-model fallback.
7. **security-data (full-cycle/Astra):** tenant authz, retention/deletion, secrets, SSRF/file safety, audit, backup/restore.
8. **pilot-evaluation (full-cycle/Astra):** anonymized benchmark, metrics, fairness/error analysis, E2E and release gate.

Each worker receives exact files/contracts and acceptance commands; implementation must not begin until owner approves the roadmap and architecture forks (storage and verification policy).

## Архитектурные развилки владельца

- **Хранилище:** PostgreSQL (multi-tenant querying/audit) vs file-backed MVP (быстрее, слабее concurrent/tenant guarantees).
- **Проверка честности:** evidence-first risk signals + human review (рекомендуется) vs automated reject (быстрее, высокий legal/fairness risk).
- **Источники:** только присланные документы (privacy-first) vs публичные профили/веб (больше сигналов, consent/SSRF/cost).
- **Интеграции:** proposal-only/manual first vs ATS/email write adapters; второй вариант требует отдельного подтверждения каждого действия.

## Критерии готовности пилота

No P0/P1 security/privacy; tenant isolation tested; 100% verdict reasons link to evidence or explicit `unknown`; model cannot add unsupported claims; deterministic result survives model outage; adverse decision always human-reviewable; deletion and export work; documented retention; E2E through exact Extella route; metrics and limitations visible.

## Незакрытые вопросы

1. Указать точный путь/репозиторий OfferClawProject и подтвердить, нужен ли перенос кода или только идеи.
2. Целевой рынок и юрисдикции; допустимые источники проверки и сроки хранения.
3. ATS/email/Telegram integrations and tenant model.
4. Expected volume and SLA; benchmark dataset availability.
