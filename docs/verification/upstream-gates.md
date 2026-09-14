# Upstream gates: прогон и сверка паспортов

Дата: 2026-09-14. Апстрим: `AnvarBakiyev/extella-agent-standards`, ревизия
`d6118bd55310261ae9aeb1aa0ed4df58e4d57cb6` (main, shallow clone в /tmp, в репо не вендорится).

## Как повторить

```sh
git clone --depth 1 https://github.com/AnvarBakiyev/extella-agent-standards.git /tmp/extella-agent-standards
cd /tmp/extella-agent-standards
P=/home/kukuruza/orca/extella-seo-employee
python3 tools/check_code_canon.py $P
python3 tools/check_ui_api_contract.py $P
python3 tools/check_self_check.py $P
python3 tools/check_app_scopes.py $P
python3 tools/check_listing_meta.py $P
python3 tools/check_ready_for_publish.py $P
python3 tools/check_canon_numbers.py $P
python3 tools/check_single_source.py $P
python3 tools/check_automation_passport.py $P/automation_passport.yaml
python3 tools/check_brand_copy.py $P/app/index.html $P/app/app.js $P/app/styles.css $P/app/extella-bridge.js
python3 tools/check_waiting_state.py $P/app/index.html $P/app/app.js
python3 tools/check_state_contract.py $P/evidence/extella.seo_employee_state.v1.json
```

Важно: части гейтов нужен каталог продукта, части — конкретные файлы
(см. `tools/GATES.md` и `--selftest` каждого гейта). Каталог вместо файла даёт
`IsADirectoryError`, а не verdict.

## Результат: 12/12 зелёных

| Гейт | Код | Итог |
|---|---|---|
| check_code_canon | 0 | канон соблюдён (файлов 36) |
| check_ui_api_contract | 0 | ✓ etb_run_expert → etb_expert_result, allowlist, timeout, parent guard, target |
| check_self_check | 0 | самопроверка есть и честна |
| check_app_scopes | 0 | права сходятся: device.run + expert.run |
| check_listing_meta | 0 | карточка готова к витрине |
| check_ready_for_publish | 0 | находок 0 |
| check_canon_numbers | 0 | номера уникальны (следующий H95) |
| check_single_source | 0 | источник один (документов 35, шаблонов 2) |
| check_automation_passport | 0 | ГОТОВА К ВЫПУСКУ, одно внимание: `provider_expected` не указан |
| check_brand_copy | 0 | бренд соблюдён (файлов 4) |
| check_waiting_state | 0 | ожидание видно везде |
| check_state_contract | 0 | контракт соблюдён |

## Выводы

1. Оба ранее падавших гейта (`check_ui_api_contract`, `check_app_scopes` — см.
   `docs/compliance/extella-standards-and-bridges.md`) на HEAD апстрима зелёные:
   `etb_run_expert` есть в обоих гейтах (5 и 2 вхождения). Локальный патч
   `patches/extella-agent-standards-etb-gates.patch` против HEAD избыточен;
   удаление — отдельным решением (патч исключён из релизного payload в
   `tools/build_release.py` и на сборку не влияет).
2. Паспорта: апстрим хранит только EXAMPLE **агентного** паспорта
   (`passports/EXAMPLE_agent.yaml`, схема agent/capabilities) и черновики в
   `passports/_drafts/`; EXAMPLE паспорта **автоматизации** нет. Наш
   `automation_passport.yaml` — другого типа (automation/components), гейт его
   принимает. Единственное замечание — неуказанный `provider_expected`.
3. Скилл `extella-ui` (`skills/extella-ui/SKILL.md`, 22 КБ) — официальный
   скилл интерфейса; ставится на машину один раз (см. `УСТАНОВКА.md` в апстриме),
   дизайн-код в нём сверяется гейтами. Рекомендуется к установке перед любыми
   правками `app/`.
