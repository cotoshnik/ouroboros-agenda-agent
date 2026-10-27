# scratchpad — рабочая память агента

> Авто-очищается после завершения цикла `briefing-<дата>`: итог цикла
> консолидируется в `dialogue_blocks.json`, черновые записи удаляются.
> Решения отбора показателей хранятся в audit-артефакте цикла
> (`../data/audit_<дата>.json`).

## Цикл briefing-<дата> — шаблон записей

- <время> collector: источник «письмо ~06:30» обработан, сохранены
  показатели 8–13 через `save_observation`.
- <время> collector: `check_completeness` — собрано N/49 базовых.
- <время> analyst: `detect_hot_topics` → N срабатываний.
- <время> analyst: `select_presentation_indicators` → отбор из K
  показателей с обоснованиями, серверная валидация пройдена.
- <время> editor: `export_report` → `report_draft_<дата>.docx` +
  `report_draft_<дата>.pptx`, status=success. Ожидают верификации
  аналитиком.
