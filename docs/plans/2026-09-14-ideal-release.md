# Ideal Release Plan: SEO Employee → 2.1.0 без оговорок

## Goal

Один консолидированный план идеального релиза: закрытые hygiene-блокеры, закалённый
контур, честные verdicts (data quality из deep-research), новые данные (P1 полностью,
P2 по гейтам), воспроизводимый гейт 2.1.0, опубликованный листинг и закрытая коммерция.
После аппрува этот план — единый исполнительный; `production-release.md`,
`production-readiness.md` и синтез deep-research остаются справочными приложениями.

## Success Criteria

1. Hygiene: секретов в дереве/истории нет, токен отозван и ротируется процедурой с drill.
2. Hardening: rate limits (429), access-логи без секретов, расширенный health/метрики,
   TLS на границе, `agent-zero` зажат или исключение задокументировано, бэкапы с
   расписанием/RPO/RTO и пройденным drill, стейджинг отвечает извне.
3. Data quality: `unmapped_rules` пуст на E2E-корпусе; `warn` находки глотаются и не
   теряются; severity градуирована (не «всё critical»); счётчики страниц/покрытия
   честные; E2E-базлайн перезаписан после изменений verdicts.
4. Новые данные: PSI/CrUX-лейн и robots/sitemap/schema-парсеры в продукте (stdlib-only);
   GSC-, W3C Nu- и security-лейны подключены; DataForSEO — решением по бюджету.
5. Гейт 2.1.0: версия сходится везде, сюиты зелёные, сборка детерминирована, `selfcheck →
   READY`, точный архив прогнан (тесты + live probe + backup-check), чистая комната
   пройдена, upstream-гейты 12/12, release notes без неподтверждённых цифр.
6. Магазин и коммерция: листинг опубликован человеком, self-purchase и побайтовый SHA-256
   сошлись, права/проценты/rails подписаны, deck добран, вердикт сменён с
   `SHIP_CLOSED_PILOT`.

## Context And Current Facts

Проверено чтением кода и живыми прогонами 2026-09-14:

- Ветка `release/2.1.0`, Python 184/184 OK, Node 64/64, `selfcheck.py → READY`.
- E2E `full_audit` 25/25 страниц, 10 critical, evidence-пак в `evidence/`; model
  enrichment 6/10 `unavailable` с детерминированным fallback.
- Версия везде 2.0.3; `prepare.py` без ротации; `server.py`/`gateway.py` без лимитов и
  метрик, `log_message` no-op; `agent-zero` (`compose.yaml:113-128`) без
  `read_only`/`cap_drop`/`healthcheck`/`user`; `tools/publish_store.py` отсутствует;
  бэкапы без расписания и RPO/RTO. Вердикт `README`: `SHIP_CLOSED_PILOT`.
- Швы data quality подтверждены в коде: `_known_rule` (`seo_employee_sources.py:236`,
  вызовы :343/:452) дропает неизвестные правила в `unmapped_rules`; ingest SEOmator
  (`:409-410`) пропускает всё кроме `fail`; сэмпл perf зажат
  (`seo_employee_profiles.py:96`, `min(max_pages, 5)`); enrichment валидируется строго
  (`seo_employee_service.py:407-456`, любая грязь → `SeoEmployeeError` → fallback).
- Deep-research workflow (статус completed, 6 агентов, синтез с критиком): 10
  предложений P0–P2; внешние кандидаты привязаны детьми к официальным докам
  (PSI/CrUX, RFC 9309, sitemaps.org, schema.org, GSC API, W3C Nu, SecurityHeaders,
  SSL Labs, DataForSEO, Common Crawl). Я перепроверил P0-суть в коде; построчные
  ссылки детей местами неточны, внешние URL при исполнении перепроверить.
- Не перепроверялось сегодня (из сессии): `FORBIDDEN_TIER` на self-purchase,
  засвеченный токен, GSC/OAuth/BYOK `not_configured`.

## Constraints And Non-goals

- Контракты репо: модель выбирает пользователь, авто-fallback запрещён, no-tools для
  Agent Zero, продуктовый Python — только stdlib.
- Магазинная версия неизменяема (H8): всё чинится до создания 2.1.0 в сторе.
- `evidence/` — дом E2E-паков, золотые фикстуры гейтов не трогать.
- Non-goals: SaaS-мультитенантность/RBAC/биллинг, запись в CMS, зашивание моделей,
  DonSeTch-интеграция.
- Идеальность не означает бесконечность: P2-лейны за гейтами, у каждого — критерий
  «входит / отложен» до начала работ.

## Key Decisions

- **I1. Единый исполнительный план.** Причина: три документа (release, readiness,
  research) расходятся по фазам; исполнению нужен один порядок. Остальные — приложения.
- **I2. P0 data quality — релиз-блокер.** Причина: идеальный релиз не может молча
  дропать находки (unmapped) и красить всё в critical — это честность verdicts, ядро
  продукта. Входит до гейта без гейта.
