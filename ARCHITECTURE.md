# Архитектура проекта

Срез текущего кода на 24 сентября 2026 года. Основание: модули
`src/benchmark/`, Makefile, конфигурация, тесты и требования [AGENTS.md](AGENTS.md).
Это описание реализации, а не план рефакторинга. Исходники не изменялись.

## 1. Структура и ответственность

```text
.
├── AGENTS.md                         требования к архитектуре и этапам
├── TASK.md                           текущая задача сопровождения
├── README.md                         команды, ограничения, форматы review
├── ARCHITECTURE.md                   устройство текущей реализации
├── Makefile                          точки входа через .venv и порядок DEV-стадий
├── requirements.txt                  зависимости окружения
├── .gitignore                        исключения для локальных/генерируемых файлов
├── .env                              локальное окружение для provider
├── config/benchmark.yaml             модели, пути, judge, gates, язык, версии
├── skill/requirements-analysis/
│   └── SKILL.md                      рабочий Skill анализа требований
├── dataset/example/
│   ├── dev/
│   │   ├── case-001/
│   │   └── case-002/
│   └── holdout/
│       └── case-101/
│           # В каждом из трёх кейсов:
│           # case.yaml              задача, исполнение, checks, rubric
│           # inputs/requirements.md разрешённые входные данные
│           # reference/answer.md    эталон для judge и эксперта
├── src/benchmark/
│   ├── __init__.py                   пустой маркер пакета
│   ├── case.py                       контракты Case/Rubric, загрузка и валидация
│   ├── provider.py                   OpenRouter, Codex CLI, FakeProvider
│   ├── executor.py                   text/agent, подготовка запросов
│   ├── runner.py                     workspace и жизненный цикл одного прогона
│   ├── checks.py                     реестр checks и валидация самого Skill
│   ├── evaluator.py                  чтение артефактов, rubric, вызов judge
│   ├── benchmark.py                  DEV, общая оценка dataset и holdout
│   ├── aggregate.py                  статистика, полнота результатов, gates
│   ├── error_analysis.py             анализ готовых оценок и ошибок без LLM
│   ├── optimizer.py                  кандидат Skill, review и применение
│   ├── regression.py                 сравнение предыдущей и новой версии
│   ├── review.py                     HTML/index, feedback и проверка одобрения
│   ├── release.py                    проверка доказательств и каталог релиза
│   └── storage.py                    JSON, хеши, конфигурация, пути, очистка
└── tests/
    ├── test_pipeline.py              исполнение, checks, judge, providers, очистка
    └── test_lifecycle.py             цикл версий, Make, review и release
```

Содержимое `.env` для анализа не читалось. Генерируемые каталоги `runs/`,
`skill_versions/` и `releases/` описаны ниже как контракты выходных данных;
наличие пути в контракте не означает успешное выполнение соответствующего этапа.
Отдельных `holdout.py`, `validation.py` и `cleanup.py` в текущем пакете нет.

## 2. Деревья зависимостей модулей

В деревьях показаны **прямые внутренние импорты**, включая импорты внутри
функций. Дочерний модуль раскрывается в собственном дереве ниже: это исключает
многократное повторение ветвей и бесконечное раскрытие циклов. Внешние пакеты и
стандартная библиотека не показаны. Передача объекта provider и вызов его методов
не являются импортом `provider.py` из executor/evaluator.

```text
checks.py — make validate
├── case.py
└── storage.py

case.py — make load-cases / validate-dataset
├── checks.py                         отложенно в validate_case_data()
└── storage.py                        отложенно в main()

benchmark.py — make dev-benchmark / holdout
├── case.py
├── runner.py
├── executor.py                       отложенная проверка реестра режимов
├── provider.py
├── checks.py
├── aggregate.py
├── error_analysis.py
└── storage.py

aggregate.py — make aggregate-dev
└── storage.py

error_analysis.py — make error-analysis
└── storage.py

optimizer.py — make optimize / optimize-review / optimize-apply
├── provider.py
├── checks.py
├── review.py                         отложенные импорты
└── storage.py

regression.py — make regression
├── benchmark.py
└── storage.py

review.py — make review
├── case.py
└── storage.py

release.py — make release
├── review.py
├── checks.py
├── regression.py
├── aggregate.py                      отложенно в release()
└── storage.py

storage.py — make clean / distclean; общие операции
└── checks.py                         только внутри lifecycle_paths()

runner.py — вызывается benchmark
├── case.py
├── checks.py
├── evaluator.py
├── executor.py
└── storage.py

executor.py
└── case.py

evaluator.py
├── case.py
└── executor.py                       TEXT_EXTENSIONS внутри artifact_text()

provider.py
└── case.py                           checked_path() только у FakeProvider
```

