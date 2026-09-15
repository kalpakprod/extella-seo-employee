# P2: cancellable probe execution

Автор архитектуры: extella / Astra. Решение владельца от 2026-09-15: архитектуру и существенные решения создаёт оркестратор; Luna реализует эту спецификацию, Sol независимо проверяет. Это утверждённое направление hard cancellation, не принятие прежнего pilot limitation.

## Причина и граница

Sol воспроизвёл slow-drip и незавершённый outbound после смерти source_proxy. Socket idle timeout и проверки monotonic после read не прерывают DNS/read внутри удалённого HTTP worker. Требуемый инвариант: в штатной ОС supervisor прекращает выполнение конкретной проверки по её бюджету, даже если DNS или удалённый сервер не отвечает. Основной аудит получает unavailable; успешного ответа после дедлайна нет. Не обещаем real-time scheduling или способность Python преодолеть зависшее ядро.

## Выбранная схема

Существующий контейнер каждой lane остаётся. Его постоянный Python HTTP процесс становится supervisor. После проверки формы запроса он запускает отдельный процесс того же entrypoint через sys.executable и закрытый CLI режим --probe-child <kind>. Только этот дочерний процесс выполняет DNS, HTTP/TLS, обработку HTML/архива и вызовы провайдеров. Существующие run_nu_probe/run_tls_probe/run_cc_probe сохраняют свою семантику, но HTTP handle_run не вызывает их напрямую.

Используем subprocess.Popen с аргументами-списком, shell=False, start_new_session=True, close_fds=True. Не используем multiprocessing fork из threaded server: наследование lock/socket состояния здесь не нужно. Новый Python процесс не получает listening socket. Внутри child запрещены новые фоновые subprocess/потоки; текущие runners их не требуют. Stdlib достаточно, новые зависимости/sidecars/очереди/пулы процессов не нужны.

## Протокол и дедлайн

Parent проверяет JSON/form, kind, scheme/authority синтаксически без DNS; сохраняет текущие HTTP 400 для malformed request. Deadline D = monotonic() + timeout_ms/1000 устанавливается после принятия полного bounded body, до запуска child. Бюджет включает запуск Python, IPC, DNS, все remote calls и сериализацию результата. Деталь: сервис сейчас ограничивает probes 20 секундами; HTTP/schema диапазон не увеличиваем и product overall timeout не продлеваем.

Parent передаёт по stdin один bounded JSON request с kind/site_url/plan и абсолютным monotonic deadline (parent/child на одном хосте используют одну monotonic шкалу). Child повторно валидирует внутренний запрос, проверяет deadline ДО любого DNS и использует только остаток. URL и request body не передаём в argv. Отдельный внутренний envelope ответа: http_status + payload. Это внутренний IPC, наружный JSON/schema не меняется.

Parent использует selectors и nonblocking pipes для bounded stdin write и stdout read под одним D, а не неблокируемый communicate с неограниченным output. Выход ограничен 65536 bytes; превышение, неверный envelope, EOF без результата, ненулевой exit или spawn failure дают HTTP 200 unavailable с совместимым reason http_503. timeout даёт HTTP 200 unavailable/reason timeout. Child также ограничивает сериализованный output до записи. stderr направляется в DEVNULL, чтобы не получить второй pipe deadlock и не логировать пользовательский документ. Диагностика parent — только kind, исход, длительность, без URL/body/secrets.

Успех принимается лишь если получен полный валидный envelope, child завершился с кодом 0 и monotonic() < D. Результат, пришедший после D, не принимается. Блокирующая DNS/provider операция допустима внутри child: deadline обеспечивает родитель. Возобновление запроса/повторный запуск child после timeout запрещены.

## Остановка и ресурсы

В deadline, IPC overflow/error или исключении parent: убить только созданную им process group через os.killpg(child.pid, SIGKILL). SIGKILL выбран намеренно: read-only probes не имеют транзакций для graceful flush; TERM grace продлил бы outbound за бюджет. Не использовать kill по имени/порту. Все pipes закрываются в finally. Child обязательно reap через wait; на обычной ОС время cancellation равно deadline плюс наблюдаемая задержка планировщика/IPC/cleanup, которую меряем отдельно.

Cleanup wait ограничен 0.25 секунды. Если процесс не reap: хранить его Popen в supervisor registry, НЕ освобождать его probe slot и пометить health degraded. При следующих health/admission calls poll/reap registry; освобождать slot только после подтверждённого exit. Не запускать дополнительные cleanup threads и не продолжать неограниченный spawn. Невозможность немедленного kernel termination не выдавать как успешно завершённую проверку. Перезапуск Orchestra не требуется и запрещён.