- **I3. P1 входит полностью.** PSI/CrUX-лейн и robots/sitemap/schema-парсеры — stdlib,
  дёшево, закрывают объективность perf и crawl-категорий. Альтернатива «только P0»
  отвергнута: это план идеального, а не минимального релиза.
- **I4. P2 — по гейтам.** GSC, W3C Nu, SecurityHeaders/SSL входят (GSC — тянет OAuth-аппрувы
  как явные задачи); DataForSEO — только решением по бюджету/ключу (open question);
  Common Crawl — excerpts-only. Каждый лейн: optional, capped, non-blocking, fail →
  `unavailable` с честным `missing_data`, никогда тихий пропуск.
- **I5. Enrichment: bounded retry, fallback сохранён.** Два ретрая с таймаутом поверх
  строгой валидации; провал — детерминированный fallback как сейчас. Причина: поднимает
  6/10 без риска для honesty-контракта. Больше двух — нет (не маскировать мёртвый
  провайдер).
- **I6. Verdicts меняются → базлайн переписывается.** После Phase 2 E2E-пак и затронутые
  золотые тесты обновляются осознанно, с записью «стало/было» в evidence. Причина: иначе
  гейт будет лгать.
- **I7. Версия 2.1.0, коммиты пофазно.** Minor-версия покрывает новые лейны; схлопывать
  фазовые коммиты запрещено (связывает исполнение).

## Recommended Approach

Строгий порядок: hygiene → hardening → data quality (P0+P1) → P2-гейты → гейт 2.1.0 →
магазин + коммерция. Данные чинятся до гейта, потому что меняют verdicts; магазин —
последним, потому что версия неизменяема. Человек ведёт токен, OAuth-аппрувы, бюджет
DataForSEO, Publish и коммерцию.

## Work Plan

### Phase 0 — Hygiene и заморозка, P0 (сегодня)

- **H0.1 Токен.** Человек отзывает засвеченный; агент ротирует файловый секрет
  (старый → 401, новый → 200), добавляет команду ротации в `prepare.py` + док с drill.
- **H0.2 Дерево.** Два коммита на `release/2.1.0`: (1) доки/evidence/CI, (2) код и
  сборки. Пуш — по явной команде. Плюс этот план в коммит (1).
- **H0.3 Freeze.** Вне плана — только в Wave 6+.
- Приёмка: `git status` пуст; drill ротации; grep секретов по `git log -p` пуст.

### Phase 1 — Hardening, P1 (агент)

- **H1.1** Rate limits (429), access-логи по H71, расширенный `/health`. Тесты на каждый
  механизм, включая «секрет не в логе».
- **H1.2** Зажать `agent-zero` в compose или задокументировать исключение с причиной.
- **H1.3** Бэкапы: расписание, RPO/RTO в `OPERATIONS.md`, drill со страховочным
  снапшотом, транскрипт.
- **H1.4** TLS ingress-ранбук + стейджинг с внешним `health/state`, транскрипт установки
  по докам.
- Приёмка: CI зелёный; curl-пробы 429/логи/метрики; restore-drill; URL стейджинга.

### Phase 2 — Data quality: честность и P1-данные (агент)

- **DQ-1 Unmapped → 0.** Перечислить все `unmapped_rules` на E2E-корпусе, добавить
  мэппинг в `rule_catalog.v2.json` через `_known_rule`, тест «пусто на корпусе».
  Реальные ключи вида `DUPLICATE_TITLE`/`MISSING_CANONICAL` — раскрыть исполнением,
  не памятью.
- **DQ-2 Warn + severity.** Глотать `warn` (observation/medium), `fail` маппить по
  градуированным порогам из вендорных правил; critical — только по порогу. Тест на
  распределение severity на фикстуре.
- **DQ-3 Честные счётчики.** Истинные page counts, per-source coverage, канонизация URL,
  снять расхождение baseline-vs-report и profile-noop. Тест на дедуп URL.
- **DQ-4 PSI v5 + CrUX lane.** Stdlib-адаптер, optional + capped (лимит страниц/запросов),
  lab + field CWV поверх 5-страничного сэмпла. Offline/без ключа → `unavailable` в
  `missing_data`, не тишина. Тест на cap и на деградацию.
- **DQ-5 Robots/sitemap/schema.** Stdlib-парсеры RFC 9309 и sitemaps, снапшот Schema.org
  в продукте; живой валидатор — внешним лейном. Тесты на парсеры.
- **DQ-6 Enrichment retry.** Два bounded-ретрая с таймаутом, затем детерминированный
  fallback. Метрика попыток в отчёт. Тест: флакающий провайдер → retry → fallback.
- **DQ-7 Перебазлайн.** Новый E2E-прогон 25 страниц, новый evidence-пак, запись
  «стало/было» по задачам и severity; золотые тесты обновить осознанно.
