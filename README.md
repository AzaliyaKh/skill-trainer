# Фреймворк разработки Agent Skills

Архитектура, контракты и требования к этапам определены в [AGENTS.md](AGENTS.md).
Здесь описаны команды запуска и особенности реализации. Нужен Python 3.10+.
Команды выполняются из корня проекта через `.venv`.

```sh
make install
make validate load-cases test
```

`make install` устанавливает `requirements.txt`, включая официальный `skills-ref`.
Для OpenRouter задайте `OPENROUTER_API_KEY` через окружение или локальный `.env`.
Правила `.gitignore` не удаляют ранее отслеживаемые файлы из Git.

`make test` / `make smoke` проверяют весь цикл локально через FakeProvider и подмену
HTTP/CLI. Тестовый релиз и синтетическое экспертное одобрение существуют только
во временном каталоге. PDF/JSON Schema тесты явно пропускаются без `pypdf`/`jsonschema`;
соответствующие рабочие checks при отсутствии зависимостей возвращают ошибку.

## Команды и артефакты

Настройки: `config/benchmark.yaml`. Другой файл: `make <target> CONFIG=config/local.yaml`.
В таблице `R` — `lifecycle.runs_dir`, `S` — имя Skill, `P`/`V` — предыдущая/новая версия.

| Цель | Ответственный модуль | Выход |
|---|---|---|
| `validate` | `checks.py` | `runs/validation/S/validation.json` |
| `load-cases` / `validate-dataset` | `case.py` | `runs/dataset_validation/dev.json` |
| `benchmark VERSION=V` | `benchmark.py` → `runner.py` → `executor.py` | `R/regression/S/V/raw/` |
| `aggregate VERSION=V` | `aggregate.py` | `R/regression/S/V/summary.json` |
| `error-analysis VERSION=V` | `error_analysis.py` | `R/regression/S/V/error_analysis.json` |
| `optimize` | `optimizer.py`, `review.py` | версия P, предложенный кандидат в `R/optimization/S/V/candidate/`, optimization.json, HTML, feedback |
| `optimize-review` | `review.py` через CLI optimizer | повторный export предложения |
| `approve` | `review.py` через CLI optimizer | подтверждённый `R/optimization/S/V/feedback.json` |
| `optimize-apply` | `optimizer.py` | неизменяемая версия V, рабочий `SKILL.md`, `R/optimization/S/V/applied.json` |
| `regression FROM=P TO=V` | `regression.py` | сравнение готовых summary P/V, `R/regression/S/P-to-V/regression.json` |
| `next-version` | `storage.py` | обновляет в config пару lifecycle `vN → vN+1` |
| `holdout` | `benchmark.py`, `aggregate.py`, `error_analysis.py` | `R/holdout/S/V/raw/`, summary, анализ, validation, `holdout.json` |
| `review` | `review.py` | `R/review/S/V/index.html`, `index.json`, `feedback.json` |
| `release` | `release.py` | `releases/S/V/` с ресурсами и `release.json` |

`storage.py` содержит общие операции с артефактами: атомарную запись JSON, хеши,
пути этапов, чтение конфигурации и очистку. Он не выполняет модели или проверки качества.

Полный параметризованный цикл также приведён в [USAGE.md](USAGE.md):

```sh
make validate load-cases test
make benchmark VERSION=v0
make aggregate VERSION=v0
make error-analysis VERSION=v0
make optimize VERSION=v0
# Проверьте runs/optimization/requirements-analysis/v1/index.html.
make approve
make optimize-apply
make benchmark VERSION=v1
make aggregate VERSION=v1
make regression FROM=v0 TO=v1
make holdout
make review
# Проверьте runs/review/requirements-analysis/v1/index.html и заполните feedback.json.
make release
```

`make work` выполняет проверки и явную последовательность стадий для текущей previous
версии до создания optimization proposal. Только `benchmark`, optimization и holdout
могут обращаться к моделям. `aggregate`, `error-analysis` и `regression` читают уже
готовые артефакты и не имеют скрытых benchmark-зависимостей.

После одобрения кандидата `make benchmark VERSION=v1` запускает модели только для
`skill_versions/S/v1`; `make aggregate VERSION=v1` агрегирует эти результаты.
`make regression FROM=v0 TO=v1` читает готовые `summary.json` обеих версий.

