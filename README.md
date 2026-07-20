# agenda_briefing_agent — пакет по п. 9 гайда Ouroboros (Sber AI Hack)

Автономный ИИ-агент подготовки аналитических справок и мониторинга
приоритетной повестки (проект из `PP AI HACK.docx`). Агент оперирует
каталогом из **67 показателей** (49 базовых + 18 динамических) из
`Показатели_для_подготовки_материалов.xlsx`.

## Закрытие пункта 9 гайда

| Подпункт гайда | Что сделано | Где |
|---|---|---|
| 9.1 Создание SKILL.md | Манифест навыка: AS IS с time-per-step, TO BE-пайплайн, политика автономии (авто / human-in-the-loop) | `SKILL.md` |
| 9.2 Настройка MCP-инструментов | Рабочий MCP-сервер (streamable_http, порт 9999, без зависимостей), 9 инструментов + конфиг для Settings → Advanced → MCP, реальный веб-коллектор RSS | `mcp/server.py`, `mcp/collector.py`, `mcp/mcp_servers.json` |
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
- **внутренние показатели:** поступают через `save_observation` из
  внутренних источников (письма, инфопанель); для прогона без доступа к
  ним есть демо-набор: `python3 data/seed_demo.py 2026-07-19`.

В Ouroboros: Settings → Advanced → MCP → вставить содержимое
`mcp/mcp_servers.json`. После подключения агенту доступны инструменты:
`list_indicators`, `get_selection_criteria`, `save_observation`,
`get_observations`, `fetch_web_sources`, `get_signals`,
`check_completeness`, `detect_hot_topics`, `build_report_draft`.

Пример диалога для проверки: «Собери сигналы и черновик справки за
сегодня» → агент вызовет `fetch_web_sources`, затем `build_report_draft`
и вернёт markdown-черновик с разделом веб-мониторинга.

## Структура

```
ouroboros_agent/
├── SKILL.md                      # 9.1: манифест навыка (AS IS / TO BE / автономия)
├── mcp/
│   ├── server.py                 # 9.2: MCP-сервер, 9 инструментов (Python 3.9+, stdlib)
│   ├── collector.py              # веб-коллектор RSS (реальный обход источников)
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
    ├── seed_demo.py              #   генератор демо-наблюдений (внутренние показатели)
    ├── observations_2026-07-19.json
    ├── signals_<дата>.json       #   собранные веб-сигналы (создаётся коллектором)
    └── report_draft_2026-07-19.md
```

## Метрики для защиты (AS IS → TO BE)

- Время подготовки справки: 135–200 мин → цель −50–70%.
- Рутинная нагрузка команды: 10–18 чел-ч/день → цель 4–8 чел-ч/день.
- Доля автособранного черновика: цель 70–85%.
- Замер на демо-данных: сбор 56 наблюдений + черновик за один цикл
  MCP-вызовов (~1 мин против 135–200 мин ручной работы).