`make check` импортирует все модули пакета; `make test`/`smoke` после него запускают
обнаружение обоих тестовых файлов. `setup`/`install` создают окружение и устанавливают
зависимости; `skill-info` вызывает внешний валидатор, внутреннего скрипта у него нет.

## 3. Деревья функций и поток исполнения

Ниже перечислены все явно определённые функции производственных модулей, включая
методы и вложенные функции. `модуль: функция()` — вызов импортированной функции;
`[реестр]` — динамический выбор обработчика. Операции файловой системы и внешних
библиотек описаны словами. Повторное упоминание функции отсылает к её раскрытию.
Общие вызовы `storage: read_json()/write_json()/digest_tree()/digest_json()`
сгруппированы в ветви «артефакты» без повторения каждой точки чтения/записи.

### case.py и checks.py

```text
case.main()
├── load_all_cases()
│   └── load_case()
│       ├── validate_case_data()
│       │   ├── safe_relative()
│       │   ├── checked_path() → safe_relative()
│       │   └── checks: normalize_checks() + HANDLERS
│       └── создание Case, Rubric, RubricCriterion, RubricLevel
└── storage: write_json()

Rubric.total [свойство]                сумма points; используется evaluator

checks.main()
├── validate_skill()
│   ├── storage: identifier(), digest_tree()
│   └── внешняя валидация Skill; для версии — временная копия под именем Skill
└── storage: write_json()

checks.run_checks()                   вызывается runner, а не checks.main()
├── normalize_checks()
├── required expected_outputs → дополнительные file_exists
└── HANDLERS[type](...) [реестр]
    ├── non_empty() / contains() / json_valid() / svg_valid()
    │   └── _text() → _path() → case: checked_path() [если задан path]
    ├── json_schema()
    │   ├── local_refs() → local_refs() [вложенная рекурсивная проверка ссылок]
    │   └── _text()
    └── file_exists() / docx_valid() / pdf_valid() → _path()

register_check(name) → register(handler)
    заполняет HANDLERS при декорировании функций; доступен для расширения
```

Валидация dataset проверяет ссылки на inputs/reference и известность типов checks.
Проверки результатов преобразуют исключения обработчиков в записи с `passed: false`.
Данные эталона в текущий `run_checks()` не передаются.

### provider.py и executor.py

```text
provider.make_provider()
└── PROVIDERS[name](...) [реестр]
    ├── OpenRouterProvider.__init__()  параметры повторов и ключ из окружения
    ├── CodexCLIProvider.__init__()    executable, timeout, путь аутентификации
    └── FakeProvider.__init__()        очередь ответов, файлы и журнал вызовов

OpenRouterProvider.generate()
├── HTTP-запрос и разбор ответа
├── _wait_before_retry()              backoff и Retry-After при временном сбое
└── GenerationResult

CodexCLIProvider.generate()
├── временный home, workspace, команда bwrap + codex exec
├── разбор событий и финального ответа
└── GenerationResult                 transcript, cost=None

FakeProvider.generate()
├── ответ из очереди или переданное исключение
├── case: checked_path()              запись настроенных outputs в agent-режиме
└── GenerationResult

executor.TextExecutor.execute()
├── _build_text_prompt() → _read_text_inputs()
└── provider.generate()               переданный объект, не внутренний импорт

executor.AgentExecutor.execute()
├── проверка supports_agent
├── _build_agent_prompt()
└── provider.generate(workspace=...)

register_executor()                   добавляет исполнителя в EXECUTORS
```

У этих модулей нет `main()`. Импорт `provider.py` вызывает загрузку `.env`.
`TEXT_EXTENSIONS` используется исполнителем, runner и evaluator. Агентский provider
изолирует workspace через Linux/bwrap; проверяемый агент не получает dataset/reference.

### runner.py и evaluator.py