Если regression не прошёл, задайте в конфигурации `previous_version: v1` и новую
`candidate_version` командой `make next-version`, затем выполните
`make error-analysis VERSION=v1`, а затем `make optimize VERSION=v1`.

`make next-version` разрешён после regression текущей пары и требует существующую
версию-кандидат. Например, для `v0/v1` он сохранит в `CONFIG` значения
`previous_version: v1` и `candidate_version: v2`, не переписывая остальные настройки.

Существующие результаты и версии не перезаписываются. Для нового эксперимента
задайте новый `results_dir`; для следующего цикла — новые версии и `lifecycle.runs_dir`.
Ошибки сохраняются в `error.json`, остальные запуски продолжаются. Завершённые выходы,
usage и checks остаются на диске при ошибке судьи. Полностью завершённый DEV benchmark
повторно используется при совпадении Skill, dataset и настроек. OpenRouter повторяет временные ошибки с задержкой,
учитывая `Retry-After`, но доступность модели после исчерпания повторов не гарантируется.

Benchmark выполняется в две фазы. Сначала для всех моделей, кейсов и повторов выполняются
генерация и deterministic checks; успешные запуски получают статус
`ready_for_evaluation`. Только после окончания всей первой фазы фиксированный judge
оценивает сохранённые результаты и переводит их в `complete`. Ошибка одной генерации
не мешает остальным генерациям, а ошибка judge не удаляет outputs, usage или checks.
При повторном `make benchmark VERSION=v0` (использует `--reuse`) ошибки стадии
evaluation возобновляются только с judge. После ошибки generation повторяется только
неудачный прогон в чистом workspace, затем выполняются checks и оценивание.
Предыдущая попытка целиком сохраняется рядом с каталогом результатов:
`raw-failed-attempts/<case-id>/<model>/run-01/attempt-01/`.
Завершённые прогоны повторно не вызываются. При новом сбое команда возвращает ошибку;
её можно запустить снова. Ошибки стадии checks автоматически не возобновляются.

Технический сбой останавливает зависимые стадии. В `summary.json` поле `complete`
означает полноту результатов, а `passed` — прохождение quality gates. Полные результаты
с низкими оценками поступают в анализ ошибок и оптимизацию; проваленные gates
сохраняются и продолжают блокировать выпуск. `error_analysis.json` создаёт стадия
`error-analysis` из реальных результатов, до запуска оптимизатора.

## Примеры и расширение

Демонстрационные данные: `dataset/example/dev/` и `dataset/example/holdout/`.
В каждом кейсе отдельно хранятся `case.yaml`, `inputs/` и `reference/`. Это ранее
согласованное размещение примеров; для рабочего dataset можно указать `dataset/dev`
и `dataset/holdout` в конфигурации. Новый Skill по умолчанию — `requirements-analysis`.

Для валидации другого Skill: `make validate SKILL=skill/name`.
Для holdout: `make load-cases DATASET=dataset/example/holdout DATASET_REPORT=runs/dataset_validation/holdout.json`.

- `execution.mode: text` получает текстовые входы и текстовые `skill/references/`,
  сохраняет один артефакт в `execution.output_path` (по умолчанию `answer.md`).
- `execution.mode: agent` использует агентский provider для бинарных входов,
  инструментов и нескольких выходных файлов. Обычный OpenRouter text-provider
  для этого режима не подходит. Ресурсы Skill доступны в `workspace/skill/`.
- Язык выбирается из `case.output_language`, затем `config.output_language`, затем `ru`.
- Новые проверки регистрируются через `register_check`, исполнители — через
  `register_executor`; предметные правила остаются в кейсах и Skill.

Checks: `non_empty`, `contains` (поле `text`), `file_exists`, `json_valid`,
`json_schema` (inline `schema`, только локальные `$ref`), `svg_valid`, `docx_valid`,
`pdf_valid`. Поддержаны `name`, `category`, `severity` и необязательный `path`.
Старый формат `checks.required_sections` сохранён; дополнительные checks — в `items`.
`expected_outputs` проверяет наличие required-файлов, а не правильность их формата.

Evaluator читает реальные текстовые/SVG/JSON-выходы, XML-содержимое DOCX/XLSX,
извлекаемый текст PDF. Визуальное качество вёрстки этим не оценивается. Для сканов,
изображений и других форматов нужен reader в `ARTIFACT_READERS` либо экспертная
проверка; без reader возникает явная ошибка. `judge.max_artifact_chars` ограничивает
объём без тихого усечения. Имена и диапазоны критериев берутся из rubric кейса.