- Приёмка: unmapped=0; warn виден в отчёте; severity не «всё critical»; PSI-лейн
  capped и деградирует честно; evidence-пак Phase 2 сохранён.

### Phase 3 — P2-лейны по гейтам (агент + человек на аппрувах)

- **DQ-8 GSC Search Analytics** отдельным optional-сервисом (queries/CTR/позиции/
  индекс). Человек: OAuth-аппрувы и тестовый property. Без аппрувов — `unavailable`,
  релиз не блокирует (фиксируется в notes).
- **DQ-9 W3C Nu lane** для HTML поверх `htmlval`; fail → `unavailable`.
- **DQ-10 SecurityHeaders + SSL Labs** capped-лейн; доку SecurityHeaders перепроверить
  руками (исследователь упёрся в 403 WAF).
- **DQ-11 DataForSEO v3** — только при решении по бюджету/ключу (open question);
  иначе дежурный `unavailable` + пункт в notes.
- **DQ-12 Сэмплирование + Common Crawl excerpts-only**, цитаты вместо полного краула.
- Приёмка каждого лейна: optional + capped + non-blocking доказаны тестом и live-пробой;
  чек-лист «входит / отложен» подписан до гейта.

### Phase 4 — Гейт 2.1.0 (агент + человек на вердикте)

- **H2.1 Бамп:** `server.py`, `MANIFEST.yaml`, `listing.json`, теги образов,
  `build_release.py`, `release-manifest.json`, `automation_passport.yaml`, оба README;
  инвариант сходимости в `test_packaging.py`.
- **H2.2 Цепочка:** сюиты (≥184 + новые DQ-тесты) → детерминированная сборка со сверкой
  → `selfcheck → READY` → точный архив (тесты + live probe + backup-check) → чистая
  комната → upstream 12/12.
- **H2.3 Release notes:** что вошло, границы (BYOK-статус каждого лейна, enrichment
  может быть частичным, одна реплика/FIFO), стало/было по verdicts. Без
  неподтверждённых цифр.
- Приёмка: один зелёный проход; evidence-пак гейта; вердикт подтверждает человек.

### Phase 5 — Магазин и коммерция (стык + человек ведёт)

- `tools/publish_store.py` → предрелиз → self-purchase (после снятия `FORBIDDEN_TIER`)
  → чистая комната → Publish человеком → смена вердикта.
- Коммерция для идеального релиза обязательна: права/IP, проценты, rails/KYC, кто
  платит LLM, замер run cost, deck без дыр, LOI-пак.
- Приёмка: листинг видим, SHA-256 побайтово, скоупы чистые, условия подписаны.

## Validation Plan

- **P0:** `git status` пуст; ротация 401/200; grep секретов пуст; сюиты OK.
- **P1:** CI зелёный; 200→429 переход; секрет не в логе; health <1с с версией и
  зависимостями; стейджинг извне; транскрипт restore.
- **P2-DQ:** unmapped=0 на корпусе; warn в отчёте; severity-распределение на фикстуре;
  дедуп URL; PSI cap + честный `unavailable`; парсеры RFC/sitemap на тестах; retry
  enrichment; новый E2E-пак со стало/было.
- **P3-лейны:** каждый — тест optional/capped/non-blocking + live-проба + чек-лист
  входит/отложен; GSC — реальный property или честный `unavailable`.
- **P4-гейт:** сюиты, сверка хешей, `READY`, архив CT160, чистая комната, 12/12.
- **P5:** purchase-check, SHA-256, Publish, подписанная коммерция.
- Самый рискованный шаг: **DQ-1…DQ-3 меняют verdicts** — любой пропуск в перебазлайне
  (DQ-7) делает гейт лживым; поэтому DQ-7 — строгий гейт перед Phase 4, а версия в
  сторе создаётся только после него (H8).

## Risks / Rollback

- H8: провал версии → следующий номер; публикация обратима (`published: false`).
- Новые лейны — только optional/capped/non-blocking: мёртвый провайдер не роняет аудит.
- Платные API (PSI quota, DataForSEO): cap + алерты трат; превышение → `unavailable`.
- Restore нетранзакционный: drill со страховкой.
- Человеческий трек (токен, OAuth, бюджет, Publish, деньги) вне критического пути фаз
  0–2, блокирует 3–5.

## Open Questions

1. DataForSEO: бюджет/ключ — входит в идеальный релиз или дежурный `unavailable`?
2. `FORBIDDEN_TIER` self-purchase — фаундерам; блокирует Phase 5.
3. Стейджинг-хост: дефолт дешёвый VPS; есть своя машина/домен — сказать.
4. OAuth-аппрувы GSC: кто и на каком property; блокирует только DQ-8.
5. Точное место `device_id`/`agent_id` — закрывается при H1.4.
