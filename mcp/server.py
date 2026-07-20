#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP-сервер навыка agenda_briefing_agent (Sber AI Hack, пункт 9.2 гайда Ouroboros).

Streamable HTTP transport, только стандартная библиотека Python 3.9+.
Запуск:  python3 mcp/server.py   ->  http://localhost:9999/mcp

Инструменты работают поверх каталога показателей
memory/knowledge/indicators.json и файлов наблюдений data/observations_<дата>.json
"""

import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector

HOST = "127.0.0.1"
PORT = 9999
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_FILE = os.path.join(BASE_DIR, "memory", "knowledge", "indicators.json")
DATA_DIR = os.path.join(BASE_DIR, "data")

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "agenda_briefing", "version": "0.1.0"}

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


def load_catalog():
    with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
        return json.load(f)


def observations_path(date):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise ValueError("date должен быть в формате ГГГГ-ММ-ДД")
    return os.path.join(DATA_DIR, "observations_%s.json" % date)


def load_observations(date):
    path = observations_path(date)
    if not os.path.exists(path):
        return {"date": date, "observations": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_observations(date, payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(observations_path(date), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def find_indicator(catalog, indicator_id):
    for ind in catalog["indicators"]:
        if ind["id"] == indicator_id:
            return ind
    return None


# --------------------------- реализация инструментов ---------------------------

def tool_list_indicators(args):
    block = args.get("block", "all")
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
    indicator_id = int(args["indicator_id"])
    date = args["date"]
    value = args["value"]
    unit = args.get("unit", "")
    source = args.get("source", "")
    catalog = load_catalog()
    ind = find_indicator(catalog, indicator_id)
    if ind is None:
        raise ValueError("показатель с id=%s не найден в каталоге" % indicator_id)
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
    store = load_observations(args["date"])
    return {"date": store["date"], "total": len(store["observations"]), "observations": store["observations"]}


def tool_check_completeness(args):
    date = args["date"]
    catalog = load_catalog()
    have = {o["indicator_id"] for o in load_observations(date)["observations"]}
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
    date = args["date"]
    store = load_observations(date)
    hot = []
    for obs in store["observations"]:
        rule = HOT_RULES.get(obs["indicator_id"])
        if not rule:
            continue
        try:
            value = float(obs["value"])
        except (TypeError, ValueError):
            continue
        condition, reason = rule
        if condition(value):
            hot.append({"indicator_id": obs["indicator_id"], "name": obs["name"],
                        "value": obs["value"], "unit": obs.get("unit", ""), "reason": reason})
    return {"date": date, "hot_topics": hot,
            "note": "«Горячие» темы кандидатны в динамический блок; включение в повестку подтверждает аналитик."}


def tool_fetch_web_sources(args):
    date = args.get("date") or datetime.now().strftime("%Y-%m-%d")
    stats = collector.collect(date)
    stats["note"] = "Собраны сигналы из открытых источников (data/web_sources.json). Повторный запуск дособирает только новые."
    return stats


def tool_get_signals(args):
    date = args.get("date") or datetime.now().strftime("%Y-%m-%d")
    topic = args.get("topic")
    limit = int(args.get("limit", 50))
    signals = collector.load_signals(date)["signals"]
    if topic:
        signals = [s for s in signals if topic in s["topics"]]
    return {"date": date, "topic": topic or "all", "total": len(signals), "signals": signals[:limit]}


def _web_section(date):
    """Раздел черновика: мониторинг открытых источников."""
    signals = collector.load_signals(date)["signals"]
    lines = ["", "## 4. Мониторинг открытых источников", ""]
    if not signals:
        lines.append("Веб-сбор не выполнялся (запустите инструмент fetch_web_sources).")
        return lines

    topic_counts = {}
    for s in signals:
        for t in s["topics"]:
            topic_counts[t] = topic_counts.get(t, 0) + 1
    if topic_counts:
        lines.append("Темы повестки: " + ", ".join("%s — %d" % (t, n) for t, n in
                     sorted(topic_counts.items(), key=lambda kv: -kv[1])))
        lines.append("")

    urgent = [s for s in signals if s["urgent"]]
    if urgent:
        lines.append("**Экстренные сигналы:**")
        lines += ["- **[%s]** %s — %s (%s)" % (s["source_name"], s["title"], s["link"],
                  ", ".join(s["urgent"])) for s in urgent[:10]]
        lines.append("")

    relevant = [s for s in signals if s["topics"]][-10:]
    if relevant:
        lines.append("Последние релевантные публикации:")
        lines += ["- [%s] %s — %s" % (s["source_name"], s["title"], s["link"]) for s in relevant]
    return lines


def tool_build_report_draft(args):
    date = args["date"]
    catalog = load_catalog()
    store = load_observations(date)
    obs_by_id = {o["indicator_id"]: o for o in store["observations"]}

    def row(ind):
        obs = obs_by_id.get(ind["id"])
        val = ("%s %s" % (obs["value"], obs.get("unit", ""))).strip() if obs else "— нет данных —"
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
    lines += ["", "## 2. Динамический блок (включён по наличию данных)", ""]
    if dynamic_present:
        lines += ["| Показатель | Значение |", "|------------|----------|"]
        lines += [row(ind) for ind in dynamic_present]
    else:
        lines.append("Данных для динамического блока на %s не поступало." % date)

    hot = tool_detect_hot_topics({"date": date})["hot_topics"]
    lines += ["", "## 3. Сигналы для внимания аналитика", ""]
    if hot:
        lines += ["- **%s**: %s %s (%s)" % (h["name"], h["value"], h["unit"], h["reason"]) for h in hot]
    else:
        lines.append("Превышений порогов не зафиксировано.")

    lines += _web_section(date)

    missing = tool_check_completeness({"date": date})["missing_base"]
    lines += ["", "## 5. Полнота данных", ""]
    if missing:
        lines.append("Не поступили %d базовых показателей: %s"
                     % (len(missing), ", ".join(m["name"] for m in missing)))
    else:
        lines.append("Все базовые показатели собраны.")
    lines += ["", "---", "Черновик. Выводы и интерпретация — за аналитиком (human-in-the-loop)."]
    return {"date": date, "draft_markdown": "\n".join(lines)}


TOOLS = [
    {"name": "list_indicators",
     "description": "Каталог показателей для аналитических материалов (67 шт.). Фильтр block: base | dynamic | all.",
     "inputSchema": {"type": "object",
                     "properties": {"block": {"type": "string", "enum": ["base", "dynamic", "all"], "default": "all"}}}},
    {"name": "get_selection_criteria",
     "description": "Критерии отбора и окна отсечки показателей (за какие сутки брать, когда приходит источник).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "save_observation",
     "description": "Сохранить собранное значение показателя за дату.",
     "inputSchema": {"type": "object",
                     "properties": {"indicator_id": {"type": "integer"}, "date": {"type": "string"},
                                    "value": {"type": ["number", "string"]}, "unit": {"type": "string"},
                                    "source": {"type": "string"}},
                     "required": ["indicator_id", "date", "value"]}},
    {"name": "get_observations",
     "description": "Получить все собранные значения показателей за дату.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "check_completeness",
     "description": "Контроль полноты: какие базовые (обязательные) показатели ещё не собраны за дату.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "detect_hot_topics",
     "description": "Найти «горячие» темы за дату: показатели, превысившие пороги (КТД, инциденты, DDoS и др.).",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
    {"name": "fetch_web_sources",
     "description": "Собрать сигналы из открытых веб-источников (RSS из data/web_sources.json) за дату. Повторный запуск дособирает только новые публикации.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}}},
    {"name": "get_signals",
     "description": "Получить собранные веб-сигналы за дату. Фильтры: topic (finance | cyber | security_svo | tech_ai | emergency), limit.",
     "inputSchema": {"type": "object",
                     "properties": {"date": {"type": "string"}, "topic": {"type": "string"},
                                    "limit": {"type": "integer", "default": 50}}}},
    {"name": "build_report_draft",
     "description": "Собрать черновик аналитической справки за дату: базовый блок всегда, динамический — по наличию данных, плюс сигналы и контроль полноты.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}},
]

TOOL_IMPL = {
    "list_indicators": tool_list_indicators,
    "get_selection_criteria": tool_get_selection_criteria,
    "save_observation": tool_save_observation,
    "get_observations": tool_get_observations,
    "check_completeness": tool_check_completeness,
    "detect_hot_topics": tool_detect_hot_topics,
    "fetch_web_sources": tool_fetch_web_sources,
    "get_signals": tool_get_signals,
    "build_report_draft": tool_build_report_draft,
}


# --------------------------- JSON-RPC / MCP dispatch ---------------------------

def rpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

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
        try:
            result = impl(params.get("arguments") or {})
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "isError": False,
            })
        except Exception as exc:  # noqa: BLE001 - ошибку отдаём клиенту в явном виде
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": "Ошибка инструмента %s: %s" % (name, exc)}],
                "isError": True,
            })
    if req_id is None:  # notifications/initialized и прочие уведомления
        return None
    return rpc_error(req_id, -32601, "method not found: %s" % method)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "AgendaBriefingMCP/0.1"

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
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_json(400, rpc_error(None, -32700, "parse error"))
            return
        if isinstance(message, list):  # batch
            responses = [r for r in (handle_message(m) for m in message) if r is not None]
            if responses:
                self._send_json(200, responses)
            else:
                self.send_response(202)
                self.end_headers()
            return
        response = handle_message(message)
        if response is None:
            self.send_response(202)
            self.end_headers()
        else:
            self._send_json(200, response)

    def do_GET(self):
        # SSE-стрим не используется: сервер работает в простом JSON-режиме
        self._send_json(405, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32601, "message": "SSE not supported, use POST"}})

    def log_message(self, fmt, *args):
        print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), fmt % args))


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), MCPHandler)
    print("MCP-сервер agenda_briefing слушает http://%s:%d/mcp" % (HOST, PORT))
    print("Инструментов: %d. Каталог: %s" % (len(TOOLS), KNOWLEDGE_FILE))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
