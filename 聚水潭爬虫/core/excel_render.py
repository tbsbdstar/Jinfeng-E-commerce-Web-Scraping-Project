# -*- coding: utf-8 -*-
"""
从主数据渲染『每周各渠道实发/实退』Excel。

- 产品列 = 分类编码里的全部产品名称（数据驱动，增删产品自动跟随，永不脱节）
- 实发数量 sheet：渠道×日期网格(实发) + 实发数量 + 实退数量 + 净发货（总计）
  底部三行口径（2026-09-10 业务方确认后改；行序也按业务方要求排成 实发→实退→净发货）：
      实发数量 = 网格求和 = 报表『实发数量』列（本周实际发出）
      实退数量 = 引用『实退数量』sheet 周合计（本周退货入库）
      净发货（总计） = 实发数量 − 实退数量，与周报里的『净发货』同口径
  历史口径变迁：最早「总计=网格求和、实发=总计-实退」→ 2026-09-02 改成「总计=实发+实退」
  → 2026-09-10 业务方定为「净发货（总计）= 实发 − 实退」。实退>实发的产品会出现负数，
  这是净发货口径下的正常结果（如第36周三代马卡龙扶手箱 -1）。
- 实退数量 sheet：渠道×日期网格(实退) + 实退数量(周合计)
- 分类编码 sheet 内容原样保留
- 三个 sheet 的列宽都按内容自适应（_autofit，中日韩字符按 2 个字宽算）
- 空缺一律写 0（不留空）
"""
from __future__ import annotations
import os
import datetime as _dt
import unicodedata as _ea
from typing import List
import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Side, Font, PatternFill
from . import calendar_utils as cal
from . import category

_CENTER = Alignment(horizontal="center", vertical="center")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HFILL = PatternFill("solid", fgColor="E2EFDA")
_SFILL = PatternFill("solid", fgColor="FFF2CC")
_BOLD = Font(bold=True)


def _label(ch: str, suffix: str) -> str:
    return f"{ch}-{suffix}" if suffix else ch


def _fmt_date(d) -> str:
    d = cal.parse_date(d)
    return f"{d.year}年{d.month}月{d.day}日"


def _build_sheet(ws, products, channels, suffix, week_dates, master, metric, summary):
    """metric='shipped'|'returned'；summary 控制底部汇总行的构建。"""
    ncol = 2 + len(products)                 # A渠道 B日期 + 产品列
    # 解除旧合并、清空
    for m in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(m))
    ws.delete_rows(1, ws.max_row + 5)
    # 表头
    hdr = ["渠道", "日期"] + list(products)
    for c, v in enumerate(hdr, 1):
        cell = ws.cell(1, c, v)
        cell.font = _BOLD; cell.alignment = _CENTER; cell.fill = _HFILL; cell.border = _BORDER
    # 网格：每渠道 7 天
    r = 2
    for ch in channels:
        start = r
        for d in week_dates:
            ws.cell(r, 1, _label(ch, suffix)).alignment = _CENTER
            dc = ws.cell(r, 2, _fmt_date(d)); dc.alignment = _CENTER; dc.border = _BORDER
            for j, prod in enumerate(products):
                v = master.qty(d, ch, prod, metric)     # 缺失=0
                cell = ws.cell(r, 3 + j, v)
                cell.alignment = _CENTER; cell.border = _BORDER
            ws.cell(r, 1).border = _BORDER
            r += 1
        ws.merge_cells(start_row=start, start_column=1, end_row=r - 1, end_column=1)
        ws.cell(start, 1).alignment = _CENTER
    last_grid = r - 1                          # 网格最后一行(=29)

    # 底部汇总
    def sum_formula(col_letter):
        return f"=SUM({col_letter}2:{col_letter}{last_grid})"

    for row_label, kind in summary:            # summary: [(label, kind)]
        for c in range(1, ncol + 1):
            ws.cell(r, c).border = _BORDER
            ws.cell(r, c).fill = _SFILL
        lab = ws.cell(r, 1, row_label); lab.font = _BOLD; lab.alignment = _CENTER
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        for j in range(len(products)):
            L = get_column_letter(3 + j)
            if kind == "sum":
                f = sum_formula(L)
            elif kind == "ref_return_total":      # 引用『实退数量』sheet 的周合计行
                f = f"='实退数量'!{L}{RETURN_TOTAL_ROW}"
            elif kind == "net_above":             # 净发货(总计) = 实发 - 实退（上面第2行 - 第1行）
                f = f"={L}{r-2}-{L}{r-1}"
            else:
                f = 0
            ws.cell(r, 3 + j, f).alignment = _CENTER
        r += 1
    # 列宽统一交给 render() 末尾的 _autofit（含『分类编码』sheet）


