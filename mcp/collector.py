# -*- coding: utf-8 -*-
"""Веб-коллектор открытых источников (RSS) для модуля мониторинга повестки.

Только стандартная библиотека Python 3.9+. Источники и тематические
словари — в data/web_sources.json. Результат — data/signals_<дата>.json
"""

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "data", "web_sources.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
USER_AGENT = "Mozilla/5.0 (compatible; agenda_briefing_agent/0.1; +local)"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def signals_path(date):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise ValueError("date должен быть в формате ГГГГ-ММ-ДД")
    return os.path.join(DATA_DIR, "signals_%s.json" % date)


def load_signals(date):
    path = signals_path(date)
    if not os.path.exists(path):
        return {"date": date, "signals": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_signals(date, payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(signals_path(date), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def clean_text(html):
    text = _TAG_RE.sub(" ", html or "")
    return _WS_RE.sub(" ", text).strip()


def fetch_url(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_rss(xml_bytes, max_items):
    """RSS 2.0 и Atom — возвращает список {title, link, published, summary}."""
    root = ET.fromstring(xml_bytes)
    items = []

    for item in root.iter("item"):
        def find(tag):
            el = item.find(tag)
            return el.text if el is not None and el.text else ""
        items.append({
            "title": clean_text(find("title")),
            "link": find("link").strip(),
            "published": find("pubDate").strip() or find("date").strip(),
            "summary": clean_text(find("description")),
        })
        if len(items) >= max_items:
            return items

    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        link_el = entry.find("a:link", ns)
        items.append({
            "title": clean_text((entry.findtext("a:title", default="", namespaces=ns))),
            "link": link_el.get("href", "") if link_el is not None else "",
            "published": entry.findtext("a:updated", default="", namespaces=ns),
            "summary": clean_text(entry.findtext("a:summary", default="", namespaces=ns)),
        })
        if len(items) >= max_items:
            break
    return items


def match_topics(text, topics):
    low = text.lower()
    return [topic for topic, keywords in topics.items()
            if any(kw in low for kw in keywords)]


def match_urgent(text, urgent_keywords):
    low = text.lower()
    return [kw.strip() for kw in urgent_keywords if kw in low]


def collect(date):
    """Обход всех включённых источников. Возвращает статистику сбора."""
    config = load_config()
    timeout = config.get("request_timeout_sec", 15)
    max_items = config.get("max_items_per_source", 100)

    store = load_signals(date)
    known_links = {s["link"] for s in store["signals"]}
    stats = {"date": date, "sources": [], "new_signals": 0, "errors": []}

    for src in config["sources"]:
        if not src.get("enabled", True):
            continue
        entry = {"id": src["id"], "name": src["name"], "fetched": 0, "new": 0}
        try:
            xml_bytes = fetch_url(src["url"], timeout)
            for item in parse_rss(xml_bytes, max_items):
                entry["fetched"] += 1
                if not item["link"] or item["link"] in known_links:
                    continue
                text = "%s %s" % (item["title"], item["summary"])
                topics = match_topics(text, config["topics"])
                urgent = match_urgent(text, config.get("urgent_keywords", []))
                store["signals"].append({
                    "source_id": src["id"],
                    "source_name": src["name"],
                    "title": item["title"],
                    "link": item["link"],
                    "published": item["published"],
                    "summary": item["summary"][:500],
                    "topics": topics,
                    "urgent": sorted(set(urgent)),
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                })
                known_links.add(item["link"])
                entry["new"] += 1
        except Exception as exc:  # noqa: BLE001 - источник недоступен, идём дальше
            stats["errors"].append({"source": src["id"], "error": str(exc)})
        stats["sources"].append(entry)

    store["signals"].sort(key=lambda s: s["collected_at"])
    store["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_signals(date, store)
    stats["new_signals"] = sum(s["new"] for s in stats["sources"])
    stats["total_signals"] = len(store["signals"])
    return stats
