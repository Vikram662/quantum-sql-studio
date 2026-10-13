"""Minimal .xlsx writer (no third-party dependency): one sheet, bold frozen header, autofilter."""
import datetime
import decimal
import zipfile
from xml.sax.saxutils import escape

from .db import to_text


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _clean(text):
    # XML 1.0 forbids most control characters
    return escape("".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 32))[:32767]


def _cell(ref, v, style=0):
    st = f' s="{style}"' if style else ""
    if v is None:
        return f'<c r="{ref}"{st}/>'
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"{st}><v>{int(v)}</v></c>'
    if isinstance(v, (int, float, decimal.Decimal)) and not (isinstance(v, float) and v != v):
        return f'<c r="{ref}"{st}><v>{v}</v></c>'
    if isinstance(v, (datetime.datetime, datetime.date)):
        base = datetime.datetime(1899, 12, 30)
        dt = v if isinstance(v, datetime.datetime) else datetime.datetime(v.year, v.month, v.day)
        serial = (dt - base).total_seconds() / 86400
        return f'<c r="{ref}" s="{2 if isinstance(v, datetime.datetime) else 3}"><v>{serial:.8f}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{_clean(to_text(v))}</t></is></c>'


def write_xlsx(path, headers, rows, sheet="Data"):
    sheet = _clean(sheet)[:31] or "Data"
    last = _col(max(0, len(headers) - 1))
    widths = [min(60, max(8, len(str(h)) + 2)) for h in headers]
    for r in rows[:200]:
        for i, v in enumerate(r[:len(widths)]):
            widths[i] = min(60, max(widths[i], len(to_text(v)[:60]) + 2))
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews><cols>']
    out += [f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)]
    out.append("</cols><sheetData>")
    out.append('<row r="1">' + "".join(_cell(f"{_col(i)}1", str(h), 1) for i, h in enumerate(headers)) + "</row>")
    for n, r in enumerate(rows, start=2):
        out.append(f'<row r="{n}">' + "".join(_cell(f"{_col(i)}{n}", v) for i, v in enumerate(r)) + "</row>")
    out.append(f'</sheetData><autoFilter ref="A1:{last}{max(1, len(rows) + 1)}"/></worksheet>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets>'
        f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
        f"'{sheet}'!$A$1:${last}${max(1, len(rows) + 1)}</definedName></definedNames></workbook>",
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/>'
        '</fill><fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF9"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="4"><xf/><xf fontId="1" fillId="2" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="164" applyNumberFormat="1"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs>'
        '</styleSheet>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("xl/worksheets/sheet1.xml", "".join(out))
