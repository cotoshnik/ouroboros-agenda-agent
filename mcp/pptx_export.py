# -*- coding: utf-8 -*-
"""Формирование презентации справки (PPTX) на основе шаблона пользователя.

Принципы:
- итоговый файл строится ИЗ шаблона (Presentation(template_path)): мастер,
  макеты, тема, шрифты, цвета, логотипы и существующие слайды сохраняются;
- пустой Presentation() не используется, собственный дизайн не создаётся;
- данные — только фактические, проверенные сервером показатели (см. server.py);
- значения показателей не изменяются и не сокращаются никогда; сокращаться
  могут только пояснения (обоснования).

Документированный маппинг (шаблон произвольный, заранее неизвестен):
- Слайд 1 (титульный): первый слайд шаблона; заполняются плейсхолдеры
  TITLE/CENTER_TITLE (заголовок справки) и SUBTITLE (дата + пометка ЧЕРНОВИК),
  если они есть. Если слайдов нет — добавляется слайд по макету с TITLE.
- Содержательные слайды: макет с плейсхолдерами TITLE + BODY/OBJECT.
  Один слайд на отобранный показатель: заголовок = название показателя,
  тело = значение+единица, дата, источник, обоснование.
- Финальный слайд: «Полнота данных» — общий статус полноты базового блока.
- Пустые плейсхолдеры на созданных слайдах удаляются (не остаётся
  «Click to add text»).
"""

import xml.etree.ElementTree as ET
import zipfile

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.util import Emu, Pt

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

MAX_JUSTIFICATION = 300   # сокращаются только пояснения, не значения
MAX_MISSING_LIST = 10
TITLE_FONT_LONG = 80      # порог длины названия для уменьшения шрифта


class TemplateError(Exception):
    """Некорректный или непригодный шаблон (без внутренних деталей наружу)."""


# --------------------------- анализ шаблона ---------------------------

def inspect_template(path):
    """Структурный разбор шаблона: слайды, макеты, плейсхолдеры, тема."""
    prs = Presentation(path)
    info = {
        "slide_count": len(prs.slides),
        "slide_width_cm": round(prs.slide_width / 360000, 1),
        "slide_height_cm": round(prs.slide_height / 360000, 1),
        "layouts": [],
        "theme": {},
    }
    for i, layout in enumerate(prs.slide_layouts):
        placeholders = []
        for ph in layout.placeholders:
            placeholders.append({
                "idx": ph.placeholder_format.idx,
                "type": str(ph.placeholder_format.type),
                "name": ph.name,
            })
        info["layouts"].append({"index": i, "name": layout.name,
                                "placeholders": placeholders})
    try:
        for part in prs.part.package.iter_parts():
            if "theme" in str(part.partname):
                root = ET.fromstring(part.blob)
                for font_kind in ("majorFont", "minorFont"):
                    el = root.find(".//{%s}%s/{%s}latin" % (A_NS, font_kind, A_NS))
                    if el is not None:
                        info["theme"][font_kind] = el.get("typeface", "")
                colors = {}
                scheme = root.find(".//{%s}clrScheme" % A_NS)
                if scheme is not None:
                    for child in scheme:
                        tag = child.tag.split("}")[-1]
                        srgb = child.find("{%s}srgbClr" % A_NS)
                        if srgb is not None:
                            colors[tag] = srgb.get("val", "")
                info["theme"]["colors"] = colors
                break
    except Exception:
        info["theme"] = {"note": "тема не разобрана, на рендер не влияет"}
    return info


# --------------------------- помощники маппинга ---------------------------

def _find_placeholder(slide, types):
    for ph in slide.placeholders:
        if ph.placeholder_format.type in types:
            return ph
    return None