```text
runner.run_and_evaluate()             callback get_provider передаёт benchmark
├── get_provider() → run_case()
│   ├── _prepare_workspace() → case: checked_path()
│   ├── strip_frontmatter()
│   ├── storage: digest_tree()         проверка ресурсов перед копированием Skill
│   ├── executor: _read_text_inputs()  ресурсы Skill для text-режима
│   ├── EXECUTORS[mode].execute()
│   ├── storage: write_json()          usage и необязательный transcript
│   ├── case: checked_path()           путь основного ответа; сохранение файла
│   ├── _collect_created_files()
│   ├── storage: read_json(), write_json()  обновление created_files
│   └── RunResult
├── checks: run_checks() → storage: write_json(checks.json)
├── get_provider()                    фиксированный judge из конфигурации
├── evaluator: evaluate_output() → storage: write_json(evaluation.json)
├── storage: write_json(status.json)
└── при исключении: storage: write_json(error.json), возврат ошибки стадии

evaluator.evaluate_output()
├── _load_reference() → artifact_text()
├── artifact_text(outputs_dir)
│   ├── artifact_text()               рекурсивно для каталога
│   ├── ARTIFACT_READERS[extension]()  пользовательский reader, если установлен
│   └── чтение текста, PDF, XML из DOCX/XLSX
├── _build_system_prompt() → case: Rubric.total [свойство]
├── provider.generate()               переданный объект judge
└── _parse_response() → case: Rubric.total
    └── CriterionScore, EvaluationResult
```

У обоих модулей нет CLI. Язык берётся из кейса, затем конфигурации, затем `ru`.
`run_case()` пишет usage до основного текстового ответа, затем обновляет список
созданных файлов. Checks и оценка идут только после завершения этой функции.
Ошибка judge оставляет уже сохранённые outputs/usage/checks. `artifact_text()`
извлекает содержимое; визуальную точность диаграммы или вёрстки не оценивает.

### benchmark.py

```text
main()
├── storage: load_config()
├── benchmark()                       обычный режим CLI
│   ├── checks: validate_skill()
│   ├── case: load_all_cases()
│   ├── _safe_model_name()
│   ├── storage: digest_tree(), digest_json(), read_json(), write_json()
│   ├── проверка manifest/завершённости при --reuse → возврат без генерации
│   └── цикл model × case × run → runner: run_and_evaluate()
│       └── provider(name) [вложенный callback]
│           └── provider: make_provider() [кеш объектов по имени provider]
└── run_holdout()                      ветка --holdout
    ├── storage: lifecycle_paths()
    ├── проверка непересечения DEV и holdout
    ├── evaluate_dataset()
    │   ├── storage: lifecycle_paths()
    │   ├── checks: validate_skill()
    │   ├── benchmark()
    │   ├── aggregate: aggregate_all()
    │   ├── error_analysis: analyze()
    │   └── storage: write_json()      summary, error_analysis, validation
    └── storage: digest_tree(), digest_json(), write_json(holdout.json)
```

`main()` возвращает ошибку при `complete: false` для DEV или `passed: false` для
holdout. Внутри `benchmark()` ошибка одного прогона не останавливает остальные.
`evaluate_dataset()` используется также regression и вызывает функции стадий напрямую.

### aggregate.py и error_analysis.py

```text
aggregate.main()
├── storage: cli_config()
├── aggregate_all()
│   ├── aggregate_model_runs()
│   │   ├── storage: read_json()       usage, checks, evaluation
│   │   └── _stats()                  общая оценка и каждый критерий
│   ├── apply_gates()
│   └── storage: read_json()          manifest и проверка ожидаемых прогонов
└── storage: write_json(summary.json)

error_analysis.main()
├── storage: cli_config(), read_json(summary.json)
├── проверка summary.complete
├── analyze()
│   └── storage: read_json()          ошибки, checks и пояснения judge
└── storage: write_json(error_analysis.json)
```

`complete` описывает техническую полноту; `passed` — полноту вместе с gates.
CLI агрегации не падает из-за низких оценок при полных данных: они нужны оптимизации.
`analyze()` не обращается к модели и не знает предметную область кейса.

### optimizer.py

```text
main()
├── storage: load_config(), lifecycle_paths()
├── optimize()                        стандартный режим
│   ├── storage: lifecycle_paths(), read_json(), digest_tree()
│   ├── проверки DEV evidence, исходной версии и рабочего Skill
│   ├── checks: validate_skill()      рабочий Skill
│   ├── storage: read_json(proposal_path) ИЛИ
│   │   provider: make_provider() → provider.generate()
│   ├── проверка изменений, копирование ресурсов в кандидата
│   ├── checks: validate_skill()      кандидат перед сохранением версий
│   ├── storage: digest_tree(), write_json(optimization.json)
│   └── review: export_optimization_review()
├── review: export_optimization_review()  --review
└── apply_optimization()              --apply
    ├── storage: lifecycle_paths()
    ├── review: optimization_evidence(), feedback_approved()
    ├── storage: read_json(), digest_tree()  защита от устаревшего одобрения
    ├── checks: validate_skill()
    ├── атомарная замена рабочего SKILL.md
    └── storage: digest_json(), write_json(applied.json)
```

