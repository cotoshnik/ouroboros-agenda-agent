#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP-сервер навыка agenda_briefing_agent (Sber AI Hack, пункт 9.2 гайда Ouroboros).

Streamable HTTP transport. Работает только с фактическими показателями:
карта показателей memory/knowledge/indicators.json + observations за дату
(data/observations_<дата>.json). Новостной/RSS-контур из пайплайна удалён.

Запуск:  python3 mcp/server.py   ->  http://127.0.0.1:9999/mcp
Конфигурация через окружение:
  AGENDA_HOST          (по умолч. 127.0.0.1 — только loopback)
  AGENDA_PORT          (по умолч. 9999)
  AGENDA_DATA_DIR      (по умолч. <проект>/data)
  AGENDA_PPTX_TEMPLATE (путь к PPTX-шаблону по умолчанию)

Зависимости: python-pptx (см. requirements.txt). Остальное — stdlib.
"""

import json
import logging
import os
import re
import signal
import threading
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.environ.get("AGENDA_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENDA_PORT", "9999"))
DATA_DIR = os.path.abspath(os.environ.get("AGENDA_DATA_DIR", os.path.join(BASE_DIR, "data")))
KNOWLEDGE_FILE = os.path.join(BASE_DIR, "memory", "knowledge", "indicators.json")
DEFAULT_PPTX_TEMPLATE = os.environ.get("AGENDA_PPTX_TEMPLATE", "")

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "agenda_briefing", "version": "0.3.0"}

MAX_BODY_BYTES = 1024 * 1024          # лимит входного JSON-RPC запроса
REQUEST_TIMEOUT_SEC = 30              # таймаут чтения/записи сокета
MAX_TEXT_FIELD = 500                  # лимит текстовых полей observation
NO_DATA = "— нет данных —"

os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(os.path.join(DATA_DIR, "server.log"), encoding="utf-8")],
)
log = logging.getLogger("agenda_briefing")

# Единая блокировка на все чтения/записи файлов данных: ThreadingHTTPServer
# обслуживает запросы в потоках, без лока возможны гонки и потеря обновлений.
LOCK = threading.RLock()


class ToolError(Exception):
    """Ошибка уровня инструмента: сообщение уходит клиенту как есть,
    поэтому без стеков, путей серверных секретов и лишних деталей."""


# Пороги для выявления «горячих» тем: id показателя -> (условие, пояснение)
HOT_RULES = {
    26: (lambda v: v < 99.5, "КТД ниже 99.5%"),
    28: (lambda v: v > 0, "есть значимые инциденты безопасности"),
    33: (lambda v: v > 0, "инциденты СВО повлияли на непрерывность деятельности Банка"),
    52: (lambda v: v > 0, "объявлен план «Ковёр»"),
    56: (lambda v: v > 0, "есть критичные IT-инциденты"),
    58: (lambda v: v > 0, "DDoS-атаки на ресурсы Банка"),
    59: (lambda v: v > 0, "DDoS-атаки на ресурсы ДЗО"),
    62: (lambda v: v > 0, "есть тяжёлые случаи заболевания на мониторинге"),
    67: (lambda v: v > 0, "есть природные инциденты"),
    68: (lambda v: v > 0, "есть технологические инциденты"),
}


# --------------------------- базовые помощники ---------------------------

def _atomic_write_bytes(path, data):
    """Атомарная запись: временный файл + fsync + os.replace."""
    tmp = "%s.tmp-%d-%d" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def validate_date(date):
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ToolError("date должен быть строкой в формате ГГГГ-ММ-ДД")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ToolError("date не является реальной календарной датой: %s" % date)
    return date


def _safe_path_in_data(filename):
    """Путь строго внутри DATA_DIR (защита от path traversal)."""
    path = os.path.realpath(os.path.join(DATA_DIR, filename))
    if not path.startswith(os.path.realpath(DATA_DIR) + os.sep):
        raise ToolError("недопустимый путь")
    return path


def load_catalog():
    with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
        return json.load(f)


def observations_path(date):
    return _safe_path_in_data("observations_%s.json" % validate_date(date))


def load_observations(date):
    path = observations_path(date)
    if not os.path.exists(path):
        return {"date": date, "observations": []}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Не перезаписываем и не «чиним» молча: данные могут быть ценными.
        raise ToolError(
            "файл observations за %s повреждён (невалидный JSON); "
            "восстановите его или переместите в архив, молчаливая перезапись запрещена" % date)


def save_observations(date, payload):
    _atomic_write_json(observations_path(date), payload)


def find_indicator(catalog, indicator_id):
    for ind in catalog["indicators"]:
        if ind["id"] == indicator_id:
            return ind
    return None


def _obs_map(date):
    return {o["indicator_id"]: o for o in load_observations(date)["observations"]}


def _hot_hits(date):
    """Показатели, сработавшие по HOT_RULES, с фактическими наблюдениями."""
    hits = {}
    for obs in load_observations(date)["observations"]:
        rule = HOT_RULES.get(obs["indicator_id"])
        if not rule:
            continue
        try:
            value = float(obs["value"])
        except (TypeError, ValueError):
            continue
        condition, reason = rule
        if condition(value):
            hits[obs["indicator_id"]] = reason
    return hits


# --------------------------- инструменты: данные ---------------------------

def tool_list_indicators(args):
    block = args.get("block", "all")
    if block not in ("base", "dynamic", "all"):
        raise ToolError("block должен быть base | dynamic | all")
    catalog = load_catalog()
    result = []
    for ind in catalog["indicators"]:
        ind_block = "base" if ind["base_block"] else "dynamic"
        if block in ("base", "dynamic") and ind_block != block:
            continue
        result.append({
            "id": ind["id"],
            "name": ind["name"],
            "block": ind_block,
            "selection_criterion": ind["selection_criterion"],
        })
    return {"total": len(result), "block": block, "indicators": result}


def tool_get_selection_criteria(args):
    catalog = load_catalog()
    criteria = [
        {"id": ind["id"], "name": ind["name"], "criterion": ind["selection_criterion"]}
        for ind in catalog["indicators"] if ind["selection_criterion"]
    ]
    return {
        "total": len(criteria),
        "note": "Окна отсечки обязательны при сборе: значение показателя действительно только внутри его окна.",
        "criteria": criteria,
    }


def tool_save_observation(args):
    indicator_id = args.get("indicator_id")
    if isinstance(indicator_id, bool) or not isinstance(indicator_id, int):
        raise ToolError("indicator_id обязателен и должен быть целым числом")
    date = validate_date(args.get("date"))
    if "value" not in args:
        raise ToolError("value обязателен")
    value = args["value"]
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        raise ToolError("value должен быть числом или строкой")
    if isinstance(value, str) and len(value) > MAX_TEXT_FIELD:
        raise ToolError("value длиннее %d символов" % MAX_TEXT_FIELD)
    unit = args.get("unit", "")
    source = args.get("source", "")
    for field, val in (("unit", unit), ("source", source)):
        if not isinstance(val, str) or len(val) > MAX_TEXT_FIELD:
            raise ToolError("%s должен быть строкой до %d символов" % (field, MAX_TEXT_FIELD))

    catalog = load_catalog()
    ind = find_indicator(catalog, indicator_id)
    if ind is None:
        raise ToolError("показатель с id=%s не найден в каталоге" % indicator_id)

    with LOCK:
        store = load_observations(date)
        store["observations"] = [o for o in store["observations"] if o["indicator_id"] != indicator_id]
        store["observations"].append({
            "indicator_id": indicator_id,
            "name": ind["name"],
            "block": "base" if ind["base_block"] else "dynamic",
            "value": value,
            "unit": unit,
            "source": source,
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        })
        store["observations"].sort(key=lambda o: o["indicator_id"])
        save_observations(date, store)
    return {"saved": True, "date": date, "indicator_id": indicator_id, "name": ind["name"]}


def tool_get_observations(args):
    date = validate_date(args.get("date"))
    with LOCK:
        store = load_observations(date)
    return {"date": store["date"], "total": len(store["observations"]), "observations": store["observations"]}


def tool_check_completeness(args):
    date = validate_date(args.get("date"))
    catalog = load_catalog()
    with LOCK:
        have = set(_obs_map(date))
    missing = [
        {"id": ind["id"], "name": ind["name"], "criterion": ind["selection_criterion"]}
        for ind in catalog["indicators"] if ind["base_block"] and ind["id"] not in have
    ]
    return {
        "date": date,
        "base_total": sum(1 for i in catalog["indicators"] if i["base_block"]),
        "collected_base": sum(1 for i in catalog["indicators"] if i["base_block"] and i["id"] in have),
        "missing_base": missing,
        "complete": not missing,
    }


def tool_detect_hot_topics(args):
    date = validate_date(args.get("date"))
    with LOCK:
        obs = {o["indicator_id"]: o for o in load_observations(date)["observations"]}
        hits = _hot_hits(date)
    hot = [{"indicator_id": iid, "name": obs[iid]["name"], "value": obs[iid]["value"],
            "unit": obs[iid].get("unit", ""), "reason": reason}
           for iid, reason in sorted(hits.items())]
    return {"date": date, "hot_topics": hot,
            "note": "«Горячие» показатели — кандидаты в презентацию с высшим приоритетом; итоговый отбор делает analyst_agent."}


# ------------------- инструменты: отбор для презентации -------------------

PRIORITIES = ("high", "normal", "low")


def _audit_path(date):
    return _safe_path_in_data("audit_%s.json" % validate_date(date))


def _write_audit(date, payload):
    _atomic_write_json(_audit_path(date), payload)


def _read_audit(date):
    path = _audit_path(date)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _analysis_context(date):
    """Вход для отбора: только карта показателей + фактические данные даты."""
    catalog = load_catalog()
    with LOCK:
        obs = _obs_map(date)
        hot = _hot_hits(date)
    completeness = tool_check_completeness({"date": date})
    return catalog, obs, hot, completeness


def _server_proposal(date):
    """Детерминированное предложение отбора (без участия модели):
    включить только сработавшие HOT_RULES, у остальных — запросить решение аналитика."""
    catalog, obs, hot, completeness = _analysis_context(date)
    decisions = []
    for ind in catalog["indicators"]:
        o = obs.get(ind["id"])
        if o is None:
            continue  # без фактического значения кандидатом не является
        if ind["id"] in hot:
            decisions.append({
                "indicator_id": ind["id"], "priority": "high", "include": True,
                "justification": "сработало пороговое правило: %s" % hot[ind["id"]],
                "ref": "hot_rule",
            })
        else:
            decisions.append({
                "indicator_id": ind["id"], "priority": "normal", "include": False,
                "justification": "значение в допустимых пределах; включение — на усмотрение analyst_agent",
                "ref": "observation",
            })
    return {"date": date, "mode": "proposal", "decisions": decisions,
            "completeness": {"collected_base": completeness["collected_base"],
                             "base_total": completeness["base_total"],
                             "complete": completeness["complete"]}}


def _validate_selection(date, selections):
    """Серверная проверка отбора модели: нельзя доверять списку модели.

    - каждый id обязан существовать в карте и иметь фактическое наблюдение за дату;
    - показатели HOT_RULES принудительно включаются (модель не может их скрыть);
    - невалидные решения отклоняются с warnings, а не молча.
    """
    if not isinstance(selections, list):
        raise ToolError("selections должен быть массивом решений")
    catalog, obs, hot, completeness = _analysis_context(date)
    warnings = []
    validated = []
    seen = set()

    for i, dec in enumerate(selections):
        if not isinstance(dec, dict):
            warnings.append("решение #%d отклонено: не объект" % i)
            continue
        iid = dec.get("indicator_id")
        if isinstance(iid, bool) or not isinstance(iid, int):
            warnings.append("решение #%d отклонено: indicator_id не целое" % i)
            continue
        if iid in seen:
            warnings.append("indicator_id=%d: дубликат решения, учтено первое" % iid)
            continue
        seen.add(iid)
        ind = find_indicator(catalog, iid)
        if ind is None:
            warnings.append("indicator_id=%d отклонён: нет в карте показателей" % iid)
            continue
        if iid not in obs:
            warnings.append("indicator_id=%d (%s) отклонён: нет фактического наблюдения за %s"
                            % (iid, ind["name"], date))
            continue
        include = dec.get("include")
        if not isinstance(include, bool):
            warnings.append("indicator_id=%d: include не boolean, считается false" % iid)
            include = False
        priority = dec.get("priority", "normal")
        if priority not in PRIORITIES:
            warnings.append("indicator_id=%d: недопустимый приоритет %r, заменён на normal"
                            % (iid, priority))
            priority = "normal"
        justification = str(dec.get("justification", "")).strip()
        if not justification:
            warnings.append("indicator_id=%d: пустое обоснование" % iid)
            justification = "без обоснования (аналитик)"
        if iid in hot and not include:
            warnings.append("indicator_id=%d принудительно включён: сработало правило HOT_RULES"
                            % iid)
            include = True
            priority = "high"
        validated.append({
            "indicator_id": iid,
            "priority": priority,
            "include": include,
            "justification": justification[:MAX_TEXT_FIELD],
            "ref": "hot_rule" if iid in hot else "observation",
        })

    # Принудительное включение hot-показателей, о которых модель «забыла»
    for iid, reason in sorted(hot.items()):
        if iid not in seen:
            warnings.append("indicator_id=%d добавлен сервером: сработало правило HOT_RULES (%s)"
                            % (iid, reason))
            validated.append({
                "indicator_id": iid, "priority": "high", "include": True,
                "justification": "сработало пороговое правило: %s" % reason,
                "ref": "hot_rule",
            })

    validated.sort(key=lambda d: (0 if d["priority"] == "high" else 1, d["indicator_id"]))
    return validated, warnings


def tool_select_presentation_indicators(args):
    """Этап отбора: без selections возвращает вход для analyst_agent и
    серверное предложение; с selections — валидирует отбор аналитика."""
    date = validate_date(args.get("date"))
    selections = args.get("selections")
    if selections is None:
        result = _server_proposal(date)
        _write_audit(date, {"date": date, "source": "server_proposal",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "decisions": result["decisions"]})
        return result
    validated, warnings = _validate_selection(date, selections)
    selected_ids = [d["indicator_id"] for d in validated if d["include"]]
    _write_audit(date, {"date": date, "source": "analyst",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "decisions": validated, "warnings": warnings})
    return {"date": date, "mode": "validated", "decisions": validated,
            "selected_indicator_ids": selected_ids, "warnings": warnings,
            "note": "Отбор проверен сервером: каждый id имеет фактическое наблюдение; HOT_RULES включены принудительно."}


def _resolve_selection(date, selection):
    """Отбор для экспорта: явный selection > последний audit аналитика > серверное предложение."""
    warnings = []
    if selection is not None:
        validated, warnings = _validate_selection(date, selection)
        source = "analyst"
    else:
        audit = _read_audit(date)
        if audit and audit.get("source") == "analyst" and audit.get("decisions"):
            validated, warnings = _validate_selection(date, audit["decisions"])
            source = "audit"
        else:
            validated = [d for d in _server_proposal(date)["decisions"] if d["include"]]
            source = "server_proposal"
            warnings.append("отбор аналитика отсутствовал — использовано серверное предложение (HOT_RULES)")
    selected = [d for d in validated if d["include"]]
    _write_audit(date, {"date": date, "source": source,
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "decisions": validated, "warnings": warnings})
    return selected, warnings


# ------------------- инструменты: сборка и экспорт -------------------

def tool_build_report_draft(args):
    date = validate_date(args.get("date"))
    catalog = load_catalog()
    with LOCK:
        obs_by_id = _obs_map(date)
        hot = _hot_hits(date)

    def row(ind):
        obs = obs_by_id.get(ind["id"])
        val = ("%s %s" % (obs["value"], obs.get("unit", ""))).strip() if obs else NO_DATA
        return "| %s | %s |" % (ind["name"], val)

    lines = [
        "# Аналитическая справка дежурной смены — ЧЕРНОВИК",
        "",
        "Дата формирования: %s" % date,
        "Сформировано автономным агентом agenda_briefing_agent; требует верификации аналитиком.",
        "",
        "## 1. Базовый блок (обязательные показатели)",
        "",
        "| Показатель | Значение |",
        "|------------|----------|",
    ]
    for ind in catalog["indicators"]:
        if ind["base_block"]:
            lines.append(row(ind))

    dynamic_present = [ind for ind in catalog["indicators"]
                       if ind["dynamic_block"] and ind["id"] in obs_by_id]
    lines += ["", "## 2. Динамический блок (включён по наличию фактических данных)", ""]
    if dynamic_present:
        lines += ["| Показатель | Значение |", "|------------|----------|"]
        lines += [row(ind) for ind in dynamic_present]
    else:
        lines.append("Фактических данных для динамического блока на %s не поступало." % date)

    lines += ["", "## 3. Сигналы для внимания аналитика", ""]
    if hot:
        for iid, reason in sorted(hot.items()):
            obs = obs_by_id[iid]
            lines.append("- **%s**: %s %s (%s)" % (obs["name"], obs["value"],
                                                   obs.get("unit", ""), reason))
    else:
        lines.append("Превышений порогов не зафиксировано.")

    completeness = tool_check_completeness({"date": date})
    lines += ["", "## 4. Полнота данных", ""]
    if completeness["missing_base"]:
        lines.append("Не поступили %d базовых показателей: %s"
                     % (len(completeness["missing_base"]),
                        ", ".join(m["name"] for m in completeness["missing_base"])))
    else:
        lines.append("Все базовые показатели собраны.")
    lines += ["", "---", "Черновик. Выводы и интерпретация — за аналитиком (human-in-the-loop)."]
    return {"date": date, "draft_markdown": "\n".join(lines)}


def _validate_docx(path):
    if not zipfile.is_zipfile(path):
        raise ToolError("DOCX не является валидным ZIP/OOXML-пакетом")
    with zipfile.ZipFile(path) as z:
        if "word/document.xml" not in z.namelist():
            raise ToolError("DOCX повреждён: отсутствует word/document.xml")


def _write_docx(date):
    """Собирает черновик и атомарно сохраняет .md и .docx, затем валидирует."""
    import docx_export
    with LOCK:
        draft = tool_build_report_draft({"date": date})["draft_markdown"]
    md_path = _safe_path_in_data("report_draft_%s.md" % date)
    docx_path = _safe_path_in_data("report_draft_%s.docx" % date)
    tmp_docx = docx_path + ".tmp-%d" % os.getpid()
    try:
        docx_export.markdown_to_docx(
            draft, tmp_docx, title="Аналитическая справка дежурной смены — %s" % date)
        _validate_docx(tmp_docx)
        os.replace(tmp_docx, docx_path)
    finally:
        if os.path.exists(tmp_docx):
            os.remove(tmp_docx)
    _atomic_write_bytes(md_path, draft.encode("utf-8"))
    _validate_docx(docx_path)
    return docx_path, md_path


def tool_export_report_docx(args):
    date = validate_date(args.get("date"))
    docx_path, md_path = _write_docx(date)
    return {"date": date, "status": "success", "docx_path": docx_path, "md_path": md_path,
            "note": "Справка сохранена в Word. Требует верификации аналитиком (human-in-the-loop)."}


def _resolve_template_path(args):
    path = args.get("template_path") or DEFAULT_PPTX_TEMPLATE
    if not path:
        return None
    return os.path.abspath(os.path.expanduser(str(path)))


def _validate_template(path):
    """Отсутствующий/повреждённый шаблон — явная ошибка, без имитации успеха."""
    if not os.path.exists(path):
        raise ToolError("PPTX-шаблон не найден: передайте template_path или задайте AGENDA_PPTX_TEMPLATE")
    if not path.lower().endswith(".pptx"):
        raise ToolError("шаблон должен быть файлом .pptx")
    if not zipfile.is_zipfile(path):
        raise ToolError("PPTX-шаблон повреждён (не ZIP/OOXML-пакет)")
    try:
        from pptx import Presentation
        Presentation(path)
    except Exception:
        raise ToolError("PPTX-шаблон не открывается python-pptx (некорректный файл)")


def tool_inspect_pptx_template(args):
    path = _resolve_template_path(args)
    if not path:
        raise ToolError("не передан template_path и не задан AGENDA_PPTX_TEMPLATE")
    _validate_template(path)
    import pptx_export
    return pptx_export.inspect_template(path)


def _write_pptx(date, template_path, selection):
    import pptx_export
    if template_path is None:
        return None, ["PPTX-шаблон не передан: этап пропущен, статус template_required"], "template_required"
    _validate_template(template_path)
    selected, warnings = _resolve_selection(date, selection)
    catalog = load_catalog()
    with LOCK:
        obs = _obs_map(date)
    items = []
    for dec in selected:
        ind = find_indicator(catalog, dec["indicator_id"])
        o = obs[dec["indicator_id"]]
        items.append({
            "indicator_id": ind["id"], "name": ind["name"],
            "block": "base" if ind["base_block"] else "dynamic",
            "value": o["value"], "unit": o.get("unit", ""), "source": o.get("source", ""),
            "priority": dec["priority"], "justification": dec["justification"],
        })
    completeness = tool_check_completeness({"date": date})
    pptx_path = _safe_path_in_data("report_draft_%s.pptx" % date)
    tmp_pptx = pptx_path + ".tmp-%d" % os.getpid()
    try:
        pptx_export.render_pptx(template_path, tmp_pptx, date, items, completeness)
        pptx_export.validate_pptx(tmp_pptx)
        os.replace(tmp_pptx, pptx_path)
    finally:
        if os.path.exists(tmp_pptx):
            os.remove(tmp_pptx)
    pptx_export.validate_pptx(pptx_path)
    return (pptx_path, [d["indicator_id"] for d in selected], warnings), None


def tool_export_report_pptx(args):
    date = validate_date(args.get("date"))
    template_path = _resolve_template_path(args)
    if template_path is None:
        return {"date": date, "status": "template_required",
                "pptx_path": None,
                "note": "Передайте готовый .pptx-шаблон (template_path или AGENDA_PPTX_TEMPLATE). "
                        "Собственный дизайн вместо шаблона не создаётся."}
    (pptx_path, selected_ids, warnings), _ = _write_pptx(date, template_path,
                                                         args.get("selection"))
    return {"date": date, "status": "success", "pptx_path": pptx_path,
            "selected_indicator_ids": selected_ids, "warnings": warnings,
            "note": "Презентация собрана по шаблону. Требует верификации аналитиком (human-in-the-loop)."}


def tool_export_report(args):
    """Единый экспортный контракт цикла: DOCX + PPTX. status=success только
    при успешном создании и валидации ОБОИХ файлов."""
    date = validate_date(args.get("date"))
    warnings = []
    result = {"date": date, "status": "success", "docx_path": None,
              "pptx_path": None, "selected_indicator_ids": [],
              "missing_base_count": 0, "warnings": warnings}

    try:
        result["docx_path"], _ = _write_docx(date)
    except ToolError as exc:
        result["status"] = "error"
        result["failed_stage"] = "docx"
        warnings.append("этап DOCX: %s" % exc)
        return result

    completeness = tool_check_completeness({"date": date})
    result["missing_base_count"] = len(completeness["missing_base"])
    if not completeness["complete"]:
        warnings.append("базовый блок неполон: %d показателей без данных"
                        % result["missing_base_count"])

    template_path = _resolve_template_path(args)
    if template_path is None:
        result["status"] = "error"
        result["failed_stage"] = "pptx"
        warnings.append("этап PPTX: шаблон не передан (template_path или AGENDA_PPTX_TEMPLATE); "
                        "DOCX-артефакт сохранён и не удалён")
        return result
    try:
        (pptx_path, selected_ids, pptx_warnings), _ = _write_pptx(
            date, template_path, args.get("selection"))
        result["pptx_path"] = pptx_path
        result["selected_indicator_ids"] = selected_ids
        warnings.extend(pptx_warnings)
    except ToolError as exc:
        result["status"] = "error"
        result["failed_stage"] = "pptx"
        warnings.append("этап PPTX: %s; DOCX-артефакт сохранён и не удалён" % exc)
    return result


# --------------------------- реестр инструментов ---------------------------

TOOLS = [
    {"name": "list_indicators",
     "description": "Карта показателей для аналитических материалов (67 шт.). Фильтр block: base | dynamic | all.",
     "inputSchema": {"type": "object",
                     "properties": {"block": {"type": "string", "enum": ["base", "dynamic", "all"], "default": "all"}}}},
    {"name": "get_selection_criteria",
     "description": "Критерии отбора и окна отсечки показателей (за какие сутки брать, когда приходит источник).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "save_observation",
     "description": "Сохранить фактическое значение показателя за дату (внутренние источники: письма, инфопанель).",
     "inputSchema": {"type": "object",
                     "properties": {"indicator_id": {"type": "integer"}, "date": {"type": "string"},
                                    "value": {"type": ["number", "string"]}, "unit": {"type": "string"},
                                    "source": {"type": "string"}},
                     "required": ["indicator_id", "date", "value"]}},
    {"name": "get_observations",
     "description": "Получить все фактические значения показателей за дату.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "check_completeness",
     "description": "Контроль полноты: какие базовые (обязательные) показатели ещё не собраны за дату.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "detect_hot_topics",
     "description": "Показатели, сработавшие по пороговым правилам HOT_RULES (КТД, инциденты, DDoS и др.). Только фактические значения.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "select_presentation_indicators",
     "description": "Отбор показателей для презентации. Без selections — вход для анализа и серверное предложение; "
                    "с selections — серверная валидация отбора analyst_agent (проверка id, фактических значений, HOT_RULES).",
     "inputSchema": {"type": "object",
                     "properties": {"date": {"type": "string"},
                                    "selections": {"type": "array", "items": {"type": "object"}}},
                     "required": ["date"]}},
    {"name": "build_report_draft",
     "description": "Полная сводка в markdown: базовый блок целиком, динамический по фактическим данным, сигналы, полнота.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "inspect_pptx_template",
     "description": "Анализ PPTX-шаблона: макеты, плейсхолдеры, слайды, тема, шрифты, цвета. Вызвать до export_report_pptx.",
     "inputSchema": {"type": "object", "properties": {"template_path": {"type": "string"}}}},
    {"name": "export_report_docx",
     "description": "Собрать справку и сохранить её в Word (.docx) и markdown в папку data/.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "export_report_pptx",
     "description": "Собрать презентацию по предоставленному PPTX-шаблону (template_path или AGENDA_PPTX_TEMPLATE). "
                    "Без шаблона возвращает status=template_required.",
     "inputSchema": {"type": "object",
                     "properties": {"date": {"type": "string"}, "template_path": {"type": "string"},
                                    "selection": {"type": "array", "items": {"type": "object"}}},
                     "required": ["date"]}},
    {"name": "export_report",
     "description": "Финальный шаг цикла: DOCX + PPTX с валидацией обоих файлов. status=success только когда оба артефакта созданы.",
     "inputSchema": {"type": "object",
                     "properties": {"date": {"type": "string"}, "template_path": {"type": "string"},
                                    "selection": {"type": "array", "items": {"type": "object"}}},
                     "required": ["date"]}},
]

TOOL_IMPL = {
    "list_indicators": tool_list_indicators,
    "get_selection_criteria": tool_get_selection_criteria,
    "save_observation": tool_save_observation,
    "get_observations": tool_get_observations,
    "check_completeness": tool_check_completeness,
    "detect_hot_topics": tool_detect_hot_topics,
    "select_presentation_indicators": tool_select_presentation_indicators,
    "build_report_draft": tool_build_report_draft,
    "inspect_pptx_template": tool_inspect_pptx_template,
    "export_report_docx": tool_export_report_docx,
    "export_report_pptx": tool_export_report_pptx,
    "export_report": tool_export_report,
}

for _name in TOOL_IMPL:
    assert any(t["name"] == _name for t in TOOLS), "TOOL_IMPL без схемы: %s" % _name
assert len(TOOLS) == len(TOOL_IMPL), "TOOLS и TOOL_IMPL рассинхронизированы"


# --------------------------- JSON-RPC / MCP dispatch ---------------------------

def rpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg):
    if not isinstance(msg, dict):
        return rpc_error(None, -32600, "invalid request")
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(method, str):
        return rpc_error(req_id, -32600, "invalid request: method")
    if not isinstance(params, dict):
        return rpc_error(req_id, -32602, "invalid params")

    if method == "initialize":
        return rpc_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return rpc_result(req_id, {})
    if method == "tools/list":
        return rpc_result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        impl = TOOL_IMPL.get(name)
        if impl is None:
            return rpc_error(req_id, -32602, "unknown tool: %s" % name)
        started = datetime.now()
        corr = "%s:%s" % (name, (params.get("arguments") or {}).get("date", "-"))
        try:
            result = impl(params.get("arguments") or {})
            log.info("tool=%s corr=%s ok duration=%.2fs", name, corr,
                     (datetime.now() - started).total_seconds())
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "isError": False,
            })
        except ToolError as exc:
            log.warning("tool=%s corr=%s tool_error: %s", name, corr, exc)
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(
                    {"status": "error", "error": str(exc)}, ensure_ascii=False)}],
                "isError": True,
            })
        except Exception:
            log.exception("tool=%s corr=%s internal_error", name, corr)
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(
                    {"status": "error", "error": "внутренняя ошибка сервера, см. server.log"},
                    ensure_ascii=False)}],
                "isError": True,
            })
    if req_id is None:  # notifications/initialized и прочие уведомления
        return None
    return rpc_error(req_id, -32601, "method not found: %s" % method)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "AgendaBriefingMCP/0.3"
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SEC)

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") not in ("/mcp", ""):
            self._send_json(404, rpc_error(None, -32601, "unknown path"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, rpc_error(None, -32700, "invalid Content-Length"))
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send_json(413, rpc_error(None, -32600, "request body too large or empty"))
            return
        try:
            raw = self.rfile.read(length)
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, rpc_error(None, -32700, "parse error"))
            return
        if isinstance(message, list):  # batch
            if len(message) > 100:
                self._send_json(400, rpc_error(None, -32600, "batch too large"))
                return
            responses = [r for r in (handle_message(m) for m in message) if r is not None]
            if responses:
                self._send_json(200, responses)
            else:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        response = handle_message(message)
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_json(200, response)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok", "server": SERVER_INFO["name"],
                                  "version": SERVER_INFO["version"], "tools": len(TOOLS)})
            return
        if self.path.rstrip("/") in ("/mcp", ""):
            # SSE-стрим не используется: сервер работает в простом JSON-режиме
            self._send_json(405, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32601, "message": "SSE not supported, use POST"}})
            return
        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        log.info("http " + fmt, *args)


def main():
    server = ThreadingHTTPServer((HOST, PORT), MCPHandler)
    server.daemon_threads = True

    def _shutdown(signum, frame):
        log.info("получен сигнал %s — завершаю работу", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("MCP-сервер %s слушает http://%s:%d/mcp", SERVER_INFO["name"], HOST, PORT)
    log.info("Инструментов: %d. Каталог: %s. Данные: %s", len(TOOLS), KNOWLEDGE_FILE, DATA_DIR)
    print("MCP-сервер agenda_briefing слушает http://%s:%d/mcp" % (HOST, PORT))
    print("Health-check: http://%s:%d/health" % (HOST, PORT))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.info("сервер остановлен")
        print("Остановлено.")


if __name__ == "__main__":
    main()
