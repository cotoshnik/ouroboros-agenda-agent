# -*- coding: utf-8 -*-
"""Экспорт черновика справки (markdown) в Word .docx.

Минимальный OOXML-писатель на чистой стандартной библиотеке (zipfile):
никаких внешних зависимостей — работает везде, где есть Python 3.9+.
Поддерживает структуру черновиков build_report_draft: заголовки #/##/###,
таблицы |...|, маркированные списки, **жирный**, разделители ---.
"""

import os
import re
import zipfile

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/>
<w:sz w:val="22"/><w:szCs w:val="22"/>
</w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:before="160" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:style>
</w:styles>"""

_DOCUMENT_TMPL = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
%s
<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>
</w:body>
</w:document>"""

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _runs(text, force_bold=False):
    """Разбивает строку на runs с учётом **жирного**."""
    out = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], force_bold))
        out.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], force_bold))
    xml = []
    for chunk, bold in out:
        if not chunk:
            continue
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        xml.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, _esc(chunk)))
    return "".join(xml) or '<w:r><w:t xml:space="preserve"></w:t></w:r>'


def _para(text, style=None):
    ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
    return "<w:p>%s%s</w:p>" % (ppr, _runs(text))


def _cell(text, bold=False, width=4500):
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>'
            "<w:p>%s</w:p></w:tc>" % (width, _runs(text, force_bold=bold)))


def _table(rows):
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    width = max(2000, 9000 // ncols)
    borders = ("<w:tblBorders>" + "".join(
        '<w:%s w:val="single" w:sz="4" w:space="0" w:color="808080"/>' % side
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>")
    xml = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>%s</w:tblPr>' % borders]
    for ri, row in enumerate(rows):
        row = row + [""] * (ncols - len(row))
        xml.append("<w:tr>" + "".join(_cell(c, bold=(ri == 0), width=width) for c in row) + "</w:tr>")
    xml.append("</w:tbl>")
    xml.append("<w:p/>")
    return "".join(xml)


def _is_separator_row(cells):
    return all(set(c) <= set("-: ") and c for c in cells)


def markdown_to_docx(md_text, out_path, title=None):
    body = []
    if title:
        body.append(_para(title, "Heading1"))
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not _is_separator_row(cells):
                    rows.append(cells)
                i += 1
            body.append(_table(rows))
            continue

        if stripped.startswith("### "):
            body.append(_para(stripped[4:], "Heading3"))
        elif stripped.startswith("## "):
            body.append(_para(stripped[3:], "Heading2"))
        elif stripped.startswith("# "):
            body.append(_para(stripped[2:], "Heading1"))
        elif stripped.startswith("- "):
            body.append(_para("•  " + stripped[2:]))
        elif stripped and set(stripped) <= set("-") and len(stripped) >= 3:
            pass  # горизонтальный разделитель пропускаем
        elif stripped:
            body.append(_para(stripped))
        i += 1

    document = _DOCUMENT_TMPL % "\n".join(body)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/document.xml", document)
    return out_path