В LLM оптимизатора передаются исходный текст Skill, summary и error_analysis.
Прямого чтения failed outputs для запроса сейчас нет; DEV dataset используется
для проверки хеша, а holdout — только для проверки разделения путей.

### regression.py и release.py

```text
regression.main()
├── storage: cli_config()
└── run_regression()
    ├── storage: lifecycle_paths()
    ├── benchmark: evaluate_dataset()  предыдущая версия на DEV
    ├── benchmark: evaluate_dataset()  кандидат на том же DEV
    ├── compare()
    │   ├── _groups()                 группировка case/model обеих сводок
    │   └── delta()                   вложенная функция разности
    └── storage: digest_tree(), digest_json(), write_json(regression.json)

release.main()
├── storage: cli_config()
└── release()
    ├── storage: lifecycle_paths()
    ├── checks: validate_skill()      повторная проверка кандидата
    ├── storage: read_json(), digest_tree(), digest_json()
    ├── regression: compare()         проверка текущей regression policy
    ├── aggregate: aggregate_all()    текущие gates для DEV кандидата и holdout
    ├── review: validate_feedback()   если политика требует review
    └── копирование кандидата + storage: write_json(release.json)
```

Regression сравнивает две версии Skill, без варианта `without_skill`.
Release сверяет хеши версий и evidence; каталог публикуется локальным переименованием
после проверок. Архив `.skill` и удалённая публикация не реализованы.

### review.py

```text
main() → storage: cli_config() → export_review()
export_review()
├── storage: lifecycle_paths()
├── review_evidence()
│   ├── storage: lifecycle_paths(), read_json(), digest_tree(), digest_json()
│   └── case: load_all_cases()         DEV и holdout для ссылок на эталоны
├── prepare_feedback() → storage: read_json(), write_json()
├── storage: write_json(index.json)
├── page_header()
└── link() [вложенная]                 относительные ссылки в HTML

validate_feedback()
├── review_evidence()
├── storage: lifecycle_paths()
└── feedback_approved() → storage: read_json()

export_optimization_review()
├── storage: lifecycle_paths()
├── optimization_evidence()
│   └── storage: lifecycle_paths(), read_json(), digest_tree(), digest_json()
├── prepare_feedback()
├── page_header()                      HTML: причины, исходный/новый Skill, diff
└── storage: write_json(index.json)
```

Обе формы review используют один контракт feedback. `prepare_feedback()` создаёт
`pending` либо сохраняет существующий feedback с тем же fingerprint.
`feedback_approved()` требует полного одобрения набора идентификаторов; человек
редактирует JSON вручную. Stage 9 показывает ссылки на предыдущие/новые outputs.

### storage.py и тестовые точки входа

```text
storage.main() → clean()               clean/distclean, без lifecycle_paths()
cli_config() → load_config()
lifecycle_paths()
├── checks: validate_skill()
└── identifier()
write_json()                          атомарная запись JSON
read_json()                           чтение JSON
digest_tree()                         хеш имён/содержимого, запрет symlink
digest_json()                         хеш JSON с сортировкой ключей

make test / smoke
├── make check                        синтаксис и импорт всех модулей
└── обнаружение unittest
    ├── test_pipeline.py
    │   ├── PipelineTests.setUp() → test_*(): runner, evaluator, case, checks, aggregate
    │   ├── BoundaryTests.test_*(): provider, извлечение артефактов, checks, пути
    │   └── CleanupTests.setUp() → test_*() → make(): storage CLI через Make
    └── test_lifecycle.py
        └── LifecycleTests.setUp() → test_*()
            ├── providers(): подготовка FakeProvider
            ├── prepare_candidate(): benchmark → aggregate → analyze → optimize
            ├── approve_optimization(): синтетический feedback внутри теста
            ├── cli_config(): конфигурация для проверок Make
            └── apply_optimization, regression, holdout, review, release
```

`test_*` здесь объединены по назначению; производственные функции перечислены
индивидуально. Всего определено 33 тестовых метода. В этой задаче тесты не запускались,
чтобы не создавать дополнительные артефакты и кеши.

## 4. Этапы и движение данных