Агрегация учитывает незавершённые запуски; неизвестный usage/cost остаётся `null`.
Неполные прогоны и critical failures блокируют успех. Gates задаются в YAML.
`confidence_level` управляет `ci_low/ci_high`; `ci95_low/ci95_high` всегда относятся
к 95%. Используется нормальная аппроксимация; для одного наблюдения CI равен `null`.

## Review и применение

Optimization использует только DEV evidence, соответствующее предыдущему Skill и
dataset. Ресурсы сохраняются; кандидат проходит `skills-ref`. HTML показывает причины,
полный исходный/новый Skill и diff. После проверки всех изменений `make approve`
проверяет соответствие feedback текущему evidence, устанавливает `approved: true` для
каждого `change_id` и переводит `status` в `complete`. Существующие комментарии и
дополнительные поля feedback сохраняются. Команда не запускается автоматически.

До одобрения кандидат хранится в `runs/optimization/S/V/candidate/`; каталог
`skill_versions/S/V` создаётся командой `make optimize-apply`. Она атомарно обновляет рабочий файл из `skill.path` либо
`optimization.apply_path`. Нужно одобрить весь кандидат; частичное, отрицательное
или устаревшее одобрение не применяется. Изменения рабочих файлов после создания
предложения блокируют запись. `make optimize-review` сохраняет заполненный feedback.
При `skill.path: skill_versions/S/v1/SKILL.md` задайте `optimization.apply_path` на
рабочий `skill/S/SKILL.md`, поскольку снимки версий неизменяемы.

Без LLM можно задать `optimization.proposal_path` на JSON с полным `skill_md` и
непустым `changes: [{"problem": "...", "change": "...", "reason": "..."}]`.

Stage 9 аналогично создаёт pending feedback для каждого запуска DEV/holdout. Эксперт
сохраняет идентификаторы, заполняет `approved`/`feedback`, ставит `status: complete`.
Release сверяет хеши кандидата и результатов, текущие gates и полное одобрение.
`release.require_expert_review: false` явно снимает последнее требование; отчёт тогда
содержит `expert_review_passed: null`, а не фиктивное одобрение.

`make review` формирует одну сравнительную HTML-страницу и автоматически открывает
`index.html` в активном окне VS Code через CLI и встроенный Simple Browser. Если VS Code
CLI недоступен, команда сохраняет страницу и печатает путь для ручного открытия.
Страница сгруппирована по split и кейсу: сначала показаны формулировка задачи, входные
файлы и эталон, затем рядом расположены результаты всех моделей и повторов. Для каждого
результата указаны модель, provider, доступные токены/стоимость/время и режим исполнения;
полное содержимое текстовых результатов, SVG и изображений показано непосредственно,
PDF встроен для просмотра, остальные форматы доступны по ссылке на исходный артефакт.
Checks, оценка judge и результат предыдущей версии раскрываются рядом с ответом.
Файлы генерации не копируются: HTML читает существующие данные из каталогов regression
и holdout, а `index.json` хранит только пути и metadata. Формат и порядок заполнения
`feedback.json` не изменились.

## Codex и очистка

Агентский режим требует Linux, работающий `bwrap` и Codex CLI с runtime под `/usr`
(включая `/usr/local`). Используются `codex exec --ephemeral --skip-git-repo-check
--sandbox workspace-write --json`, отдельный home и mount-изоляция. Репозиторий,
dataset, эталоны и пользовательские config/MCP не монтируются; inputs и Skill доступны
для чтения. Небезопасного fallback нет. Аутентификация — `~/.codex/auth.json`,
`providers.codex.auth_file` либо `CODEX_API_KEY`/`OPENAI_API_KEY`. `temperature` и
`max_tokens` относятся к текстовому API и в CLI не передаются. Стоимость остаётся `null`.

`make clean` удаляет `runs/`, `skill_versions/`, `releases/`, сборки и кеши,
сохраняя `.venv`, исходники, рабочие Skills, dataset и `.env`.
`make distclean` дополнительно удаляет окружение. Обе команды работают без `.venv`.
Для иных корней задайте полный список `CLEAN_DIRS`, например
`make clean CLEAN_DIRS="runs skill_versions releases custom-output"`.
Пути вне проекта и пересечения с исходниками отклоняются.
