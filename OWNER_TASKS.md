# Задачи владельца выпуска 2.0

## Подтверждено кодом

- Runtime принимает несколько Целей, строит пять детерминированных профилей и ограничивает один Запуск значением `1..100` страниц.
- SEOmator catalog содержит 251 известное правило в 20 категориях; одиночный факт имеет `supported`, а `verified` требует точного corroboration set.
- Deadline равен `180/120` секундам для одной страницы и `900/720` для multi-page. Agent Zero использует только `seo_employee_no_tools`.
- Сервис изолирует состояние, Снимок, историю, Базовую линию, daily index и lock по `target_id`; очередь хранится атомарно и восстанавливается явно один раз при старте.
- Панель показывает список Целей, очередь, coverage, `empty/running/ready/partial/failed` и до десяти карточек.
- Bridge ожидает `etb_init.device`, прекращает вызов с видимой ошибкой без устройства и передаёт singular `target` в каждом `etb_run_expert`.
- Installer проверяет manifest, пишет только каноническую привязку агента и сохраняет заменённые файлы в локальном backup перед откатом.

## Требования финальной root-проверки

- Проверить чистый архив и manifest, затем выполнить документированные узкие Python, Node и contract checks.
- В Docker staging подтвердить `docker compose -f deploy/compose.yaml config -q`, loopback bindings, отдельные сети, no-tools profile и отсутствие Docker socket.
- Подтвердить на живом Extella bridge устройство из `etb_init.device` и singular `target`; затем запустить v2 `check_app_scopes.py` для минимальных прав `expert.run` + `device.run`.
- Выполнить preflight, configure, queue, manual/daily и polling сценарии; проверить пять состояний, `not_configured`, exact `next_run`, restart persistence и queue recovery.
- Подтвердить пределы 1/25/100, двухпроходный SEOmator sample, 251-rule mapping и per-target isolation. Любой provider/model/auth требует отдельного живого вызова.

## Внешние блокеры

- Extella authentication и platform binding.
- Реальные pilot data.
- Investor numbers.