На контейнер максимум 2 активных probe child; неблокирующий admission semaphore, без очереди. Это локальная верхняя граница расхода процессов/памяти для существующих pids_limit=32 и mem_limit=128m, а не число пользователей или требование внешнего API. Busy даёт HTTP 200 unavailable/http_503 и не запускает child. После timeout следующий запрос должен успешно получить освобождённый slot.

Существующий ThreadingHTTPServer ограничить 4 handler threads через server-level nonblocking admission до создания thread; лишнее соединение получает короткий 503 JSON и закрывается. Это предотвращает обход child cap бесконечными parent threads. Accepted connection получает bounded 2-second idle timeout для чтения HTTP headers/body и отправки ответа; body cap прежний. Публичного порта у worker нет; hard execution budget начинается после полного тела, а не TCP accept. Slow ingress здесь не объявлять hard-cancelled: thread cap ограничивает его воздействие. Health не запускает child и остаётся доступен при двух выполняющихся проверках при отсутствии отдельного ingress-flood.

Если caller disconnects, не обещаем мгновенной отмены: запрос живёт не дальше своего D и затем child уничтожается независимо от клиента. BrokenPipe при отправке ответа не нарушает cleanup. При завершении supervisor в штатном shutdown уничтожить зарегистрированные children. Для SIGKILL supervisor полагаемся на существующую границу контейнера: завершение PID1 прекращает контейнерные процессы; не обещаем переносимость этого свойства на произвольный daemon вне контейнера.

## Что сохраняется

Pinned public IP, mixed DNS rejection, SNI/certificate validation, same-origin redirect revalidation, IPv6 Host brackets, NAT64 embedded-private guard, byte caps, exact Common Crawl URL, bounded validated-IP fallback. Нет прямого runner fallback в HTTP supervisor при ошибке child: только unavailable. GSC/DataForSEO остаются not_configured. Required sources и итоговый report schema не меняются.

## Приёмка реализации и независимого ревью

1. Реальные отдельные worker и source_proxy: origin slow-drip продолжает слать меньше idle timeout, но child завершается по D; remote origin видит закрытие connection. Проверить site fetch, provider JSON, Nu POST и CC archive paths.
2. DNS hang детерминированным process-local test double только в тестовом child; supervisor и production CLI без test-only network bypass. После D child отсутствует/reaped, health отвечает, следующий запрос работает.
3. SIGKILL source_proxy до D: отдельный worker остаётся healthy, но его probe child не переживает D. Измерять child PID/process activity и origin socket; return timeout сам по себе не доказательство.
4. Два зависших запроса + третий: active child count не растёт сверх 2, третий получает unavailable; после deadline оба reap. No zombie/orphan, registry/slots восстанавливаются. Oversized stdout, child crash, malformed JSON, early/late exit тоже очищаются.
5. Реальный TLS/SNI/неверный сертификат, mixed/rebinding DNS, IPv6, NAT64 regressions; чистый collect_sources → HTTP workers → report с exact missing_data и required audit ready.
6. Python/Node suites, fresh isolated Docker live probes; сохранить raw timestamps, requested deadline, elapsed, observed termination/cleanup отдельно. Использовать scheduler tolerance в тестах и объяснить её; не объявлять математически точную остановку в миллисекунду.

Luna реализует детали функций/тестов в этих границах. Изменение IPC, process model, budget boundary, concurrency, cancellation или публичного контракта возвращается Astra до реализации. Sol проверяет final exact HEAD; архитектурный PASS не выводится из одного зелёного unit test.

## Бюджет между продуктом и worker

Уточнение архитектора после критериев Sol: внешний subprocess timeout B остаётся прежним (не увеличиваем overall audit budget). Для probes сервис резервирует 1 секунду внутри B на IPC/cleanup/HTTP: wire timeout_ms = floor((B - 1) * 1000). При B <= 1 секунды worker не запускается, сразу unavailable/timeout. При обычном B=20 worker получает 19 секунд. Parent worker cleanup максимум 0.25 секунды; оставшийся запас предназначен для ответа и сохранения source_proxy результата. Это уменьшает race, но не обещает доставку при произвольно задержанной ОС/сети; внешний timeout всё равно остаётся страховкой. Для прямых worker запросов deadline означает время child execution с bounded cleanup отдельно. Тестовый grace <=250ms из предложения Sol — критерий локального измерения после D, не гарантия real-time scheduling.