def _content_layout(prs):
    """Макет с TITLE и BODY/OBJECT; fallback — макет с TITLE."""
    title_types = (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
    body_types = (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT)
    fallback = None
    for layout in prs.slide_layouts:
        types = {ph.placeholder_format.type for ph in layout.placeholders}
        if any(t in types for t in title_types):
            if any(t in types for t in body_types):
                return layout
            if fallback is None:
                fallback = layout
    if fallback is not None:
        return fallback
    raise TemplateError("в шаблоне нет макета с плейсхолдером заголовка — "
                        "добавьте такой макет в шаблон")


def _remove_empty_placeholders(slide, keep):
    for ph in list(slide.placeholders):
        if ph.placeholder_format.idx not in keep:
            ph._element.getparent().remove(ph._element)


def _set_title(slide, text):
    ph = _find_placeholder(slide, (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE))
    if ph is None:
        return None
    tf = ph.text_frame
    tf.word_wrap = True
    tf.clear()
    run = tf.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(20 if len(text) > TITLE_FONT_LONG else 24)
    run.font.bold = True
    return ph


def _fill_body(slide, lines):
    """lines: список (текст, размер_pt, bold). Возвращает placeholder или None."""
    ph = _find_placeholder(slide, (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT))
    if ph is None:
        # в макете нет тела — добавляем текстовый блок в пределах слайда
        left = Emu(int(slide.part.package.presentation_part.presentation.slide_width * 0.07))
        top = Emu(2200000)
        width = Emu(int(slide.part.package.presentation_part.presentation.slide_width * 0.86))
        height = Emu(3600000)
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
    else:
        tf = ph.text_frame
    tf.word_wrap = True
    tf.clear()
    for i, (text, size, bold) in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
    return ph


# --------------------------- рендер и валидация ---------------------------

def render_pptx(template_path, out_path, date, items, completeness):
    """Собирает презентацию из шаблона.

    items — проверенные сервером отобранные показатели:
    {indicator_id, name, block, value, unit, source, priority, justification}.
    completeness — результат check_completeness.
    """
    prs = Presentation(template_path)

    # --- титульный слайд ---
    if len(prs.slides) > 0:
        title_slide = prs.slides[0]
    else:
        title_layout = None
        for layout in prs.slide_layouts:
            if any(ph.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
                   for ph in layout.placeholders):
                title_layout = layout
                break
        if title_layout is None:
            raise TemplateError("в шаблоне нет ни слайдов, ни макета с заголовком")
        title_slide = prs.slides.add_slide(title_layout)

    keep = set()
    title_ph = _set_title(title_slide, "Аналитическая справка дежурной смены")
    if title_ph is not None:
        keep.add(title_ph.placeholder_format.idx)
    subtitle = _find_placeholder(title_slide, (PP_PLACEHOLDER.SUBTITLE,))
    if subtitle is not None:
        tf = subtitle.text_frame
        tf.clear()
        run = tf.paragraphs[0].add_run()
        run.text = "%s — ЧЕРНОВИК (требует верификации аналитиком)" % date
        keep.add(subtitle.placeholder_format.idx)
    _remove_empty_placeholders(title_slide, keep)

    # --- содержательные слайды: один отобранный показатель = один слайд ---
    layout = _content_layout(prs)
    for item in items:
        slide = prs.slides.add_slide(layout)
        keep = set()
        title_ph = _set_title(slide, item["name"])
        if title_ph is not None:
            keep.add(title_ph.placeholder_format.idx)
        value_line = ("%s %s" % (item["value"], item.get("unit", ""))).strip()
        justification = item.get("justification", "")[:MAX_JUSTIFICATION]
        body_ph = _fill_body(slide, [
            (value_line, 32, True),
            ("Дата: %s" % date, 16, False),
            ("Источник: %s" % (item.get("source") or "—"), 16, False),
            ("Основание включения: %s" % justification, 14, False),
        ])
        if body_ph is not None:
            keep.add(body_ph.placeholder_format.idx)
        _remove_empty_placeholders(slide, keep)

    # --- финальный слайд: статус полноты данных ---
    summary = prs.slides.add_slide(layout)
    keep = set()
    title_ph = _set_title(summary, "Полнота данных")
    if title_ph is not None:
        keep.add(title_ph.placeholder_format.idx)
    missing = completeness["missing_base"]
    lines = [
        ("Собрано базовых показателей: %d из %d"
         % (completeness["collected_base"], completeness["base_total"]), 20, True),
    ]
    if missing:
        names = [m["name"] for m in missing[:MAX_MISSING_LIST]]
        tail = "" if len(missing) <= MAX_MISSING_LIST else " и ещё %d" % (len(missing) - MAX_MISSING_LIST)
        lines.append(("Нет данных: %s%s" % (", ".join(names), tail), 14, False))
    else:
        lines.append(("Все базовые показатели собраны.", 16, False))
    lines.append(("Статус: ЧЕРНОВИК — требуется верификация аналитиком (human-in-the-loop).", 14, False))
    body_ph = _fill_body(summary, lines)
    if body_ph is not None:
        keep.add(body_ph.placeholder_format.idx)
    _remove_empty_placeholders(summary, keep)

    prs.save(out_path)
    return out_path


def validate_pptx(path):
    """Проверка готового файла: ZIP/OOXML целостность + открытие + есть слайды."""
    if not zipfile.is_zipfile(path):
        raise TemplateError("PPTX не является валидным ZIP/OOXML-пакетом")
    try:
        prs = Presentation(path)
    except Exception:
        raise TemplateError("PPTX не открывается python-pptx (файл повреждён)")
    if len(prs.slides) == 0:
        raise TemplateError("PPTX не содержит ни одного слайда")
    return True
