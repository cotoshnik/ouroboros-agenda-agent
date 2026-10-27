# agenda_briefing_agent — пакет по п. 9 гайда Ouroboros (Sber AI Hack)

Автономный ИИ-агент подготовки аналитических справок и презентаций
(проект из `PP AI HACK.docx`). Агент оперирует картой из **67 показателей**
(49 базовых + 18 динамических) из `Показатели_для_подготовки_материалов.xlsx`
и работает **только с фактически поступившими значениями** — новости, RSS
и веб-сигналы не используются.

## Закрытие пункта 9 гайда

| Подпункт гайда | Что сделано | Где |
|---|---|---|
| 9.1 Создание SKILL.md | Манифест навыка: AS IS с time-per-step, TO BE-пайплайн, политика автономии, контракт результата | `SKILL.md` |
| 9.2 Настройка MCP-инструментов | Рабочий MCP-сервер (streamable_http, порт 9999), 12 инструментов, экспорт DOCX + PPTX по шаблону, конфиг для Settings → Advanced → MCP | `mcp/server.py`, `mcp/docx_export.py`, `mcp/pptx_export.py`, `mcp/mcp_servers.json` |
| 9.3 Протокол A2A | Три специализированных агента (collector → analyst → editor) с карточками, контрактом handoff и правилами деградации | `agents/` |
| 9.4 Память и контекст | `identity.md`, `scratchpad.md`, `knowledge/` (карта показателей, источники, шаблон), `dialogue_blocks.json` | `memory/` |
| 9.5 Safety layer | Конституция P1–P7 (иммунная система P3, hardcoded sandbox P4, запрет `/evolve`, запрет фабрикации P2), политика ревью | `safety/` |

## Быстрый старт

```bash
cd ouroboros_agent
pip install -r requirements.txt   # python-pptx — единственная зависимость
python3 mcp/server.py             # MCP-сервер на http://127.0.0.1:9999/mcp
                                  # Windows: py mcp\server.py
```

Проверка готовности: `curl http://127.0.0.1:9999/health`.

В Ouroboros: Settings → Advanced → MCP → вставить содержимое
`mcp/mcp_servers.json`. После подключения агенту доступны 12 инструментов
(см. `INSTRUCTION.md`, раздел 8).

Рабочий диалог (пример):

1. «Запиши КТД за сегодня 99.87%, источник — письмо 07:15» → `save_observation`.
2. «Все ли базовые показатели собраны?» → `check_completeness`.
3. «Выбери актуальные показатели для презентации» →
   `select_presentation_indicators` (отбор с обоснованиями + серверная
   валидация).
4. «Собери справку и презентацию за сегодня, шаблон templates/report_template.pptx»
   → `export_report`: создаёт и валидирует `data/report_draft_<дата>.docx`
   и `data/report_draft_<дата>.pptx`. `status: success` — только когда
   оба файла валидны.

PPTX-шаблон передаётся аргументом `template_path` или переменной
окружения `AGENDA_PPTX_TEMPLATE`. Без шаблона PPTX-этап возвращает
`template_required` — собственный дизайн не создаётся. Анализ шаблона
перед экспортом: `inspect_pptx_template`.

## Структура

```
ouroboros_agent/
├── SKILL.md                      # 9.1: манифест навыка (AS IS / TO BE / контракт результата)
├── INSTRUCTION.md                # подробная инструкция (запуск, инструменты, runbook)
├── requirements.txt              # python-pptx (закреплённая версия)
├── mcp/
│   ├── server.py                 # 9.2: MCP-сервер, 12 инструментов (stdlib + python-pptx)
│   ├── docx_export.py            # экспорт справки в Word .docx (stdlib)
│   ├── pptx_export.py            # презентация по шаблону пользователя (python-pptx)
│   └── mcp_servers.json          # конфиг для Settings → Advanced → MCP
├── agents/                       # 9.3: A2A
│   ├── collector.agent.json      #   приём фактических показателей (auto)
│   ├── analyst.agent.json        #   полнота, HOT_RULES, отбор для презентации
│   ├── editor.agent.json         #   DOCX + PPTX, human-in-the-loop
│   └── README.md                 #   схема и контракт handoff
├── memory/                       # 9.4: память и контекст
│   ├── identity.md               #   самоописание агента
│   ├── scratchpad.md             #   рабочая память (авто-очистка)
│   ├── dialogue_blocks.json      #   консолидированная история
│   └── knowledge/
│       ├── indicators.json       #   67 показателей из Excel (base/dynamic + критерии)
│       ├── sources.md            #   внутренние источники и окна отсечки
│       └── report_template.md    #   шаблон справки + правила PPTX
├── safety/                       # 9.5: safety layer
│   ├── BIBLE.md                  #   Конституция P1–P7
│   └── review_policy.md          #   политика ревью
├── templates/
│   └── report_template.pptx      #   тестовый шаблон (в production — шаблон пользователя)
├── tools/
│   └── make_test_template.py     #   генератор тестового шаблона
├── tests/
│   └── test_suite.py             #   34 теста: unit + integration + валидация файлов
└── data/
    ├── observations_<дата>.json  #   фактические показатели (только save_observation)
    ├── audit_<дата>.json         #   решения отбора показателей с обоснованиями
    └── report_draft_<дата>.md/.docx/.pptx  # материалы цикла
```

## Тесты

```bash
python3 -m unittest discover -s tests -v
```

34 теста: данные и валидация, отбор показателей, DOCX/PPTX-маппинг,
экспортный контракт, HTTP-интеграция (включая гонки и повреждённые файлы),
E2E-цикл. Тестовые данные пишутся во временный каталог, `data/` проекта
не затрагивается.

## Метрики для защиты (AS IS → TO BE)

- Время подготовки справки: 135–200 мин → цель −50–70%.
- Рутинная нагрузка команды: 10–18 чел-ч/день → цель 4–8 чел-ч/день.
- Доля автособранного материала: цель 70–85%.
- Замер: полный цикл (приём → проверка → отбор → DOCX + PPTX) за один
  MCP-вызов `export_report` против часов ручной работы.
