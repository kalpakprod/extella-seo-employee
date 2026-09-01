// ожидание: фоновая задача; состояние страницы обновляется одной ограниченной цепочкой.
(() => {
  const PANEL_VERSION = '2.0.3';
  const EXPERT_RUN = 'seo_employee_run';
  const EXPERT_STATE = 'seo_employee_state';
  const STATE_VIEWS = ['empty', 'running', 'failed', 'result'];
  const GROUPS = ['new', 'fixed', 'unchanged'];
  const PROFILES = ['service_b2b', 'ecommerce', 'local_business', 'content_media', 'saas_marketplace'];
  const MODES = ['full_audit', 'daily_monitor', 'search_performance', 'work_plan'];
  const LANGUAGES = ['ru', 'en'];
  const SITE_TYPES = ['website', 'store', 'service', 'publication'];
  const ISO_3166_ALPHA2 = Object.freeze(
    'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ '
    + 'CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR '
    + 'GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO '
    + 'JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS '
    + 'MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW '
    + 'SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM '
    + 'US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'
  ).split(' ');
  const ALLOWED_REGIONS = new Set(['GLOBAL', ...ISO_3166_ALPHA2]);
  const EVIDENCE_TEST_IDS = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel', 'india', 'juliet'];
  const POLL_INTERVAL_MS = 2000;
  const POLL_MAX_MS = 900000;
  const el = id => document.getElementById(id);
  const own = (value, key) => Boolean(value)
    && typeof value === 'object'
    && Object.prototype.hasOwnProperty.call(value, key);
  const isObject = value => Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value);
  const asText = (value, fallback = '') => value === undefined || value === null ? fallback : String(value);

  const COPY = {
    ru: {
      pageTitle: 'SEO-сотрудник Extella',
      productLabel: 'Extella · SEO-сотрудник · Closed Pilot',
      title: 'Проверяй сайты и получай задачи с доказательствами',
      lead: 'Выбери цель, сохрани настройки и начни с первой проверки сайта.',
      capabilitiesLabel: 'Включено',
      capabilityAudit: 'Технический аудит',
      capabilityCoverage: 'Покрытие и очередь',
      capabilityPlan: 'Рабочий план',
      targetsLabel: 'Цели',
      targetsTitle: 'Выбери цель',
      newTarget: 'Добавить цель',
      siteUrlLocked: 'URL сохранённой цели нельзя изменить. Создай новую цель для другого URL.',
      noTargets: 'Целей пока нет. Создай первую цель, чтобы запустить проверку.',
      targetFormLabel: 'Настройки цели',
      targetFormTitle: 'Сайт и расписание',
      targetFormHint: 'Сохрани цель, затем подтверди право на аудит и запусти проверку.',
      targetNameLabel: 'Название цели',
      siteUrlLabel: 'Публичный URL сайта',
      siteUrlPlaceholder: 'https://example.com',
      profileLabel: 'Профиль',
      profileService: 'Сервис B2B',
      profileEcommerce: 'Интернет-магазин',
      profileLocal: 'Локальный бизнес',
      profileContent: 'Контент и медиа',
      profileSaas: 'SaaS и маркетплейс',
      languageLabel: 'Язык',
      languageRu: 'Русский',
      languageEn: 'English',
      regionLabel: 'Регион',
      siteTypeLabel: 'Тип сайта',
      siteTypeWebsite: 'Сайт',
      siteTypeStore: 'Магазин',
      siteTypeService: 'Сервис',
      siteTypePublication: 'Публикация',
      businessGoalLabel: 'Бизнес-цель',
      dailyTimeLabel: 'Время суточного запуска',
      timezoneLabel: 'Часовой пояс IANA',
      timezonePlaceholder: 'Europe/Berlin',
      modeLabel: 'Режим',
      modeFull: 'Полный аудит',
      modeDaily: 'Суточный мониторинг',
      modePerformance: 'Поисковая аналитика',
      modeWorkPlan: 'Рабочий план',
      maxPagesLabel: 'Максимум страниц',
      ownershipLabel: 'У меня есть право владения сайтом или полномочие на аудит',
      saveTarget: 'Сохранить цель',
      savingTarget: 'Сохраняю цель…',
      selectedTargetLabel: 'Выбранная цель',
      noSelectedTarget: 'Выбери цель слева',
      selectTargetHint: 'После сохранения здесь появятся состояние, покрытие и задачи.',
      runLabel: 'Запуск',
      runTitle: 'Проверь выбранный сайт',
      optionalSourcesTitle: 'Необязательные источники',
      optionalSourcesStatus: 'Без подключения',
      gscStatus: 'Не настроена, необязательно',
      dataForSeoStatus: 'Не настроен, необязательно',
      optionalSourcesHint: 'Для подключения открой настройки источника в среде Extella. Бесплатный технический аудит работает без этих подключений.',
      panelLabel: 'Панель SEO-сотрудника',
      panelVersionLabel: 'Версия панели',
      targetListLabel: 'Список целей',
      findingComparisonLabel: 'Сравнение находок',
      severityCritical: 'критично',
      severityError: 'ошибка',
      severityWarning: 'предупреждение',
      severityInfo: 'информация',
      runAction: 'Проверить сайт',
      runningAction: 'Проверяю…',
      readyStatus: 'Готов к проверке.',
      saveTargetFirst: 'Сначала сохрани цель.',
      ownershipRequired: 'Подтверди право на аудит и сохрани цель.',
      targetReady: 'Цель готова к проверке.',
      checkingStatus: 'Проверка началась. Собираю независимые источники.',
      loadingState: 'Получаю последнее состояние…',
      runningStatus: 'Проверка уже выполняется. Обновляю состояние…',
      queuedStatus: 'Цель в очереди. Показываю её позицию и причину ожидания.',
      duplicateStatus: 'Такая проверка уже выполняется или завершена. Обновляю состояние…',
      emptyTitle: 'Проверок ещё нет',
      emptyText: 'Сохрани цель и нажми «Проверить сайт». Здесь появятся задачи и доказательства.',
      runningTitle: 'Проверяю публичные страницы',
      runningText: 'Собираю независимые источники и формирую задачи.',
      queuedTitle: 'Проверка ждёт своей очереди',
      queuedText: 'Запуск сохранён. Очередь покажет позицию и причину ожидания.',
      failedTitle: 'Проверку не удалось завершить',
      failedText: 'Проверь публичный адрес и подключение, затем запусти проверку ещё раз.',
      partialTitle: 'Результат неполный',
      runTime: 'Время проверки',
      resultMode: 'Режим',
      sourceStates: 'Источники',
      newFindings: 'Новые',
      fixedFindings: 'Исправленные',
      unchangedFindings: 'Без изменений',
      emptyGroup: 'В этой группе находок нет.',
      countOnly: 'Количество известно, подробных карточек нет.',
      stateEmpty: 'Проверок нет',
      stateReady: 'Готово',
      statePartial: 'Частично',
      stateRunning: 'Идёт проверка',
      stateQueued: 'В очереди',
      stateFailed: 'Нужна проверка',
      sourceOk: 'готов',
      sourceNotConfigured: 'не настроен',
      sourceUnavailable: 'недоступен',
      sourceFailed: 'ошибка',
      sourceUnsupported: 'не поддерживается',
      sourceUnknown: 'Статус не указан',
      sourceInstruction: 'Проверь настройки источника и запусти проверку ещё раз.',
      severity: 'Серьёзность',
      target: 'Цель',
      url: 'URL',
      fact: 'Факт',
      sources: 'Источники',
      businessImpact: 'Деловое последствие',
      minimalFix: 'Минимальное исправление',
      verification: 'Как проверить',
      evidence: 'Доказательства',
      noEvidence: 'Отдельные доказательства не переданы.',
      unnamedSource: 'Источник не указан',
      modelLimitation: 'Модель не обогатила задачи. Ниже сохранены только проверенные факты, доказательства и способы проверки.',
      modelUnavailable: 'Модель недоступна. Детерминированные факты и доказательства сохранены.',
      modelNotNeeded: 'Модель для этого результата не требовалась.',
      missingSources: 'Ограничение покрытия: ',
      coverageLabel: 'Покрытие',
      coverageTitle: 'Что проверено',
      coverageUnknown: 'Ещё нет данных',
      coverageReady: 'Покрытие сохранено',
      coveragePartial: 'Покрытие неполное',
      plannedPages: 'Запланировано страниц',
      crawledPages: 'Проверено страниц',
      sampledPages: 'Страниц в выборке',
      categoriesTitle: 'Категории',
      completedSourcesTitle: 'Выполнены',
      unavailableSourcesTitle: 'Недоступны',
      unmappedTitle: 'Непокрытые правила',
      none: 'Нет данных',
      queueLabel: 'Очередь',
      queueTitle: 'Текущий и следующие запуски',
      queueNoPosition: 'Позиция не назначена',
      queuePosition: 'Позиция в очереди: ',
      queueCurrent: 'Текущий запуск',
      queueIdle: 'Нет активного запуска',
      queueReason: 'Причина ожидания',
      queueNoReason: 'Ожидание не требуется',
      queueEmpty: 'Ожидающих целей нет.',
      queueWaiting: 'Ожидание другого запуска',
      scheduleTime: 'Время',
      nextRun: 'Следующий суточный запуск',
      notScheduled: 'Ещё не рассчитан',
      proposalsLabel: 'Предложения действий',
      proposalsTitle: 'Только ручное выполнение',
      proposalStatus: 'Нужно подтверждение',
      proposalsEmpty: 'После обогащения модели здесь появятся отдельные предложения с доказательствами.',
      proposalTarget: 'Цель',
      proposalChange: 'Изменение',
      proposalOperation: 'Операция',
      proposalEvidence: 'Доказательство',
      proposalPreview: 'Предпросмотр',
      proposalRollback: 'Откат',
      proposalExpiry: 'Срок действия',
      proposalConfirmation: 'Подтверждение',
      proposalInstruction: 'Ручная инструкция',
      copyInstruction: 'Скопировать инструкцию',
      copiedInstruction: 'Инструкция скопирована.',
      copyUnavailable: 'Скопируй инструкцию вручную из карточки.',
      proposed: 'Предложено',
      confirmed: 'Подтверждено',
      notConfirmed: 'Не подтверждено',
      helpTitle: '? Как это работает',
      helpSources: 'CrawlSEO и SEOmator независимо проверяют публичные страницы. Google Search Console и DataForSEO остаются необязательными источниками.',
      helpCoverage: 'Покрытие показывает план, фактически проверенные страницы и непокрытые правила. Неполный результат не называется полным.',
      helpLimits: 'SEO-сотрудник не гарантирует позиции, трафик или выручку; он также не гарантирует лиды. Без пригодного источника поисковой аналитики он не определяет потери трафика.',
      helpRollback: 'Владелец сайта решает, принимать ли задачу и как откатить изменение. Панель сама сайт не меняет.',
      dataTitle: 'Какие данные сохраняются',
      dataCategories: 'Категории: настройки целей, адреса страниц, факты, источники, доказательства и статусы очереди.',
      dataLocation: 'Выполнение и хранение: в среде Extella владельца сайта.',
      dataRecipient: 'Получатель входа модели: выбранный владельцем провайдер модели получает только разрешённый минимум по задаче.',
      dataRetention: 'Срок: до удаления сохранённой цели или отчёта владельцем.',
      dataRemoval: 'Удаление: владелец удаляет цель и связанные локальные данные через среду Extella.',
      dataConsent: 'Google Search Console и DataForSEO не блокируют бесплатный технический аудит и подключаются только после отдельного явного согласия.',
      invalidName: 'Укажи название цели.',
      invalidUrl: 'Укажи публичный URL с http или https, без локального или приватного адреса.',
      invalidProfile: 'Выбери профиль из списка.',
      invalidLanguage: 'Выбери язык из списка.',
      invalidRegion: 'Укажи GLOBAL или двухбуквенный код региона, например KZ.',
      invalidSiteType: 'Выбери тип сайта из списка.',
      invalidGoal: 'Укажи бизнес-цель.',
      invalidTime: 'Укажи время суточного запуска.',
      invalidTimezone: 'Укажи действующий часовой пояс IANA, например Europe/Berlin.',
      invalidMode: 'Выбери режим из списка.',
      invalidMaxPages: 'Укажи целое число страниц от 1 до 100.',
      invalidOwnership: 'Подтверди право на аудит, чтобы сохранить готовую к запуску цель.',
      timeoutError: 'Проверка не ответила вовремя. Проверь подключение к Extella и запусти её ещё раз.',
      pollTimeoutError: 'Проверка превысила отведённое время. Проверь очередь и запусти её ещё раз.',
      unavailableError: 'Extella сейчас не получила результат. Проверь подключение и повтори проверку.',
      invalidUrlError: 'Адрес сайта не принят. Укажи публичный URL и повтори проверку.',
      ownershipError: 'Сначала подтверди право на аудит и сохрани цель.',
      genericError: 'Проверку не удалось завершить. Проверь адрес и подключение, затем запусти её ещё раз.',
      settingsError: 'Цель не сохранилась. Проверь поля и подключение к Extella, затем повтори.',
      standaloneStatus: 'Открой эту панель внутри Extella, чтобы выполнить проверку.',
    },
    en: {
      pageTitle: 'Extella SEO Employee',
      productLabel: 'Extella · SEO Employee · Closed Pilot',
      title: 'Check sites and get evidence-backed tasks',
      lead: 'Choose a target, save its settings, and start with a site check.',
      capabilitiesLabel: 'Included',
      capabilityAudit: 'Technical audit',
      capabilityCoverage: 'Coverage and queue',
      capabilityPlan: 'Work plan',
      targetsLabel: 'Targets',
      targetsTitle: 'Choose a target',
      newTarget: 'Add target',
      siteUrlLocked: 'A saved target URL cannot be changed. Create a new target for another URL.',
      noTargets: 'There are no targets yet. Create the first target to start a check.',
      targetFormLabel: 'Target settings',
      targetFormTitle: 'Site and schedule',
      targetFormHint: 'Save the target, confirm audit authority, and start the check.',
      targetNameLabel: 'Target name',
      siteUrlLabel: 'Public site URL',
      siteUrlPlaceholder: 'https://example.com',
      profileLabel: 'Profile',
      profileService: 'B2B service',
      profileEcommerce: 'Ecommerce',
      profileLocal: 'Local business',
      profileContent: 'Content and media',
      profileSaas: 'SaaS and marketplace',
      languageLabel: 'Language',
      languageRu: 'Русский',
      languageEn: 'English',
      regionLabel: 'Region',
      siteTypeLabel: 'Site type',
      siteTypeWebsite: 'Website',
      siteTypeStore: 'Store',
      siteTypeService: 'Service',
      siteTypePublication: 'Publication',
      businessGoalLabel: 'Business goal',
      dailyTimeLabel: 'Daily run time',
      timezoneLabel: 'IANA timezone',
      timezonePlaceholder: 'Europe/Berlin',
      modeLabel: 'Mode',
      modeFull: 'Full audit',
      modeDaily: 'Daily monitor',
      modePerformance: 'Search performance',
      modeWorkPlan: 'Work plan',
      maxPagesLabel: 'Maximum pages',
      ownershipLabel: 'I have site ownership or authority to run this audit',
      saveTarget: 'Save target',
      savingTarget: 'Saving target…',
      selectedTargetLabel: 'Selected target',
      noSelectedTarget: 'Choose a target on the left',
      selectTargetHint: 'The state, coverage, and tasks will appear here after saving.',
      runLabel: 'Run',
      runTitle: 'Check the selected site',
      optionalSourcesTitle: 'Optional sources',
      optionalSourcesStatus: 'Not connected',
      gscStatus: 'Not configured, optional',
      dataForSeoStatus: 'Not configured, optional',
      optionalSourcesHint: 'Open the source settings in Extella to connect one. The free technical audit works without these connections.',
      panelLabel: 'SEO Employee panel',
      panelVersionLabel: 'Panel version',
      targetListLabel: 'Target list',
      findingComparisonLabel: 'Finding comparison',
      severityCritical: 'critical',
      severityError: 'error',
      severityWarning: 'warning',
      severityInfo: 'information',
      runAction: 'Check site',
      runningAction: 'Checking…',
      readyStatus: 'Ready to check.',
      saveTargetFirst: 'Save the target first.',
      ownershipRequired: 'Confirm audit authority and save the target.',
      targetReady: 'The target is ready to check.',
      checkingStatus: 'The check started. Collecting independent sources.',
      loadingState: 'Loading the latest state…',
      runningStatus: 'A check is already running. Refreshing the state…',
      queuedStatus: 'The target is queued. Showing its position and wait reason.',
      duplicateStatus: 'This check is already running or has finished. Refreshing the state…',
      emptyTitle: 'No checks yet',
      emptyText: 'Save the target and select “Check site”. Tasks and evidence will appear here.',
      runningTitle: 'Checking public pages',
      runningText: 'Collecting independent sources and preparing tasks.',
      queuedTitle: 'The check is waiting in the queue',
      queuedText: 'The run is saved. The queue shows its position and wait reason.',
      failedTitle: 'The check could not finish',
      failedText: 'Check the public address and connection, then run the check again.',
      partialTitle: 'The result is incomplete',
      runTime: 'Check time',
      resultMode: 'Mode',
      sourceStates: 'Sources',
      newFindings: 'New',
      fixedFindings: 'Fixed',
      unchangedFindings: 'Unchanged',
      emptyGroup: 'There are no findings in this group.',
      countOnly: 'The count is available, but detailed cards are not.',
      stateEmpty: 'No checks yet',
      stateReady: 'Ready',
      statePartial: 'Partial',
      stateRunning: 'Checking',
      stateQueued: 'Queued',
      stateFailed: 'Needs attention',
      sourceOk: 'ready',
      sourceNotConfigured: 'not configured',
      sourceUnavailable: 'unavailable',
      sourceFailed: 'failed',
      sourceUnsupported: 'unsupported',
      sourceUnknown: 'Status not provided',
      sourceInstruction: 'Check the source settings and run the check again.',
      severity: 'Severity',
      target: 'Target',
      url: 'URL',
      fact: 'Fact',
      sources: 'Sources',
      businessImpact: 'Business impact',
      minimalFix: 'Minimum fix',
      verification: 'How to verify',
      evidence: 'Evidence',
      noEvidence: 'No separate evidence was provided.',
      unnamedSource: 'Unnamed source',
      modelLimitation: 'The model did not enrich the tasks. Only verified facts, evidence, and verification steps are shown below.',
      modelUnavailable: 'The model is unavailable. Deterministic facts and evidence are preserved.',
      modelNotNeeded: 'The model was not needed for this result.',
      missingSources: 'Coverage limitation: ',
      coverageLabel: 'Coverage',
      coverageTitle: 'What was checked',
      coverageUnknown: 'No data yet',
      coverageReady: 'Coverage saved',
      coveragePartial: 'Coverage is incomplete',
      plannedPages: 'Planned pages',
      crawledPages: 'Crawled pages',
      sampledPages: 'Sampled pages',
      categoriesTitle: 'Categories',
      completedSourcesTitle: 'Completed',
      unavailableSourcesTitle: 'Unavailable',
      unmappedTitle: 'Unmapped rules',
      none: 'No data',
      queueLabel: 'Queue',
      queueTitle: 'Current and next runs',
      queueNoPosition: 'No position',
      queuePosition: 'Queue position: ',
      queueCurrent: 'Current run',
      queueIdle: 'No active run',
      queueReason: 'Wait reason',
      queueNoReason: 'No wait required',
      queueEmpty: 'There are no waiting targets.',
      queueWaiting: 'Waiting for another run',
      scheduleTime: 'Time',
      nextRun: 'Next daily run',
      notScheduled: 'Not calculated yet',
      proposalsLabel: 'Action proposals',
      proposalsTitle: 'Manual execution only',
      proposalStatus: 'Confirmation required',
      proposalsEmpty: 'Separate evidence-backed proposals appear here after model enrichment.',
      proposalTarget: 'Target',
      proposalChange: 'Change',
      proposalOperation: 'Operation',
      proposalEvidence: 'Evidence',
      proposalPreview: 'Preview',
      proposalRollback: 'Rollback',
      proposalExpiry: 'Expires',
      proposalConfirmation: 'Confirmation',
      proposalInstruction: 'Manual instruction',
      copyInstruction: 'Copy instruction',
      copiedInstruction: 'Instruction copied.',
      copyUnavailable: 'Copy the instruction manually from the card.',
      proposed: 'Proposed',
      confirmed: 'Confirmed',
      notConfirmed: 'Not confirmed',
      helpTitle: '? How it works',
      helpSources: 'CrawlSEO and SEOmator independently inspect public pages. Google Search Console and DataForSEO remain optional sources.',
      helpCoverage: 'Coverage shows the plan, pages actually checked, and unmapped rules. An incomplete result is never called complete.',
      helpLimits: 'The SEO Employee does not guarantee rankings, traffic, or revenue; it also does not guarantee leads. Without a usable search-analytics source it cannot determine traffic loss.',
      helpRollback: 'The site owner decides whether to accept a task and how to roll back a change. The panel never changes the site.',
      dataTitle: 'What data is stored',
      dataCategories: 'Categories: target settings, page addresses, facts, sources, evidence, and queue statuses.',
      dataLocation: 'Processing and storage: in the site owner’s Extella environment.',
      dataRecipient: 'Model-input recipient: the owner-selected model provider receives only the allowed task minimum.',
      dataRetention: 'Retention: until the owner removes the saved target or report.',
      dataRemoval: 'Removal: the owner deletes the target and related local data through the Extella environment.',
      dataConsent: 'Google Search Console and DataForSEO do not block the free technical audit and connect only after separate explicit consent.',
      invalidName: 'Enter a target name.',
      invalidUrl: 'Enter a public URL using http or https, without a local or private address.',
      invalidProfile: 'Choose a profile from the list.',
      invalidLanguage: 'Choose a language from the list.',
      invalidRegion: 'Enter GLOBAL or a two-letter region code, for example KZ.',
      invalidSiteType: 'Choose a site type from the list.',
      invalidGoal: 'Enter a business goal.',
      invalidTime: 'Enter the daily run time.',
      invalidTimezone: 'Enter a valid IANA timezone, for example Europe/Berlin.',
      invalidMode: 'Choose a mode from the list.',
      invalidMaxPages: 'Enter a whole number of pages from 1 to 100.',
      invalidOwnership: 'Confirm audit authority to save a runnable target.',
      timeoutError: 'The check did not answer in time. Check the Extella connection and run it again.',
      pollTimeoutError: 'The check exceeded its time limit. Check the queue and run it again.',
      unavailableError: 'Extella did not receive a result. Check the connection and run the check again.',
      invalidUrlError: 'The site address was not accepted. Enter a public URL and run the check again.',
      ownershipError: 'Confirm audit authority and save the target first.',
      genericError: 'The check could not finish. Check the address and connection, then run it again.',
      settingsError: 'The target was not saved. Check the fields and Extella connection, then try again.',
      standaloneStatus: 'Open this panel inside Extella to run a check.',
    },
  };

  let language = 'ru';
  let bridge = null;
  let currentState = {
    state: 'empty',
    config: {},
    targets: [],
    selected_target: null,
    last_report: null,
    schedules: [],
  };
  let selectedTargetId = null;
  let savedTargetId = null;
  let runBusy = false;
  let suppressDirty = false;
  let pollTimer = null;
  let pollToken = 0;
  let stateRequestToken = 0;
  let pollTargetId = null;
  let pollStartedAt = 0;
  let pollDeadlineAt = 0;

  function t(key) { return COPY[language][key] || COPY.ru[key] || key; }

  async function healStaleCache() {
    if (!/^https?:$/.test(window.location.protocol)) return false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch(window.location.pathname, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) return false;
      const fresh = await response.text();
      const match = fresh.match(/const PANEL_VERSION\s*=\s*['"]([^'"]+)['"]/);
      if (match && match[1] && match[1] !== PANEL_VERSION) {
        window.location.replace(window.location.pathname + '?fresh=' + Date.now() + window.location.hash);
        return true;
      }
    } catch {
      return false;
    } finally {
      window.clearTimeout(timer);
    }
    return false;
  }

  function applyTheme(theme) {
    if (theme === 'light') document.documentElement.setAttribute('data-lm', '1');
    if (theme === 'dark') document.documentElement.removeAttribute('data-lm');
  }

  function applyLanguage(value) {
    const requested = String(value || '').toLowerCase();
    language = requested.startsWith('en') ? 'en' : 'ru';
    document.documentElement.lang = language;
    document.title = t('pageTitle');
    document.querySelectorAll('[data-i18n]').forEach(node => {
      node.textContent = t(node.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(node => {
      node.placeholder = t(node.dataset.i18nPlaceholder);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(node => {
      node.setAttribute('aria-label', t(node.dataset.i18nAria));
    });
    renderState(currentState, { announce: false, preserveInputs: true });
  }

  function handleHostMessage(data) {
    if (!isObject(data)) return;
    applyTheme(data.theme);
    if (data.type === 'etb_init') {
      applyLanguage(data.language || data.lang || data.locale || 'ru');
    }
  }

  function showView(name) {
    STATE_VIEWS.forEach(view => {
      const node = el('state-' + view);
      if (node) node.hidden = view !== name;
    });
  }

  function announce(message) {
    const node = el('live-status');
    if (node) node.textContent = message;
  }

  function isWorkState(state) {
    return state === 'running' || state === 'queued' || state === 'duplicate';
  }

  function selectedTarget() {
    const listed = currentState.targets.find(target => target && target.target_id === selectedTargetId);
    if (listed) return listed;
    if (isObject(currentState.selected_target)) return currentState.selected_target;
    return null;
  }

  function targetState(target) {
    if (isObject(target)) {
      const queued = queueViewModel(currentState, target.target_id).selectedItem;
      if (queued && ['queued', 'running'].includes(queued.status)) return queued.status;
      if (typeof target.state === 'string') return target.state;
      if (typeof target.last_state === 'string') return target.last_state;
      if (typeof target.status === 'string' && ['running', 'queued', 'ready', 'partial', 'failed', 'empty'].includes(target.status)) {
        return target.status;
      }
    }
    return target && target.target_id === selectedTargetId ? currentState.state : 'empty';
  }

  function stateLabel(state) {
    const key = ({
      ready: 'stateReady',
      partial: 'statePartial',
      running: 'stateRunning',
      queued: 'stateQueued',
      duplicate: 'stateRunning',
      failed: 'stateFailed',
      empty: 'stateEmpty',
    })[state] || 'stateEmpty';
    return t(key);
  }

  function profileLabel(profile) {
    return ({
      service_b2b: t('profileService'),
      ecommerce: t('profileEcommerce'),
      local_business: t('profileLocal'),
      content_media: t('profileContent'),
      saas_marketplace: t('profileSaas'),
    })[profile] || asText(profile, '—');
  }

  function modeLabel(mode) {
    return ({
      full_audit: t('modeFull'),
      daily_monitor: t('modeDaily'),
      search_performance: t('modePerformance'),
      work_plan: t('modeWorkPlan'),
    })[mode] || asText(mode, '—');
  }

  function normalizeTarget(value) {
    if (!isObject(value)) return null;
    const target = { ...value };
    if (typeof target.target_id !== 'string' || !target.target_id.trim()) return null;
    return target;
  }

  function extractSelectedTarget(value) {
    if (!isObject(value)) return null;
    return normalizeTarget(value.selected_target)
      || normalizeTarget(value.target)
      || normalizeTarget(value.config && value.config.target)
      || (isObject(value.config) && typeof value.config.target_id === 'string' ? normalizeTarget(value.config) : null);
  }

  function extractTargets(value) {
    if (!isObject(value)) return [];
    const lists = [
      value.targets,
      value.target_summaries,
      value.config && value.config.targets,
      value.data && value.data.targets,
    ];
    let raw = lists.find(Array.isArray) || [];
    const selected = extractSelectedTarget(value);
    if (!raw.length && selected) raw = [selected];
    const result = [];
    const ids = new Set();
    raw.forEach(item => {
      const target = normalizeTarget(item);
      if (target && !ids.has(target.target_id)) {
        ids.add(target.target_id);
        result.push(target);
      }
    });
    return result;
  }

  function selectBootstrapTarget(value, preferredId = null) {
    const targets = extractTargets(value);
    return targets.find(target => target.target_id === preferredId) || targets[0] || null;
  }

  function mergeTargets(existing, incoming) {
    const result = [];
    const byId = new Map();
    [...(Array.isArray(existing) ? existing : []), ...(Array.isArray(incoming) ? incoming : [])].forEach(item => {
      const target = normalizeTarget(item);
      if (!target) return;
      const prior = byId.get(target.target_id);
      const merged = prior ? { ...prior, ...target } : target;
      if (!prior) result.push(merged);
      else result[result.indexOf(prior)] = merged;
      byId.set(target.target_id, merged);
    });
    return result;
  }

  const ACTIVE_QUEUE_STATUSES = new Set(['queued', 'running']);
  const TERMINAL_STATES = new Set(['empty', 'ready', 'partial', 'failed']);

  function queueViewModel(state, targetId = null) {
    const source = isObject(state) ? state : {};
    const sourceTargetId = source.target_id
      || (isObject(source.selected_target) && source.selected_target.target_id)
      || null;
    const queue = isObject(source.queue) ? source.queue : {};
    const items = Array.isArray(queue.items)
      ? queue.items.filter(item => isObject(item))
      : [];
    const selectedItem = targetId
      ? items.find(item => item.target_id === targetId) || null
      : null;
    const activeItems = items.filter(item => ACTIVE_QUEUE_STATUSES.has(item.status));
    let position = null;
    if (selectedItem && ACTIVE_QUEUE_STATUSES.has(selectedItem.status)) {
      const itemPosition = Number(selectedItem.position);
      if (Number.isInteger(itemPosition) && itemPosition > 0) position = itemPosition;
    }
    if (position === null && (!targetId || sourceTargetId === targetId)) {
      const queuePosition = Number(queue.position);
      if (Number.isInteger(queuePosition) && queuePosition > 0) position = queuePosition;
    }
    if (position === null && selectedItem && ACTIVE_QUEUE_STATUSES.has(selectedItem.status)) {
      const itemIndex = activeItems.indexOf(selectedItem);
      if (itemIndex >= 0) position = itemIndex + 1;
    }
    const runningItem = activeItems.find(item => item.status === 'running') || null;
    const current = runningItem || (isObject(queue.current) ? queue.current : null);
    const reason = selectedItem && (selectedItem.reason || selectedItem.wait_reason)
      || queue.reason
      || queue.wait_reason
      || (selectedItem && selectedItem.status === 'queued' ? 'worker_busy' : null);
    return {
      items,
      activeItems,
      selectedItem,
      activeSelected: Boolean(selectedItem && ACTIVE_QUEUE_STATUSES.has(selectedItem.status)),
      current,
      position,
      reason,
    };
  }

  function queuePayloadState(value) {
    if (!isObject(value)) return null;
    const targetId = value.target_id
      || (isObject(value.selected_target) && value.selected_target.target_id)
      || selectedTargetId;
    const view = queueViewModel(value, targetId);
    if (view.activeSelected) return view.selectedItem.status;
    const item = value.queue_item || value.queueItem;
    if (isObject(item) && ACTIVE_QUEUE_STATUSES.has(item.status)) return item.status;
    return null;
  }

  function shouldContinuePolling(state, targetId) {
    const source = isObject(state) ? state : {};
    const view = queueViewModel(source, targetId);
    if (view.activeSelected) return true;
    return !TERMINAL_STATES.has(source.state);
  }

  function normalizeStatePayload(payload) {
    let source = payload;
    if (typeof source === 'string') {
      try { source = JSON.parse(source); } catch { throw new Error('Unexpected expert result'); }
    }
    if (!isObject(source)) throw new Error('Unexpected expert result');
    const next = { ...currentState, ...source };
    const incomingTargets = extractTargets(source);
    next.targets = mergeTargets(currentState.targets, incomingTargets);
    const selected = extractSelectedTarget(source);
    if (selected) {
      next.selected_target = selected;
      next.targets = mergeTargets(next.targets, [selected]);
    }
    if (typeof source.target_id === 'string') {
      const byId = next.targets.find(target => target.target_id === source.target_id);
      if (byId) next.selected_target = byId;
    }
    if (source.report && isObject(source.report)) next.last_report = source.report;
    if (source.last_report && isObject(source.last_report)) next.last_report = source.last_report;
    if (source.run && Array.isArray(source.tasks) && !source.last_report) next.last_report = source;
    const queueState = queuePayloadState(source);
    const explicitState = typeof source.state === 'string' ? source.state : '';
    if (queueState) next.state = queueState;
    else if (['empty', 'running', 'ready', 'partial', 'failed', 'duplicate', 'queued'].includes(explicitState)) next.state = explicitState || 'empty';
    else if (source.run && Array.isArray(source.tasks)) next.state = 'ready';
    return next;
  }

  function setRunBusy(busy) {
    runBusy = busy;
    syncRunButton();
  }

  function runAllowed() {
    const target = selectedTarget();
    return Boolean(target
      && selectedTargetId
      && savedTargetId === selectedTargetId
      && target.ownership_confirmed === true
      && typeof target.site_url === 'string'
      && target.site_url);
  }

  function syncRunButton() {
    const button = el('run-audit');
    if (!button) return;
    const waiting = runBusy || isWorkState(currentState.state);
    button.disabled = waiting || !runAllowed();
    button.textContent = waiting ? t('runningAction') : t('runAction');
    const guidance = el('run-guidance');
    if (!guidance) return;
    if (waiting) guidance.textContent = t(currentState.state === 'queued' ? 'queuedStatus' : 'checkingStatus');
    else if (!selectedTargetId || !selectedTarget()) guidance.textContent = t('selectTargetHint');
    else if (savedTargetId !== selectedTargetId) guidance.textContent = t('saveTargetFirst');
    else if (selectedTarget().ownership_confirmed !== true) guidance.textContent = t('ownershipRequired');
    else guidance.textContent = t('targetReady');
  }

  function publicUrl(value) {
    let parsed;
    try { parsed = new URL(value); } catch { return null; }
    const host = parsed.hostname.toLowerCase();
    const privateV4 = /^(127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
    const privateV6 = host === '::1' || host === '[::1]' || /^\[?f[cd]/.test(host) || /^\[?fe8/.test(host);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password
        || host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')
        || privateV4 || privateV6) return null;
    return parsed.toString();
  }

  function validTimezone(value) {
    try {
      new Intl.DateTimeFormat('en', { timeZone: value }).format();
      return true;
    } catch {
      return false;
    }
  }

  function validRegion(value) {
    const region = String(value || '').trim().toUpperCase();
    return ALLOWED_REGIONS.has(region);
  }

  function validationError(message, focusId) {
    const node = el('settings-error');
    if (node) {
      node.textContent = message;
      node.hidden = false;
    }
    announce(message);
    if (focusId) {
      const focusable = el(focusId);
      if (focusable) focusable.focus();
    }
    return null;
  }

  function readTargetForm() {
    const error = el('settings-error');
    if (error) error.hidden = true;
    const name = el('target-name').value.trim();
    if (!name) return validationError(t('invalidName'), 'target-name');
    const siteUrl = publicUrl(el('site-url').value.trim());
    if (!siteUrl) return validationError(t('invalidUrl'), 'site-url');
    const profile = el('profile').value;
    if (!PROFILES.includes(profile)) return validationError(t('invalidProfile'), 'profile');
    const targetLanguage = el('language').value;
    if (!LANGUAGES.includes(targetLanguage)) return validationError(t('invalidLanguage'), 'language');
    const region = el('region').value.trim().toUpperCase();
    if (!validRegion(region)) return validationError(t('invalidRegion'), 'region');
    const siteType = el('site-type').value;
    if (!SITE_TYPES.includes(siteType)) return validationError(t('invalidSiteType'), 'site-type');
    const businessGoal = el('business-goal').value.trim();
    if (!businessGoal) return validationError(t('invalidGoal'), 'business-goal');
    const dailyRunTime = el('daily-time').value;
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(dailyRunTime)) return validationError(t('invalidTime'), 'daily-time');
    const timezone = el('timezone').value.trim();
    if (!timezone || !validTimezone(timezone)) return validationError(t('invalidTimezone'), 'timezone');
    const mode = el('mode').value;
    if (!MODES.includes(mode)) return validationError(t('invalidMode'), 'mode');
    const maxPages = Number(el('max-pages').value);
    if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 100) return validationError(t('invalidMaxPages'), 'max-pages');
    const ownershipConfirmed = el('ownership-confirmed').checked === true;
    const settings = {
      target_name: name,
      site_url: siteUrl,
      profile,
      language: targetLanguage,
      region,
      site_type: siteType,
      business_goal: businessGoal,
      daily_run_time: dailyRunTime,
      timezone,
      mode,
      max_pages: maxPages,
      ownership_confirmed: ownershipConfirmed,
    };
    if (selectedTargetId) settings.target_id = selectedTargetId;
    return settings;
  }

  function fillTargetForm(target = {}) {
    suppressDirty = true;
    el('target-name').value = asText(target.target_name);
    el('site-url').value = asText(target.site_url);
    const existingTarget = typeof target.target_id === 'string' && target.target_id.length > 0;
    el('site-url').disabled = existingTarget;
    el('site-url-help').hidden = !existingTarget;
    el('site-url-help').textContent = existingTarget ? t('siteUrlLocked') : '';
    el('profile').value = PROFILES.includes(target.profile) ? target.profile : 'service_b2b';
    el('language').value = LANGUAGES.includes(target.language) ? target.language : 'ru';
    el('region').value = asText(target.region, 'GLOBAL');
    el('site-type').value = SITE_TYPES.includes(target.site_type) ? target.site_type : 'website';
    el('business-goal').value = asText(target.business_goal);
    el('daily-time').value = asText(target.daily_run_time, '09:00');
    el('timezone').value = asText(target.timezone, 'UTC');
    el('mode').value = MODES.includes(target.mode) ? target.mode : 'full_audit';
    el('max-pages').value = String(Number.isInteger(target.max_pages) ? target.max_pages : 25);
    el('ownership-confirmed').checked = target.ownership_confirmed === true;
    suppressDirty = false;
  }

  function formatDate(value) {
    if (!value) return t('notScheduled');
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return t('notScheduled');
    return new Intl.DateTimeFormat(language === 'en' ? 'en' : 'ru', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date);
  }

  function nextRunFrom(state) {
    const source = isObject(state) ? state : {};
    const schedules = source.schedules;
    if (Array.isArray(schedules)) {
      const schedule = schedules.find(item => isObject(item) && own(item, 'next_run'));
      if (schedule) return schedule.next_run;
      const legacySchedule = schedules.find(item => isObject(item) && own(item, 'next_run_at'));
      if (legacySchedule) return legacySchedule.next_run_at;
    }
    if (isObject(schedules) && own(schedules, 'next_run')) return schedules.next_run;
    if (isObject(schedules) && own(schedules, 'next_run_at')) return schedules.next_run_at;
    if (own(source, 'next_run')) return source.next_run;
    if (own(source, 'next_run_at')) return source.next_run_at;
    if (source.config && own(source.config, 'next_run')) return source.config.next_run;
    if (source.config && own(source.config, 'next_run_at')) return source.config.next_run_at;
    return null;
  }

  function renderSchedule(state) {
    const target = selectedTarget() || {};
    const config = isObject(state.config) ? state.config : {};
    const time = asText(target.daily_run_time || config.daily_run_time, el('daily-time').value || '09:00');
    const timezone = asText(target.timezone || config.timezone, el('timezone').value || 'UTC');
    const next = nextRunFrom(state);
    el('schedule-time').textContent = time + ' ' + timezone;
    el('next-run').textContent = formatDate(next);
    const queue = isObject(state.queue) ? state.queue : {};
    if (!next && queue.next_run) el('next-run').textContent = formatDate(queue.next_run);
  }

  function sourceStatus(status) {
    const key = ({
      ok: 'sourceOk',
      not_configured: 'sourceNotConfigured',
      unavailable: 'sourceUnavailable',
      failed: 'sourceFailed',
      unsupported: 'sourceUnsupported',
    })[status];
    return key ? t(key) : t('sourceUnknown');
  }

  function severityLabel(severity) {
    const key = ({
      critical: 'severityCritical',
      error: 'severityError',
      warning: 'severityWarning',
      info: 'severityInfo',
    })[severity];
    return key ? t(key) : asText(severity, '—');
  }

  function textNode(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = asText(text, '—');
    return node;
  }

  function taskSources(task) {
    const sources = new Set();
    if (Array.isArray(task.sources)) task.sources.forEach(source => {
      sources.add(isObject(source) ? asText(source.name) : asText(source));
    });
    if (task.source) sources.add(asText(task.source));
    if (Array.isArray(task.evidence)) task.evidence.forEach(item => {
      if (isObject(item) && item.source) sources.add(asText(item.source));
    });
    return [...sources].filter(Boolean);
  }

  function addTaskField(list, label, value) {
    const row = document.createElement('div');
    row.append(textNode('dt', '', label), textNode('dd', '', value || '—'));
    list.append(row);
  }

  function modelStatus(report) {
    const value = report && report.model_enrichment;
    if (typeof value === 'string') return value;
    if (isObject(value) && typeof value.status === 'string') return value.status;
    return 'not_needed';
  }

  function taskHasModelFields(task) {
    return isObject(task)
      && typeof task.business_impact === 'string'
      && task.business_impact.trim()
      && typeof task.minimal_fix === 'string'
      && task.minimal_fix.trim();
  }

  function taskCard(task, index, showModelFields = false) {
    task = isObject(task) ? task : {};
    const card = document.createElement('article');
    card.className = 'task-card';
    const heading = document.createElement('div');
    heading.className = 'task-heading';
    heading.append(
      textNode('h3', '', asText(task.rule_key || task.confirmed_fact || task.fact, t('fact'))),
      textNode('span', 'severity', severityLabel(task.severity)),
    );
    card.append(heading, textNode('p', 'task-url', asText(task.url, '—')));

    const fields = document.createElement('dl');
    fields.className = 'task-fields';
    addTaskField(fields, t('severity'), severityLabel(task.severity));
    addTaskField(fields, t('target'), asText(task.target_name || task.target_id, selectedTargetId || '—'));
    addTaskField(fields, t('url'), asText(task.url, '—'));
    addTaskField(fields, t('fact'), asText(task.confirmed_fact || task.fact, '—'));
    addTaskField(fields, t('sources'), taskSources(task).join(', ') || '—');
    if (showModelFields && taskHasModelFields(task)) {
      addTaskField(fields, t('businessImpact'), task.business_impact);
      addTaskField(fields, t('minimalFix'), task.minimal_fix);
    }
    addTaskField(fields, t('verification'), asText(task.verification, '—'));
    card.append(fields);

    const details = document.createElement('details');
    details.className = 'evidence';
    const summary = textNode('summary', '', t('evidence'));
    summary.dataset.testid = `evidence-${EVIDENCE_TEST_IDS[index]}`;
    details.append(summary);
    const evidenceList = document.createElement('ul');
    evidenceList.className = 'evidence-list';
    const evidence = Array.isArray(task.evidence) ? task.evidence : [];
    if (!evidence.length) evidenceList.append(textNode('li', '', t('noEvidence')));
    evidence.forEach(item => {
      const entry = document.createElement('li');
      entry.append(
        textNode('strong', '', asText(item?.source, t('unnamedSource'))),
        textNode('span', '', [item && item.fact, item && (item.rule || item.source_rule)].filter(Boolean).map(String).join(' · ') || '—'),
      );
      evidenceList.append(entry);
    });
    details.append(evidenceList);
    card.append(details);
    return card;
  }

  function explicitGroupItems(report, key) {
    const source = isObject(report) ? report : {};
    const comparison = isObject(source.comparison) ? source.comparison : {};
    const values = [comparison[key + '_items'], comparison[key], source[key + '_findings']];
    return values.find(Array.isArray) || null;
  }

  function buildReportModel(report, limit = 10, stateName = '') {
    const source = isObject(report) ? report : {};
    const comparison = isObject(source.comparison) ? source.comparison : {};
    const fullTasks = Array.isArray(source.tasks)
      ? source.tasks.filter(task => isObject(task)) : [];
    const fullById = new Map(
      fullTasks
        .filter(task => task.task_id !== undefined && task.task_id !== null)
        .map(task => [String(task.task_id), task]),
    );
    const result = { new: [], fixed: [], unchanged: [] };
    const explicit = GROUPS.some(key => explicitGroupItems(source, key));
    if (explicit) {
      GROUPS.forEach(key => {
        result[key] = (explicitGroupItems(source, key) || []).map(item => {
          const stub = isObject(item) ? item : {};
          const full = stub.task_id === undefined || stub.task_id === null
            ? null : fullById.get(String(stub.task_id));
          return full ? { ...stub, ...full } : { ...stub };
        });
      });
    } else {
      fullTasks.forEach(task => {
        const group = GROUPS.includes(task.comparison_group) ? task.comparison_group : 'new';
        result[group].push({ ...task });
      });
    }
    const partial = stateName === 'partial' || source.state === 'partial';
    const counts = {};
    GROUPS.forEach(key => {
      const numeric = typeof comparison[key] === 'number' && comparison[key] >= 0 ? comparison[key] : result[key].length;
      counts[key] = partial && key === 'fixed' ? 0 : numeric;
    });
    if (partial) result.fixed = [];
    let remaining = Number.isInteger(limit) && limit >= 0 ? limit : 10;
    GROUPS.forEach(key => {
      result[key] = result[key].slice(0, remaining);
      remaining -= result[key].length;
    });
    return { groups: result, counts };
  }

  function hydrateReportModel(report, limit = 10) {
    return buildReportModel(report, limit);
  }

  function groupedTasks(report) {
    return buildReportModel(report).groups;
  }

  function renderGroup(key, items, comparison, startIndex, showModelFields) {
    const container = el(key + '-findings');
    container.replaceChildren();
    const numericCount = typeof comparison[key] === 'number' && comparison[key] >= 0 ? comparison[key] : items.length;
    el(key + '-count').textContent = String(numericCount);
    if (!items.length) {
      container.append(textNode('p', 'empty-group', numericCount ? t('countOnly') : t('emptyGroup')));
      return startIndex;
    }
    items.forEach((task, offset) => container.append(taskCard(task, startIndex + offset, showModelFields)));
    return startIndex + items.length;
  }

  function coverageValue(value) {
    return Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
  }

  function listValues(value) {
    if (!Array.isArray(value)) return [];
    return value.map(item => isObject(item) ? asText(item.name || item.source || item.rule_key) : asText(item)).filter(Boolean);
  }

  function buildCoverageModel(coverage) {
    const value = isObject(coverage) ? coverage : {};
    return {
      planned_pages: coverageValue(value.planned_pages),
      crawled_pages: coverageValue(value.crawled_pages),
      sampled_pages: coverageValue(value.sampled_pages),
      categories: listValues(value.categories || value.applied_categories),
      completed_sources: listValues(value.completed_sources || value.completed),
      unavailable_sources: listValues(value.unavailable_sources || value.unavailable),
      unmapped_rules: listValues(value.unmapped_rules),
      complete: value.complete !== false && value.incomplete !== true,
    };
  }

  function renderList(id, values) {
    const list = el(id);
    list.replaceChildren();
    if (!values.length) {
      list.append(textNode('li', '', t('none')));
      return;
    }
    values.forEach(value => list.append(textNode('li', '', value)));
  }

  function renderCoverage(report, stateName) {
    const coverage = buildCoverageModel(report && report.coverage);
    el('coverage-planned').textContent = String(coverage.planned_pages);
    el('coverage-crawled').textContent = String(coverage.crawled_pages);
    el('coverage-sampled').textContent = String(coverage.sampled_pages);
    renderList('coverage-categories', coverage.categories);
    renderList('coverage-completed', coverage.completed_sources);
    renderList('coverage-unavailable', coverage.unavailable_sources);
    el('unmapped-count').textContent = String(coverage.unmapped_rules.length);
    renderList('coverage-unmapped', coverage.unmapped_rules);
    el('unmapped-wrap').hidden = coverage.unmapped_rules.length === 0;
    const partial = stateName === 'partial' || coverage.complete === false;
    el('coverage-status').textContent = partial
      ? t('coveragePartial')
      : coverage.planned_pages || coverage.crawled_pages || coverage.categories.length
        ? t('coverageReady') : t('coverageUnknown');
  }

  function sourceEntries(report) {
    const sources = Array.isArray(report && report.sources) ? report.sources : [];
    return sources.filter(source => isObject(source));
  }

  function renderSourceList(report) {
    const list = el('source-list');
    list.replaceChildren();
    const sources = sourceEntries(report);
    if (!sources.length) {
      list.append(textNode('li', '', t('sourceUnknown')));
      return;
    }
    sources.forEach(source => {
      const item = document.createElement('li');
      const name = textNode('span', 'source-name', asText(source.name, t('unnamedSource')));
      const status = textNode('span', 'source-status', sourceStatus(source.status));
      item.append(name, textNode('span', '', ' · '), status);
      if (!['ok', 'not_configured'].includes(source.status) || source.status === 'not_configured') {
        const guidance = asText(source.instruction || source.connection_instruction, t('sourceInstruction'));
        item.append(textNode('span', 'source-guidance', guidance));
      }
      list.append(item);
    });
  }

  function proposalItems(report) {
    const result = [];
    const seen = new Set();
    const candidates = [
      ...(Array.isArray(report && report.action_proposals) ? report.action_proposals : []),
      ...((Array.isArray(report && report.tasks) ? report.tasks : []).map(task => task && task.action_proposal)),
    ];
    candidates.forEach(item => {
      if (!isObject(item)) return;
      const id = asText(item.proposal_id || item.id, '');
      if (!id || seen.has(id)) return;
      seen.add(id);
      result.push(item);
    });
    return result;
  }

  function addProposalField(list, label, value) {
    const row = document.createElement('div');
    row.append(textNode('dt', '', label), textNode('dd', '', value || '—'));
    list.append(row);
  }

  function proposalStatusLabel(value) {
    return ({
      proposed: t('proposed'),
      confirmed: t('confirmed'),
      required: t('notConfirmed'),
      pending: t('notConfirmed'),
    })[value] || asText(value, t('notConfirmed'));
  }

  async function copyInstruction(instruction) {
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(instruction);
        announce(t('copiedInstruction'));
        return;
      } catch {
        announce(t('copyUnavailable'));
        return;
      }
    }
    announce(t('copyUnavailable'));
  }

  function renderProposals(report) {
    const container = el('action-proposals');
    container.replaceChildren();
    const proposals = proposalItems(report);
    el('proposals-empty').hidden = proposals.length > 0;
    proposals.forEach((proposal, index) => {
      const card = document.createElement('article');
      card.className = 'proposal';
      card.append(textNode('h3', '', asText(proposal.change || proposal.operation, t('proposalsTitle'))));
      const fields = document.createElement('dl');
      fields.className = 'proposal-fields';
      addProposalField(fields, t('proposalTarget'), asText(proposal.target && proposal.target.site_url || proposal.site_url || selectedTarget()?.site_url, '—'));
      addProposalField(fields, t('proposalOperation'), asText(proposal.operation, 'manual_change'));
      addProposalField(fields, t('proposalChange'), asText(proposal.change, '—'));
      addProposalField(fields, t('proposalEvidence'), Array.isArray(proposal.evidence)
        ? proposal.evidence.map(item => isObject(item) ? asText(item.fact || item.source) : asText(item)).join(', ') : asText(proposal.evidence, '—'));
      addProposalField(fields, t('proposalPreview'), asText(proposal.preview, '—'));
      addProposalField(fields, t('proposalRollback'), asText(proposal.rollback, '—'));
      addProposalField(fields, t('proposalExpiry'), formatDate(proposal.expires_at));
      addProposalField(fields, t('proposalConfirmation'), proposalStatusLabel(proposal.confirmation || proposal.status));
      card.append(fields);
      const instruction = asText(proposal.instruction || proposal.manual_instruction || proposal.preview || proposal.change, '');
      if (instruction) {
        addProposalField(fields, t('proposalInstruction'), instruction);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'secondary';
        button.dataset.testid = 'copy-proposal-' + (EVIDENCE_TEST_IDS[index] || 'extra');
        button.textContent = t('copyInstruction');
        button.addEventListener('click', () => void copyInstruction(instruction));
        card.append(button);
      }
      container.append(card);
    });
  }

  function queueItems(state) {
    const queue = isObject(state.queue) ? state.queue : {};
    const candidates = [
      queue.items,
      queue.queue_items,
      state.queue_items,
      state.pending_queue,
    ];
    return (candidates.find(Array.isArray) || []).filter(item => isObject(item));
  }

  function queueTargetName(item) {
    if (!isObject(item)) return '—';
    if (item.target_name) return asText(item.target_name);
    const target = currentState.targets.find(value => value.target_id === item.target_id);
    return asText(target && target.target_name || item.target_id, '—');
  }

  function targetQueuePosition(target) {
    const fromQueue = queueViewModel(currentState, target && target.target_id).position;
    if (fromQueue !== null) return fromQueue;
    const direct = Number(target && target.queue_position);
    if (Number.isInteger(direct) && direct > 0) return direct;
    const waiting = queueItems(currentState).filter(item => item.status === 'queued' || item.status === 'running');
    const index = waiting.findIndex(item => item.target_id === (target && target.target_id));
    return index >= 0 ? index + 1 : null;
  }

  function queueReasonLabel(reason, selectedItem) {
    if (!reason) return selectedItem && selectedItem.status === 'queued' ? t('queueWaiting') : t('queueNoReason');
    if (reason === 'worker_busy') return t('queueWaiting');
    return /\s/.test(String(reason)) ? String(reason) : t('queueWaiting');
  }

  function renderQueue(state) {
    const target = selectedTarget();
    const view = queueViewModel(state, target && target.target_id);
    const current = view.current || state.current_run || state.active_run;
    const position = view.position !== null ? view.position : target && target.queue_position;
    const reason = view.reason || (target && target.queue_reason);
    el('queue-current').textContent = isObject(current)
      ? asText(current.run_id || current.queue_id || current.target_id, t('queueIdle'))
      : asText(current, t('queueIdle'));
    el('queue-position').textContent = Number.isInteger(Number(position))
      ? t('queuePosition') + Number(position) : t('queueNoPosition');
    const reasonText = queueReasonLabel(reason, view.selectedItem);
    el('queue-reason').textContent = reasonText;
    const list = el('queue-list');
    list.replaceChildren();
    const waiting = view.activeItems;
    waiting.forEach(item => {
      const row = document.createElement('li');
      row.append(
        textNode('strong', '', queueTargetName(item)),
        textNode('span', '', ' · ' + stateLabel(item.status)),
      );
      if (item.reason) row.append(textNode('span', 'source-guidance', queueReasonLabel(item.reason, item)));
      list.append(row);
    });
    el('queue-empty').hidden = waiting.length > 0;
  }

  function renderTargetList() {
    const list = el('target-list');
    list.replaceChildren();
    const targets = currentState.targets.filter(target => normalizeTarget(target));
    el('target-list-empty').hidden = targets.length > 0;
    targets.forEach(target => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'target-entry';
      button.setAttribute('aria-pressed', String(target.target_id === selectedTargetId));
      button.addEventListener('click', () => selectTarget(target.target_id));
      button.append(
        textNode('span', 'target-entry-name', asText(target.target_name, target.site_url)),
        textNode('span', 'target-entry-meta', profileLabel(target.profile)),
      );
      const meta = document.createElement('span');
      meta.className = 'target-entry-meta';
      meta.append(textNode('span', 'target-entry-state', stateLabel(targetState(target))));
       const position = targetQueuePosition(target);
       if (position !== null) meta.append(textNode('span', '', t('queuePosition') + position));
      button.append(meta);
      item.append(button);
      list.append(item);
    });
  }

  function renderTargetSummary() {
    const target = selectedTarget();
    if (!target) {
      el('selected-target-title').textContent = t('noSelectedTarget');
      el('target-summary').textContent = t('selectTargetHint');
      return;
    }
    el('selected-target-title').textContent = asText(target.target_name, target.site_url);
    el('target-summary').textContent = [
      profileLabel(target.profile),
      stateLabel(targetState(target)),
      asText(target.site_url, '—'),
    ].join(' · ');
  }

  function renderReport(report, stateName) {
    const source = isObject(report) ? report : {};
    el('run-time').textContent = formatDate(source.run && source.run.completed_at || source.completed_at || currentState.updated_at);
    el('result-mode').textContent = modeLabel(source.mode || (source.run && source.run.mode));
    renderSourceList(source);
    const partial = stateName === 'partial' || (source.coverage && source.coverage.complete === false);
    el('partial-notice').hidden = !partial;
    if (partial) {
      const coverage = buildCoverageModel(source.coverage);
      const missing = report.missing_data || coverage.unavailable_sources;
      el('partial-sources').textContent = t('missingSources') + (Array.isArray(missing) && missing.length ? missing.map(String).join(', ') : t('none'));
    }
    const status = modelStatus(source);
    const limitation = el('model-limitation');
    limitation.hidden = status === 'ok';
    limitation.textContent = status === 'unavailable'
      ? t('modelUnavailable')
      : status === 'not_needed' ? t('modelNotNeeded') : t('modelLimitation');
    const model = buildReportModel(source, 10, stateName);
    let index = 0;
    const comparison = isObject(source.comparison) ? { ...source.comparison } : {};
    if (stateName === 'partial') comparison.fixed = 0;
    GROUPS.forEach(key => {
      index = renderGroup(key, model.groups[key], comparison, index, status === 'ok');
    });
    renderCoverage(source, stateName);
    renderProposals(source);
  }

  function renderState(state, {
    announce: shouldAnnounce = true,
    preserveInputs = false,
    ignoreQueue = false,
  } = {}) {
    currentState = isObject(state) ? state : currentState;
    const queuedState = ignoreQueue ? null : queuePayloadState(currentState);
    if (queuedState) currentState = { ...currentState, state: queuedState };
    if (!preserveInputs) {
      const target = selectedTarget();
      if (target) fillTargetForm(target);
    }
    renderTargetList();
    renderTargetSummary();
    renderSchedule(currentState);
    renderQueue(currentState);
    syncRunButton();
    const stateName = currentState.state || 'empty';
    if (stateName === 'running' || stateName === 'queued' || stateName === 'duplicate') {
      showView('running');
      el('state-tag').textContent = stateName === 'queued' ? t('stateQueued') : t('stateRunning');
      el('running-stage').textContent = stateName === 'queued' ? t('queuedText') : t('runningText');
      if (shouldAnnounce) announce(t(stateName === 'queued' ? 'queuedStatus' : stateName === 'duplicate' ? 'duplicateStatus' : 'checkingStatus'));
      return;
    }
    if (stateName === 'failed') {
      showView('failed');
      el('state-tag').textContent = t('stateFailed');
      el('failed-reason').textContent = friendlyError(currentState.last_error || currentState.error);
      if (shouldAnnounce) announce(el('failed-reason').textContent);
      return;
    }
    if (stateName === 'ready' || stateName === 'partial') {
      const report = currentState.last_report || (currentState.run && currentState.tasks ? currentState : null);
      if (!report) {
        showView('failed');
        el('state-tag').textContent = t('stateFailed');
        el('failed-reason').textContent = t('genericError');
        if (shouldAnnounce) announce(t('genericError'));
        return;
      }
      showView('result');
      el('state-tag').textContent = t(stateName === 'partial' ? 'statePartial' : 'stateReady');
      renderReport(report, stateName);
      if (shouldAnnounce) announce(t(stateName === 'partial' ? 'partialTitle' : 'readyStatus'));
      return;
    }
    showView('empty');
    el('state-tag').textContent = t('stateEmpty');
    if (shouldAnnounce) announce(t('readyStatus'));
  }

  function friendlyError(raw, settings = false) {
    if (settings) return t('settingsError');
    const value = String(
      isObject(raw) ? raw.code || raw.message || raw.message_ru : raw || '',
    ).toLowerCase();
    if (/ownership_confirmation_required|ownership/.test(value)) return t('ownershipError');
    if (/poll_timeout|deadline/.test(value)) return t('pollTimeoutError');
    if (/timeout|time|время|timed out/.test(value)) return t('timeoutError');
    if (/url|address|адрес|public|private|loopback/.test(value)) return t('invalidUrlError');
    if (/unavailable|network|fetch|connection|недоступ|подключ/.test(value)) return t('unavailableError');
    return t('genericError');
  }

  function cancelPolling() {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
    pollToken += 1;
    stateRequestToken += 1;
    pollTargetId = null;
    pollStartedAt = 0;
    pollDeadlineAt = 0;
  }

  function remainingDeadline(deadlineAt, now = Date.now()) {
    const deadline = Number(deadlineAt);
    return Number.isFinite(deadline) ? Math.max(0, deadline - now) : 0;
  }

  function pollingDelay(deadlineAt, now = Date.now()) {
    const remaining = remainingDeadline(deadlineAt, now);
    return remaining ? Math.min(POLL_INTERVAL_MS, remaining) : 0;
  }

  function stateRequest(targetId, deadlineAt) {
    const remaining = remainingDeadline(deadlineAt);
    if (!remaining) return Promise.resolve({ ok: false, error: 'poll_timeout' });
    const params = targetId ? { method: 'state', target_id: targetId } : { method: 'state' };
    let timer = null;
    const timeout = new Promise(resolve => {
      timer = window.setTimeout(() => resolve({ ok: false, error: 'poll_timeout' }), remaining);
    });
    const call = bridge.run(EXPERT_STATE, params, {
      timeoutMs: Math.max(1, Math.min(240000, remaining)),
    });
    return Promise.race([call, timeout]).finally(() => {
      if (timer !== null) window.clearTimeout(timer);
    });
  }

  function pollingExpired() {
    return !pollDeadlineAt || Date.now() >= pollDeadlineAt;
  }

  function schedulePolling(targetId, token) {
    if (token !== pollToken || targetId !== selectedTargetId || !shouldContinuePolling(currentState, targetId)) return;
    if (pollingExpired()) {
      cancelPolling();
      currentState = { ...currentState, state: 'failed', last_error: 'poll_timeout' };
      renderState(currentState, { ignoreQueue: true });
      announce(t('pollTimeoutError'));
      return;
    }
    const delay = pollingDelay(pollDeadlineAt);
    if (!delay) return;
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      void pollState(targetId, token);
    }, delay);
  }

  function startPolling(targetId) {
    if (!targetId) return;
    if (pollTimer !== null && pollTargetId === targetId) return;
    cancelPolling();
    pollTargetId = targetId;
    pollStartedAt = Date.now();
    pollDeadlineAt = pollStartedAt + POLL_MAX_MS;
    schedulePolling(targetId, pollToken);
  }

  // ожидание: фоновая задача
  async function pollState(targetId, token) {
    if (token !== pollToken || targetId !== selectedTargetId || pollingExpired()) {
      if (token === pollToken && targetId === selectedTargetId) {
        cancelPolling();
        currentState = { ...currentState, state: 'failed', last_error: 'poll_timeout' };
        renderState(currentState, { ignoreQueue: true });
        announce(t('pollTimeoutError'));
      }
      return;
    }
    const state = await refreshState({
      targetId,
      announceLoading: false,
      fromPolling: true,
      requestToken: stateRequestToken,
      deadlineAt: pollDeadlineAt,
    });
    if (token !== pollToken || targetId !== selectedTargetId) return;
    if (shouldContinuePolling(state, targetId)) schedulePolling(targetId, token);
    else cancelPolling();
  }

  async function saveConfiguration(event) {
    event.preventDefault();
    const settings = readTargetForm();
    if (!settings) return;
    const button = el('save-target');
    button.disabled = true;
    button.textContent = t('savingTarget');
    setRunBusy(true);
    try {
      const answer = await bridge.run(EXPERT_RUN, { method: 'configure', ...settings });
      if (!answer.ok) throw new Error(answer.error);
      currentState = normalizeStatePayload(answer.data);
      let target = selectedTarget();
      if (!target && settings.target_id) target = normalizeTarget(settings);
      if (!target) target = normalizeTarget({ ...settings, target_id: asText(settings.target_id, '') });
      if (target) {
        currentState.targets = mergeTargets(currentState.targets, [target]);
        selectedTargetId = target.target_id;
        currentState.selected_target = target;
        savedTargetId = target.target_id;
      }
      fillTargetForm(target || settings);
      renderState(currentState, { preserveInputs: true });
      announce(t('targetReady'));
    } catch (error) {
      const node = el('settings-error');
      node.textContent = friendlyError(error && error.message, true);
      node.hidden = false;
      announce(node.textContent);
    } finally {
      setRunBusy(false);
      button.disabled = false;
      button.textContent = t('saveTarget');
    }
  }

  function runControlState(payload) {
    if (!isObject(payload)) return null;
    if (payload.state === 'duplicate' || payload.state === 'running' || payload.state === 'queued') return payload.state;
    return queuePayloadState(payload);
  }

  async function runAudit() {
    const target = selectedTarget();
    if (!runAllowed() || !target) {
      announce(!selectedTargetId || !target ? t('selectTargetHint') : target.ownership_confirmed !== true ? t('ownershipRequired') : t('saveTargetFirst'));
      return;
    }
    const targetId = selectedTargetId;
    const mode = MODES.includes(target.mode) ? target.mode : 'full_audit';
    cancelPolling();
    setRunBusy(true);
    currentState = { ...currentState, state: 'running', selected_target: target };
    renderState(currentState, { preserveInputs: true });
    try {
      const answer = await bridge.run(EXPERT_RUN, { method: 'run', target_id: targetId, mode, trigger: 'manual' });
      if (!answer.ok) throw new Error(answer.error);
      const controlState = runControlState(answer.data);
      currentState = normalizeStatePayload(answer.data);
      if (controlState) {
        currentState = { ...currentState, state: controlState };
        renderState(currentState);
        startPolling(targetId);
        return;
      }
      renderState(currentState);
      if (isWorkState(currentState.state)) startPolling(targetId);
    } catch (error) {
      currentState = { ...currentState, state: 'failed', last_error: error && error.message };
      renderState(currentState);
    } finally {
      setRunBusy(false);
    }
  }

  async function refreshState({
    targetId = selectedTargetId,
    announceLoading = true,
    fromPolling = false,
    requestToken = stateRequestToken,
    deadlineAt = Date.now() + POLL_MAX_MS,
  } = {}) {
    if (!bridge || !bridge.embedded) {
      renderState(currentState, { announce: false, preserveInputs: true });
      announce(t('standaloneStatus'));
      return currentState;
    }
    setRunBusy(true);
    if (announceLoading) announce(t('loadingState'));
    let followupTargetId = null;
    let resultState = currentState;
    try {
      const answer = await stateRequest(targetId, deadlineAt);
      if (!answer.ok) throw new Error(answer.error);
      if (requestToken !== stateRequestToken || (targetId && targetId !== selectedTargetId)) return currentState;
      const incomingTargets = extractTargets(answer.data);
      currentState = normalizeStatePayload(answer.data);
      const selected = targetId
        ? currentState.targets.find(target => target.target_id === targetId)
        : selectBootstrapTarget(answer.data, selectedTargetId) || selectedTarget() || currentState.targets[0];
      if (selected) {
        selectedTargetId = selected.target_id;
        currentState.selected_target = selected;
        if (savedTargetId === null || savedTargetId === selectedTargetId) savedTargetId = selectedTargetId;
        if (!targetId && incomingTargets.length) followupTargetId = selected.target_id;
      }
      renderState(currentState, { preserveInputs: true });
      const activeTarget = targetId || selectedTargetId;
      if (followupTargetId) {
        // Targetless bootstrap must hydrate the selected target before polling it.
      } else if (activeTarget && shouldContinuePolling(currentState, activeTarget)) {
        if (!fromPolling) startPolling(activeTarget);
      } else if (!fromPolling && !followupTargetId) {
        cancelPolling();
      }
      resultState = currentState;
    } catch (error) {
      if (requestToken !== stateRequestToken || (targetId && targetId !== selectedTargetId)) return currentState;
      currentState = { ...currentState, state: 'failed', last_error: error && error.message };
      renderState(currentState, { ignoreQueue: error && error.message === 'poll_timeout' });
      if (error && error.message === 'poll_timeout'
          || fromPolling && !shouldContinuePolling(currentState, targetId)) cancelPolling();
    } finally {
      setRunBusy(false);
    }
    if (followupTargetId && requestToken === stateRequestToken && selectedTargetId === followupTargetId) {
      return refreshState({
        targetId: followupTargetId,
        announceLoading: false,
        fromPolling: false,
        requestToken,
        deadlineAt,
      });
    }
    return resultState;
  }

  async function loadState() {
    return refreshState({ targetId: null });
  }

  function selectTarget(targetId) {
    if (!targetId || targetId === selectedTargetId) return;
    cancelPolling();
    selectedTargetId = targetId;
    savedTargetId = targetId;
    const target = currentState.targets.find(item => item.target_id === targetId) || null;
    currentState = {
      ...currentState,
      selected_target: target,
      state: targetState(target),
      last_report: target && target.last_report ? target.last_report : null,
    };
    fillTargetForm(target || {});
    renderState(currentState, { preserveInputs: true });
    void refreshState({ targetId, announceLoading: true });
  }

  function startNewTarget() {
    cancelPolling();
    selectedTargetId = null;
    savedTargetId = null;
    currentState = { ...currentState, selected_target: null, state: 'empty', last_report: null };
    fillTargetForm({
      profile: 'service_b2b',
      language: 'ru',
      region: 'GLOBAL',
      site_type: 'website',
      daily_run_time: '09:00',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      mode: 'full_audit',
      max_pages: 25,
      ownership_confirmed: false,
    });
    renderState(currentState, { preserveInputs: true });
    announce(t('saveTargetFirst'));
    el('target-name').focus();
  }

  function markDirty() {
    if (suppressDirty) return;
    if (savedTargetId === selectedTargetId) savedTargetId = null;
    syncRunButton();
  }

  async function boot() {
    if (await healStaleCache()) return;
    const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (localTimezone) el('timezone').value = localTimezone;
    bridge = new ExtellaBridge({ allowedExperts: [EXPERT_RUN, EXPERT_STATE], timeoutMs: 240000 });
    bridge.subscribeHost(handleHostMessage);
    el('target-form').addEventListener('submit', saveConfiguration);
    el('run-audit').addEventListener('click', runAudit);
    el('new-target').addEventListener('click', startNewTarget);
    ['target-name', 'site-url', 'profile', 'language', 'region', 'site-type', 'business-goal',
      'daily-time', 'timezone', 'mode', 'max-pages', 'ownership-confirmed'].forEach(id => {
      const node = el(id);
      node.addEventListener('input', markDirty);
      node.addEventListener('change', markDirty);
    });
    window.addEventListener('beforeunload', cancelPolling);
    window.addEventListener('pagehide', cancelPolling);
    applyLanguage('ru');
    await loadState();
    window.__SEO_PANEL_TEST__ = {
      PANEL_VERSION,
      runAudit,
      refreshState,
      renderState,
      normalizeStatePayload,
      selectBootstrapTarget,
      buildReportModel,
      buildCoverageModel,
      cancelPolling,
      selectTarget,
      queueViewModel,
      shouldContinuePolling,
      stateRequest,
      remainingDeadline,
      pollingDelay,
    };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      buildReportModel,
      buildCoverageModel,
      groupedTasks,
      hydrateReportModel,
      nextRunFrom,
      normalizeStatePayload,
      normalizeTarget,
      selectBootstrapTarget,
      queuePayloadState,
      queueViewModel,
      refreshState,
      remainingDeadline,
      pollingDelay,
      runControlState,
      shouldContinuePolling,
      stateRequest,
      validRegion,
      isWorkState,
      ALLOWED_REGIONS,
      severityLabel,
    };
  }
  if (typeof document !== 'undefined' && typeof window !== 'undefined') void boot();
})();
