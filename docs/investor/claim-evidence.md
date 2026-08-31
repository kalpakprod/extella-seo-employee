# Claims and evidence: Extella Universal SEO Employee 2.0

Статус `code-verified` означает, что утверждение закреплено в коде, контракте или тесте репозитория. `live-verified` означает результат живой проверки CT160, переданный владельцем выпуска. Он не является доказательством рынка или клиентского результата. `unverified` запрещает использовать утверждение в investor- или sales-материалах как факт.

## Code-verified

| Утверждение | Доказательство | Граница формулировки |
|---|---|---|
| Продукт разворачивается self-hosted через Docker Compose. | [Compose](../../deploy/compose.yaml), [deployment README](../../deploy/README.md) | Это не доказывает готовность чужой инфраструктуры, SLA или публичный запуск. |
| Бесплатный технический core строится на CrawlSEO и SEOmator. | [v2 blueprint](../blueprints/universal-seo-employee-v2.md), [service contract](../contracts/service-seo-runner.md) | Не утверждает коммерческие условия optional источников или экономику запуска. |
| Поддерживаются пять детерминированных профилей: `service_b2b`, `ecommerce`, `local_business`, `content_media`, `saas_marketplace`. | [v2 blueprint](../blueprints/universal-seo-employee-v2.md), [profile tests](../../tests/test_seo_employee_profiles.py) | Не доказывает качество аудита для каждой отрасли. |
| AuditPlan ограничивает crawl budget диапазоном 1-100 страниц, default равен 25. | [v2 blueprint](../blueprints/universal-seo-employee-v2.md), [service v2 tests](../../tests/test_seo_employee_service_v2.py) | Не является обещанием полного аудита сайта. |
| Несколько Целей имеют изолированные target state, history, baseline и очередь; source-heavy запуски идут через FIFO. | [v2 blueprint](../blueprints/universal-seo-employee-v2.md), [queue tests](../../tests/test_seo_employee_queue.py), [target tests](../../tests/test_seo_employee_targets.py) | Не доказывает нагрузочную ёмкость или многопользовательский SaaS. |
| Запуск поддерживает `manual` и `daily` trigger. | [service contract](../contracts/service-seo-runner.md), [service v2 tests](../../tests/test_seo_employee_service_v2.py) | Не доказывает фактическую частоту использования. |
| GSC и DataForSEO являются optional lanes. Их отсутствие не блокирует бесплатный технический аудит. | [service contract](../contracts/service-seo-runner.md), [UI contract](../contracts/ui-seo-panel.md) | OAuth и credentials этих источников не подтверждены. |
| Agent Zero получает sanitized facts и работает по no-tools профилю. | [Agent Zero transport](../../experts/seo_employee_run.py), [transport tests](../../tests/test_agent_zero_transport.py) | Это не доказывает качество каждой модели или безопасность внешнего provider. |
| Сбой модели не уничтожает deterministic findings и evidence. | [v2 blueprint](../blueprints/universal-seo-employee-v2.md), [service v2 tests](../../tests/test_seo_employee_service_v2.py) | Это не гарантирует отсутствие всех runtime failures. |
| Продукт не исполняет рекомендации автоматически и не изменяет сайт. | [UI contract](../contracts/ui-seo-panel.md), [service contract](../contracts/service-seo-runner.md) | Человек может вручную применить рекомендацию вне продукта. |

## Live-verified: CT160

| Утверждение | Доказательство | Граница формулировки |
|---|---|---|
| На CT160 пройден preflight и живой маршрут `agy/gemini-3.7-flash-high`. | Результат живой проверки CT160, переданный владельцем выпуска; [transport tests](../../tests/test_agent_zero_transport.py) проверяют контракт транспорта. | Подтверждён только этот точный маршрут. |
| Обе source probes выполнили аудит 25/25 страниц на публичном scraping fixture; private target блокируется обоими workers. | [CT160 evidence](../../dist/ct160-verification-v2.json), [service contract](../contracts/service-seo-runner.md) | Это не полный пользовательский аудит и не доказательство качества на домене design partner. |
| Полный API E2E на публичном scraping fixture завершился `ready`: оба источника 25/25, 5 browser samples, Agent Zero 10/10 и 10 задач. | [CT160 evidence](../../dist/ct160-verification-v2.json) | Это технический fixture, не клиентский результат и не доказательство ROI. |
| Релевантные тесты прошли в проверенном техническом контуре. | Результат живой проверки CT160, переданный владельцем выпуска; [v2 service tests](../../tests/test_seo_employee_service_v2.py), [queue tests](../../tests/test_seo_employee_queue.py), [profile tests](../../tests/test_seo_employee_profiles.py). | Не заявляется количество тестов, покрытие всех сценариев или production SLO. |

## Unverified

| Утверждение, которое нельзя делать | Почему | Что нужно для подтверждения |
|---|---|---|
| Поддерживаются любые модели или provider routes. | Live-проверка существует только для `agy/gemini-3.7-flash-high`. | Отдельный preflight и живой запуск для каждого точного маршрута. |
| Работают consumer subscription или BYOK. | Эти способы доступа не проходили подтверждённый live path. | Отдельная безопасная проверка каждого способа доступа. |
| Работают OAuth-интеграции GSC и DataForSEO. | Они заявлены как optional, но OAuth не подтверждён. | Подтверждённый OAuth flow и source run. |
| Продукт прошёл полный запуск на домене, принадлежащем пользователю. | Подтверждены только одностраничные source probes. | Согласованный user-owned-domain run с правом на аудит. |
| Есть рынок, клиенты, выручка, TAM, цена, спрос, ROI или retention. | В распоряжении проекта нет подтверждённых рыночных или коммерческих данных. | Документированные интервью, пилотные данные и финансовые записи. |

## Пилотные гипотезы и измерения

Для 3-5 design partners из SEO-агентств и соло SEO фиксируются только наблюдаемые показатели: time-to-report, accepted recommendations, repeat usage, run cost и failures. Ни одно значение не задано заранее. До завершения пилота эти метрики не служат доказательством спроса, экономии, выручки или product-market fit.
