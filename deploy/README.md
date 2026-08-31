# Docker deployment

Требования: Linux `amd64`, Docker Engine с Compose v2, Python 3.11+ и Git.

## Новый хост

```sh
cp deploy/.env.example deploy/.env
python3 deploy/prepare.py \
  --device-id '<Extella device id>' \
  --hosting-profile client_server \
  --agent-id '<agent_... from Extella>'
docker compose --project-name extella-seo-release -f deploy/compose.yaml up -d
```

`prepare.py` собирает три закреплённых образа, создаёт локальные secret-файлы с правами `600`, запускает Agent Zero и синхронизирует его внутренний API-токен без вывода значения. Затем владелец открывает `http://127.0.0.1:50081`, вручную подключает свой провайдер и выбирает модель. Код SEO Employee не ограничивает модель; живым E2E подтверждён только `agy/gemini-3.7-flash-high`, работа через пользовательскую подписку, BYOK и другие модели пока не подтверждена.

## Существующий Agent Zero

```sh
python3 deploy/prepare.py \
  --device-id '<Extella device id>' \
  --hosting-profile client_server \
  --agent-id '<agent_... from Extella>' \
  --external-agent-zero-key /secure/path/agent_zero_api_key \
  --external-agent-zero-container existing-agent-zero
docker compose --project-name extella-seo-release -f deploy/compose.yaml up -d
```

Путь к ключу передаётся локально; значение не печатается и не попадает в образ. Скрипт проверяет закреплённый образ и подключает уже работающий Docker-контейнер к внутренней сети под алиасом `agent-zero`, без перезапуска. Agent Zero в этом режиме Compose не создаёт.

## Доступ и перенос

- API продукта: `http://127.0.0.1:8088`; токен находится в `deploy/secrets/seo_employee_api_token`.
- Панель Agent Zero: `http://127.0.0.1:50081`.
- Все порты привязаны к loopback. Для внешнего доступа хостинг должен отдельно настроить TLS reverse proxy и собственную аутентификацию.
- Данные хранятся в именованных Docker volumes. Для проверяемого снимка и восстановления используйте [`backup.py`](backup.py) по инструкции [`OPERATIONS.md`](OPERATIONS.md) с тем же `--project-name`, что и при запуске Compose. На CT160 это `extella-seo-release`; секреты, bindings и данные Agent Zero в снимок намеренно не входят.
- Публичный сайт только читается. Контейнер продукта не имеет прямого интернет-маршрута; CrawlSEO, SEOmator, DNS-only resolver и Agent Zero вынесены в отдельные сети.

Проверка состояния:

```sh
docker compose --project-name extella-seo-release -f deploy/compose.yaml ps
python3 deploy/probe.py health
python3 deploy/probe.py state
```

Для обычной проверки API используйте `GET /health` без токена и `GET /api/state` с `Authorization: Bearer <локальный токен>`.