Обозначения: `D = results_dir`, `R = lifecycle.runs_dir`, `S = имя Skill`,
`P/V = previous_version/candidate_version`, `SV = lifecycle.versions_dir`.
В текущем YAML: `D=runs/dev/requirements-analysis`, `R=runs`,
`S=requirements-analysis`, `P=v0`, `V=v1`, `SV=skill_versions`.

| Этап / Make | Входной модуль | Входные файлы | Выходные файлы → потребитель |
|---|---|---|---|
| 1. Baseline / `validate` | `checks.main` | рабочий `SKILL.md`, ресурсы | `runs/validation/S/validation.json`; Skill → DEV. Последующие стадии валидируют Skill заново, этот JSON напрямую не читают |
| 2. Dataset / `load-cases` | `case.main` | `case.yaml`, inputs, reference | `runs/dataset_validation/dev.json`; dataset → benchmark. Отчёт загрузки не является входным шлюзом benchmark |
| 3. DEV / `dev-benchmark` | `benchmark.main` | Skill, DEV dataset, config | `D/manifest.json`, прогоны, `D/benchmark.json` → агрегация и проверки оптимизатора |
| 4. Aggregation / `aggregate-dev` | `aggregate.main` | `D/**/{usage,checks,evaluation,status,error}.json`, manifest, config | `D/summary.json` → анализ и оптимизатор |
| 5. Error analysis / `error-analysis` | `error_analysis.main` | summary, checks, evaluation, error, thresholds | `D/error_analysis.json` → оптимизатор |
| 6. Improvement / `optimize` | `optimizer.main` | Skill, benchmark, summary, analysis, config; опционально proposal JSON | `SV/S/{P,V}/`, `R/optimization/S/V/{optimization,index,feedback}.json`, `index.html` → проверка предложения и regression |
| 6а. Применение / `optimize-apply` | `optimizer.main --apply` | кандидат, optimization.json, одобренный feedback | рабочий `SKILL.md`, `applied.json` → следующий рабочий цикл; regression использует снимки SV |
| 7. Regression / `regression` | `regression.main` | `SV/S/{P,V}/`, DEV, одинаковый config | `R/regression/S/{P,V}/raw/`, summary/analysis/validation в каждом каталоге версии; `R/regression/S/regression.json` → review/release |
| 8. Holdout / `holdout` | `benchmark.main --holdout` | `SV/S/V/`, holdout dataset, config | `R/holdout/S/V/raw/`, `summary.json`, `error_analysis.json`, `validation.json`, `holdout.json` → review/release |
| 9. Expert review / `review` | `review.main` | отчёты regression/holdout, manifest, outputs, checks, evaluation, reference, версии Skill | `R/review/S/V/{index,feedback}.json`, `index.html`; заполненный человеком feedback → release |
| 10. Release / `release` | `release.main` | кандидат, обе версии/evidence regression, holdout evidence, feedback по политике, config | `releases/S/V/` с Skill, ресурсами и `release.json` → локальный потребитель Skill |

`--output` и Make-переменные позволяют менять пути отчётов валидации; таблица
показывает стандартные пути. Конфигурация читается всеми основными стадиями 3–10.

```text
Baseline + Golden Dataset
    ↓
DEV benchmark → Aggregation → Error analysis → Skill improvement
                                                  ↓
                                     review предложения / применение
                                                  ↓
                                      Regression двух версий
                                                  ↓
                                          Holdout кандидата
                                                  ↓
                                           Expert review
                                                  ↓
                                               Release
```

Это порядок жизненного цикла, а не полная цепочка зависимостей Make. Автоматические
prerequisites заданы только для `optimize → error-analysis → aggregate-dev →
dev-benchmark` (Make выполняет их в обратном порядке). `work` сначала выполняет
validate/load-cases/test, затем optimize. Regression, holdout, review и release
запускаются отдельно; holdout не читает regression.json и не требует его наличия.

```text
D/<case-id>/<safe-model>/run-XX/
├── workspace/
│   ├── inputs/                       только разрешённые данные кейса
│   ├── skill/                        копия Skill и ресурсов
│   └── outputs/                      фактические артефакты
├── usage.json                        метрики генерации и created_files
├── checks.json                       детерминированные результаты
├── evaluation.json                   динамические критерии rubric
├── status.json                       complete при полном успехе
├── error.json                        вместо полного успеха при исключении
├── transcript.json                   если provider вернул события
└── response.md                       ответ агента, если основной файл уже создан
```

