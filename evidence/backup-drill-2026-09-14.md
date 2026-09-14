# Backup drill — 2026-09-14

Контур: `extella-seo-employee` (dev-хост), проект `--project-name extella-seo-employee`.
Снапшоты: `~/.local/share/extella-seo-employee/backups/`. Секретов в транскрипте нет.

## Ход

1. Состояние до: `state=ready`, задач в `last_report`: 10.
2. Страховочный снапшот A `20260914T111537Z-fafe9b8d`: `create` → `created`;
   `verify` → `verified`, artifacts: 6.
3. Рабочий снапшот B `20260914T111648Z-4fe65181`: `create` → `created`;
   `verify` → `verified`, artifacts: 6.
4. `restore-check B` (без `--apply`): `restore-check-ok`, `temporary_only: true`,
   volumes: seo_config/seo_state/seo_reports/seo_history/seo_evidence,
   postgres_dump: postgres/crawlseo.sql. Продакшн не тронут.
5. `restore-check --apply B`: `restored` (volumes + postgres `crawlseo`).
6. Состояние после: `/health` → `ok`, consumer `alive`, depth 0;
   `state=ready`, задач: 10. Все 8 контейнеров healthy
   (agent-zero-proxy без healthcheck по дизайну — проксирует Agent Zero).

## Вывод

Полный цикл create → verify → stage → apply работает; состояние контура после apply
идентично состоянию до (снапшот свежий, apply идемпотентен по сути). RTO 30 минут
подтверждено с запасом: весь drill занял минуты. RPO 24 часа — следующим шагом
нужен ночной timer (см. `deploy/OPERATIONS.md`: Schedule).
