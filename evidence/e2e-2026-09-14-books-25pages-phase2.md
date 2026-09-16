# E2E Phase 2 (DQ-7): перебазлайн full_audit на 25 страницах — 2026-09-14

Сайт: `https://books.toscrape.com/` (публичный тестовый стенд, владение подтверждено флагом).
Сырьё: [e2e-2026-09-14-books-25pages-phase2.json](e2e-2026-09-14-books-25pages-phase2.json) —
конфиг, очередь, `last_report` (секретов нет, `bound_to` вырезан).
Предыдущий базлайн: [e2e-2026-09-14-books-25pages.json](e2e-2026-09-14-books-25pages.json)
(run `seo-122cfc9fc`, normalizer 2.0.0).

## Сценарий

1. `POST /api/configure` — `service_b2b`, `full_audit`, `max_pages: 25`, `ownership_confirmed: true`
2. `POST /api/run` — queue `seo-18a8e0fd9`, worker run `seo-f390612b`
3. `GET /api/state?target_id=...` до `completed` / top `ready`

Время: запрошен 12:05:08Z, завершён 12:15:19Z (~10м11с). `last_error: null`,
normalizer 2.1.0, schema `extella.seo_employee_report.v2`.

## Стало / было

| Метрика | Было (run `seo-122cfc9fc`) | Стало (queue `seo-18a8e0fd9`) |
|---|---|---|
| Задачи | 10 critical: meta-desc ×3, dup-desc ×2, canonical ×2, js-canonical ×2, mixed-content ×1 | 10 critical: core-canonical-present ×9, meta-description-missing ×1 |
| Evidence level | 8 supported + 2 verified (в основном один SEOmator) | 10 verified (CrawlSEO + SEOmator попарно) |
| Источники | CrawlSEO + SEOmator, без покомпонентной карты | CrawlSEO 25/25 + SEOmator 25/25 + PSI (capped, 1 страница) |
| `unmapped_rules` | не tracked покомпонентно (CrawlSEO `MISSING_CANONICAL` дропался молча) | `[]` у всех трёх источников |
| Model enrichment | 6/10 `unavailable`, без счётчика попыток | 8/10 `unavailable`, `attempts: 16` (2 bounded retry) |
| `missing_data` | `[]` | `[]` |
| Сравнение | 0/0/0 `not_compared` | 0/0/0 `not_compared` — сброс базлайна (catalog-major), принято как новый базлайн |

## Почему verdicts изменились

- **DQ-1:** CrawlSEO-правила (`MISSING_CANONICAL`, `MISSING_DESCRIPTION`) замаплены в каталог
  2.1.0 (254 правила) вместо молчаливого дропа → 9 URL получили парные evidence
  CrawlSEO+SEOmator и перешли из `supported` в `verified`.
- **Приоритизация:** топ-10 сортируется severity → evidence_level → … (`prioritize_findings`,
  `limit=10`, поведение до Phase 2 не менялось). Все 10 мест заняли `verified`; одиночные
  SEOmator-`supported` (dup-desc, js-canonical, mixed-content) ушли за пределы капа и в
  `tasks` не попали. В mapping ничего не потеряно: `unmapped=[]` у всех источников.
- **DQ-6:** два bounded retry подняли enrichment 6/10 → 8/10; провал — детерминированный
  fallback, ограничение зафиксировано в отчёте.

## Честные оговорки (что пак НЕ показывает)

- **Severity:** в живом корпусе все 10 задач — `critical`, потому что корпус дал только
  fail-статусы по правилам каталога с severity `critical` (порогово корректно, не дефолт).
  Градация доказана фикстурными тестами: `test_warn_only_finding_is_downgraded_one_step`,
  `test_mixed_fail_and_warn_keeps_catalog_severity`,
  `test_psi_thresholds_emit_graded_occurrences`.
- **Warn:** живой корпус warn-статусов не дал; ingest warn доказан тестами
  (`test_seomator_warn_is_ingested_with_warn_status`, downgrade-тесты выше), код:
  `experts/seo_employee_sources.py:469`, `experts/seo_employee_service.py:1171-1172`.
- **PSI:** keyless-квота ответила HTTP 429 → lab-метрики `unavailable` с честными notes
  (`psi api http_429`, `crux not_configured`), аудит не упал. Cap и деградация покрыты
  `tests/test_psi_worker.py` (46/46 Node + Python PSI-тесты зелёные).
- **Robots/sitemap/schema (DQ-5):** живые находки в топ-10 не вошли; парсеры покрыты
  `test_psi_robots_parser_flags_orphan_rule_and_bad_line`,
  `test_psi_sitemap_parser_flags_malformed_and_doctype`,
  `test_psi_schema_parser_flags_broken_jsonld_only`.

## Приёмка DQ-7

- [x] Новый E2E-прогон 25/25 страниц, источники CrawlSEO + PSI + SEOmator
- [x] `unmapped_rules` пуст на корпусе (все источники)
- [x] Счётчики честные: crawled 25 / planned 25 / sampled 5, per-source coverage в отчёте
- [x] Запись «стало/было» — эта страница; сравнение 0/0/0 принято как сброс базлайна
- [x] Сьюиты: Python 233/233 OK, Node 46/46 pass (прогон 2026-09-14, эта сессия)
- [x] Золотые тесты обновлены осознанно в этом же коммите (см. diff `tests/`)

## Как повторить

```sh
SEO_TOKEN=$(docker compose --project-name extella-seo-employee -f deploy/compose.yaml exec -T seo-employee cat /run/secrets/seo_employee_api_token | tr -d '\r\n ')
curl -s -H "Authorization: Bearer $SEO_TOKEN" -H 'Content-Type: application/json' \
  -d '{"target_name":"Toscrape Books","site_url":"https://books.toscrape.com","profile":"service_b2b","max_pages":25,"mode":"full_audit","ownership_confirmed":true}' \
  http://127.0.0.1:8088/api/configure
curl -s -H "Authorization: Bearer $SEO_TOKEN" -H 'Content-Type: application/json' \
  -d '{"trigger":"manual"}' http://127.0.0.1:8088/api/run
# GET /api/state?target_id=target-books-toscrape-com-d142438e до status completed
```
