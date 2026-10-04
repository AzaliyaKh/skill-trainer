# Lifecycle commands

Все команды выполняются из корня проекта. Версия указывается явно.

```sh
make validate
make load-cases

make benchmark VERSION=v0
make aggregate VERSION=v0
make error-analysis VERSION=v0
make optimize VERSION=v0
make optimize-review
make approve
make optimize-apply

make benchmark VERSION=v1
make aggregate VERSION=v1
make regression FROM=v0 TO=v1

make holdout VERSION=v1
make review
make release
```

`benchmark` пишет DEV-запуски в `runs/regression/<skill>/<version>/raw/`.
`aggregate` и `error-analysis` читают только выбранную версию и не запускают модели.
`regression` читает готовые `summary.json` двух версий и не запускает benchmark.

Если regression не пройдена:

```sh
make error-analysis VERSION=v1
make next-version
make optimize VERSION=v1
```

После review и применения v2 цикл продолжается командами `make benchmark VERSION=v2`
и `make aggregate VERSION=v2`, затем `make regression FROM=v1 TO=v2`.

Для совместимости сохранены aliases `dev-benchmark`, `aggregate-dev`,
`version-benchmark`, `aggregate-version` и `optimize-version`. Они делегируют работу
параметризованным целям и не содержат отдельной реализации.

`make holdout VERSION=v4` использует только `skill/versions/<skill>/v4/` и сохраняет
результаты в `runs/holdout/<skill>/v4/`. Без VERSION используется candidate_version из конфигурации.
Версия должна существовать; подмена исходным Skill не допускается.

`make review` создаёт `runs/review/<skill>/<candidate_version>/result.md` с ответами
каждой модели, оценками по критериям, проверками, токенами, временем и стоимостью.
Содержание DOCX извлекается для чтения; оригиналы доступны по ссылкам.
HTML и JSON для экспертного одобрения также сохраняются.

Holdout-датасет должен уже существовать по `dataset.holdout_path`: команда не создаёт кейсы.
Для генерации DOCX задайте в holdout `case.yaml`:

```yaml
prompt: Подготовь документ на русском языке и сохрани в outputs/technical-specification.docx.
execution:
  mode: agent
  output_path: technical-specification.docx
expected_outputs:
  - path: technical-specification.docx
    type: docx
    required: true
checks:
  - type: docx_valid
    path: technical-specification.docx
    severity: critical
  - type: contains
    path: technical-specification.docx
    text: Назначение
```

`VERSION=v4` — конкретная версия; `v*` не раскрывается как выбор последней версии.