def _disp_len(v) -> int:
    """显示宽度：中文/全角算 2 个字符宽，其余算 1（Excel 列宽单位≈半角字符数）。"""
    return sum(2 if _ea.east_asian_width(ch) in ("W", "F") else 1 for ch in str(v))


def _autofit(ws, min_w=8, max_w=42):
    """按内容自适应列宽。
    - 跳过**横向合并**区域的左上格：『本周小结』那一长串合并在 C..M，按它算列宽会把 C 列撑爆；
      标题行 A:B 的合并同理。纵向合并(渠道名)不受影响，正常参与计算。
    - 跳过公式格（存的是 '=...' 文本，显示出来的是短数字，按公式文本算会过宽）。"""
    skip = {(rng.min_row, rng.min_col) for rng in ws.merged_cells.ranges
            if rng.min_col != rng.max_col}
    widths = {}
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if v is None or (cell.row, cell.column) in skip:
                continue
            if isinstance(v, str) and v.startswith("="):
                continue
            w = _disp_len(v)
            if w > widths.get(cell.column, 0):
                widths[cell.column] = w
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = min(max(min_w, w + 2), max_w)


# 实退 sheet 网格 28 行 -> 周合计在第 30 行
RETURN_TOTAL_ROW = 30


def render(template_path: str, out_path: str, master, week_monday,
           channels: List[str], suffix: str, note: str = None):
    products = category.product_names(template_path)   # 顺序 = 分类编码里的顺序
    week_dates = cal.week_days(cal.parse_date(week_monday))
    wb = openpyxl.load_workbook(template_path)          # 保留 分类编码 sheet
    ws_send = wb["实发数量"]; ws_ret = wb["实退数量"]

    # 实退：网格(returned) + 周合计『实退数量』
    _build_sheet(ws_ret, products, channels, suffix, week_dates, master,
                 metric="returned", summary=[("实退数量", "sum")])
    # 实发：网格(shipped) + 实发数量 + 实退数量(引用实退sheet) + 净发货（总计）
    _build_sheet(ws_send, products, channels, suffix, week_dates, master,
                 metric="shipped",
                 # 行序 = 实发 → 实退 → 净发货（2026-09-10 业务方要求，原来净发货在最上面）
                 summary=[("实发数量", "sum"), ("实退数量", "ref_return_total"),
                          ("净发货（总计）", "net_above")])

    # 一句话周报：写进『实发数量』sheet 汇总行下方（流程要求）
    if note:
        ncol = 2 + len(products)
        note_row = ws_send.max_row + 2          # 汇总行(32)下空一行
        lab = ws_send.cell(note_row, 1, "本周小结")
        lab.font = _BOLD; lab.alignment = _CENTER
        cell = ws_send.cell(note_row, 3, note)
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws_send.merge_cells(start_row=note_row, start_column=3, end_row=note_row, end_column=ncol)
        ws_send.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=2)
        ws_send.row_dimensions[note_row].height = 15 * (note.count("\n") + 2)

    # 三个 sheet 都自适应列宽（『分类编码』是原样保留的，也一并调）
    for _s in wb.worksheets:
        _autofit(_s)

    # 打开时默认停在『实发数量』sheet（激活 + 仅它被选中 + 光标回 A1）
    send_idx = wb.sheetnames.index("实发数量")
    wb.active = send_idx
    for s in wb.worksheets:
        sel = (s.title == "实发数量")
        s.sheet_view.tabSelected = sel
    ws_send.sheet_view.topLeftCell = "A1"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        wb.save(out_path)
        return out_path
    except PermissionError:
        alt = out_path.replace(".xlsx", f"_请关闭原文件后替换_{_dt.datetime.now():%H%M%S}.xlsx")
        wb.save(alt)
        return alt


def week_out_name(week_monday) -> str:
    m = cal.parse_date(week_monday); sun = m + _dt.timedelta(days=6)
    y, w, _ = m.isocalendar()
    return f"每周各渠道实发_实退数据_{y}年第{w:02d}周({m.month:02d}{m.day:02d}-{sun.month:02d}{sun.day:02d}).xlsx"
