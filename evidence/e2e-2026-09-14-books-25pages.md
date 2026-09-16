# E2E: full_audit на 25 страницах — 2026-09-14

Сайт: `https://books.toscrape.com/` (публичный тестовый стенд, владение подтверждено флагом).
Сырьё: [e2e-2026-09-14-books-25pages.json](e2e-2026-09-14-books-25pages.json) — конфиг, очередь, `last_report` (секретов нет, `bound_to` вырезан).

## Сценарий

1. `POST /api/configure` — `service_b2b`, `full_audit`, `max_pages: 25`, `ownership_confirmed: true`
2. `POST /api/run` — queue `seo-a49ad020c`, worker `seo-122cfc9fc`
3. `GET /api/state?target_id=...` до `completed` / top `ready`

Время: запрошен 10:32:05Z, завершён 10:41:48Z (~9м43с). `last_error: null`.

## Результат

- Просканировано страниц: **25/25**, источники: CrawlSEO + SEOmator, категории: 20/20
- Задач: **10 critical**, evidence `verified`/`supported`, `missing_data: []`
- Правила: meta-description-missing ×3, content-duplicate-description ×2,
  core-canonical-present ×2, js-rendered-canonical ×2, security-mixed-content ×1
- Model enrichment: **6/10, status `unavailable`** — детерминированные evidence сохранены,
  ограничение зафиксировано в отчёте (на прогоне 5 страниц было 8/10 — обогащение нестабильно)
- Опциональные источники GSC / DataForSEO не подключены (BYOK, вне скоупа)

## Как повторить

```sh
SEO_TOKEN=$(docker compose --project-name extella-seo-employee -f deploy/compose.yaml exec -T seo-employee cat /run/secrets/seo_employee_api_token | tr -d '\r\n ')
curl -s -H "Authorization: Bearer $SEO_TOKEN" -H 'Content-Type: application/json' \
  -d '{"target_name":"Toscrape Books","site_url":"https://books.toscrape.com","profile":"service_b2b","max_pages":25,"mode":"full_audit","ownership_confirmed":true,"daily_run_time":"03:00","timezone":"UTC"}' \
  http://127.0.0.1:8088/api/configure
curl -s -H "Authorization: Bearer $SEO_TOKEN" -H 'Content-Type: application/json' \
  -d '{"target_id":"target-books-toscrape-com-d142438e","mode":"full_audit"}' \
  http://127.0.0.1:8088/api/run
# опрос до completed:
curl -s -H "Authorization: Bearer $SEO_TOKEN" \
  'http://127.0.0.1:8088/api/state?target_id=target-books-toscrape-com-d142438e'
```

Важно: у `/api/state` в query допустим только `target_id` — лишний параметр даёт 404 от gateway.
Прогресс смотреть по статусу элемента очереди (`queue.items[].status`), а не по `last_run`
(там только `at`/`kind`).