У regression/holdout такой же формат прогонов внутри `raw/`. Эталоны остаются
в dataset и читаются evaluator/review, а не тестируемой моделью. Holdout evidence
не передаётся оптимизатору. `usage.json` учитывает генерацию; стоимость judge и
оптимизации в этот файл не включается.

## 5. Архитектурные вопросы — без исправлений

1. **Циклические зависимости.** `case → checks → case` и `checks → storage → checks`
   существуют на уровне функций; также получается `case → storage → checks → case`.
   Отложенные импорты предотвращают обычную ошибку инициализации, но усложняют
   навигацию. Рекурсивного вызова функций из этих циклов не следует:
   `validate_skill()` использует хеш/идентификатор, а не `lifecycle_paths()`.
2. **Смешение ответственности storage.** `lifecycle_paths()` не только собирает пути,
   но валидирует baseline, читает Skill и вызывает checks. Поэтому даже получение
   пути review/release зависит от рабочего Skill и валидатора. Очистка, конфигурация,
   хеширование и бизнес-пути находятся в одном файле.
3. **Повторные проверки Skill.** `evaluate_dataset()` вызывает `lifecycle_paths()`,
   `validate_skill()` и затем `benchmark()`, который снова валидирует Skill.
   Аналогичные повторы есть в review/optimizer/release. Это реальные пересечения
   ответственности; часть повторов на границе release нужна для проверки свежести.
4. **Разные правила остановки.** DEV CLI блокирует зависимые стадии при неполном
   benchmark. `evaluate_dataset()` игнорирует возвращённый `complete` и всё равно
   агрегирует/анализирует результаты. `run_regression()` затем может запускать
   вторую версию после неудачи первой. Итоговые gates могут отклонить результат,
   но порядок отличается от строгой остановки DEV.
5. **Неявные побочные действия Make.** `make aggregate-dev` через prerequisite может
   запустить модели, хотя `aggregate.py` сам не вызывает LLM. Для повторного
   использования benchmark требуется исходный Skill/dataset. Изменение aggregation
   gates меняет `settings_digest`, поэтому даже перерасчёт порогов через этот Make
   target требует нового каталога вместо простого использования прежних прогонов.
6. **Несколько определений полноты.** `benchmark --reuse` проверяет значение
   `status.json`, наличие файлов и отсутствие error. Агрегация проверяет наличие
   status по manifest, но не его значение; `aggregate_model_runs()` сам status не
   проверяет. Условия расходятся при повреждённых или вручную изменённых артефактах.
7. **Скрытая связь слоёв исполнения.** Runner импортирует приватный
   `executor._read_text_inputs`; evaluator зависит от списка расширений executor.
   Provider объединяет HTTP, запуск/изоляцию CLI и тестовый fake. Эти решения
   работают, но границы шире, чем предполагают названия файлов.
8. **Конфигурация и метрики.** `load_config()` проверяет только тип mapping; остальная
   валидация распределена по стадиям. Список очистки повторяется в Makefile и
   `storage.DEFAULT_ARTIFACTS`. `models[].name` не используется оркестратором:
   каталоги определяет `model`. Usage judge/optimizer теряется после вызова provider,
   поэтому сводная стоимость не равна стоимости всего жизненного цикла.
9. **Ограничения review и языков.** UI реализует выбор русского или английского,
   хотя поле языка допускает другие значения. Optimizer не добавляет явного
   требования output_language в свой запрос. Review использует хеши отчётов,
   а актуальность raw evidence дополнительно проверяет release; сам HTML не является
   доказательством неизменности файлов. Одобрение предложения требуется для apply,
   но regression/holdout работают со снимком кандидата независимо от apply.
10. **Неиспользуемые и неясные файлы.** Неиспользуемых производственных Python-модулей
    в `src/benchmark/` не обнаружено: они доступны из CLI, внутренних вызовов или
    тестов. Пустой `__init__.py` имеет понятную роль. В корне присутствуют
    `gpt_token.txt` и `openrouter_token`, но текущий код не читает файлы под этими
    именами; их роль в рабочем процессе не определена. Содержимое не исследовалось.
    Старые вкладки IDE `agent.md`/корневого `SKILL.md` не подтверждают наличие файлов:
    в текущем корне их нет. Ненужных внешних пакетов по используемым возможностям
    не выявлено; зависимости частично загружаются даже для операций без моделей
    через цепочку `release → regression → benchmark → provider`.

Эти наблюдения не сопровождаются изменениями кода. Отчёт составлен чтением исходников
и сверкой определений/внутренних импортов через AST, без запуска pipeline или API.
