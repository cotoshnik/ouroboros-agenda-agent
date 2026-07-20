# agenda_briefing_agent — пакет по п. 9 гайда Ouroboros (Sber AI Hack)

Автономный ИИ-агент подготовки аналитических справок и мониторинга
приоритетной повестки (проект из `PP AI HACK.docx`). Агент оперирует
каталогом из **67 показателей** (49 базовых + 18 динамических) из
`Показатели_для_подготовки_материалов.xlsx`.

## Закрытие пункта 9 гайда

| Подпункт гайда | Что сделано | Где |
|---|---|---|
| 9.1 Создание SKILL.md | Манифест навыка: AS IS с time-per-step, TO BE-пайплайн, политика автономии (авто / human-in-the-loop) | `SKILL.md` |
| 9.2 Настройка MCP-инструментов | Рабочий MCP-сервер (streamable_http, порт 9999, без зависимостей), 10 инструментов + конфиг для Settings → Advanced → MCP, реальный веб-коллектор RSS, экспорт справки в Word | `mcp/server.py`, `mcp/collector.py`, `mcp/docx_export.py`, `mcp/mcp_servers.json` |
| 9.3 Протокол A2A | Три специализированных агента (collector → analyst → editor) с карточками, контрактом handoff и правилами деградации | `agents/` |
| 9.4 Память и контекст | `identity.md` (самоописание сценария), `scratchpad.md` (рабочая память), `knowledge/` (каталог показателей, источники, шаблон), `dialogue_blocks.json` (консолидированная история) | `memory/` |
| 9.5 Safety layer | Цифровая Конституция P1–P7 (иммунная система P3, hardcoded sandbox P4, запрет `/evolve`), политика ревью Advisory/Blocking | `safety/` |

## Быстрый старт (проверка прототипа)

```bash
cd ouroboros_agent
python3 mcp/server.py   # MCP-сервер на http://localhost:9999/mcp
```

Дальше агент собирает данные сам через MCP-инструменты:

- **веб-мониторинг (реальный):** «Собери сигналы из открытых источников» →
  `fetch_web_sources` обходит RSS (РБК, РИА, ТАСС, Интерфакс, Банк России),
  размечает публикации по темам повестки и складывает в
  `data/signals_<дата>.json`. Перечень источников и словарей —
  `data/web_sources.json`.
- **внутренние показатели:** поступают ТОЛЬКО как реальные значения через
  `save_observation` из внутренних источников (письма, инфопанель).
  Тестовых/демо-данных в проекте нет: без реального значения показатель
  честно помечается в справке как «— нет данных —» (BIBLE.md, P2).

В Ouroboros: Settings → Advanced → MCP → вставить содержимое
`mcp/mcp_servers.json`. После подключения агенту доступны инструменты:
`list_indicators`, `get_selection_criteria`, `save_observation`,
`get_observations`, `fetch_web_sources`, `get_signals`,
`check_completeness`, `detect_hot_topics`, `build_report_draft`,
`export_report_docx`.

Пример диалога для проверки: «Собери сигналы и сохрани справку за
сегодня в Word» → агент вызовет `fetch_web_sources`, затем
`export_report_docx` и сохранит `data/report_draft_<дата>.docx` —
финальный артефакт цикла (контракт результата: `SKILL.md`, раздел 6).

## Структура

```
ouroboros_agent/
├── SKILL.md                      # 9.1: манифест навыка (AS IS / TO BE / автономия)
├── mcp/
│   ├── server.py                 # 9.2: MCP-сервер, 10 инструментов (Python 3.9+, stdlib)
│   ├── collector.py              # веб-коллектор RSS (реальный обход источников)
│   ├── docx_export.py            # экспорт справки в Word .docx (без зависимостей)
│   └── mcp_servers.json          # 9.2: конфиг для Settings → Advanced → MCP
├── agents/                       # 9.3: A2A
│   ├── collector.agent.json      #   сбор данных (auto)
│   ├── analyst.agent.json        #   полнота, «горячие» темы (auto + логи)
│   ├── editor.agent.json         #   черновик + human-in-the-loop
│   └── README.md                 #   схема и контракт handoff
├── memory/                       # 9.4: память и контекст
│   ├── identity.md               #   самоописание агента
│   ├── scratchpad.md             #   рабочая память (авто-очистка)
│   ├── dialogue_blocks.json      #   консолидированная история
│   └── knowledge/
│       ├── indicators.json       #   67 показателей из Excel (base/dynamic + критерии)
│       ├── sources.md            #   источники и окна отсечки
│       └── report_template.md    #   шаблон справки
├── safety/                       # 9.5: safety layer
│   ├── BIBLE.md                  #   Конституция P1–P7
│   └── review_policy.md          #   политика ревью
└── data/
    ├── web_sources.json          #   открытые источники + тематические словари
    ├── observations_<дата>.json  #   РЕАЛЬНЫЕ внутренние показатели (только save_observation)
    ├── signals_<дата>.json       #   собранные веб-сигналы (создаётся коллектором)
    └── report_draft_<дата>.md/.docx  # справки (Word — финальный артефакт)
```

## Метрики для защиты (AS IS → TO BE)

- Время подготовки справки: 135–200 мин → цель −50–70%.
- Рутинная нагрузка команды: 10–18 чел-ч/день → цель 4–8 чел-ч/день.
- Доля автособранного черновика: цель 70–85%.
- Замер: сбор 400+ веб-сигналов и справка в Word за один цикл
  MCP-вызовов (~1 мин против 135–200 мин ручной работы).
