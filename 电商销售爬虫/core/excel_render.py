# -*- coding: utf-8 -*-
"""
月度汇总 Excel 生成器。
从主数据读取，按「周一~周日」周块生成：
  - Sheet「各平台销售汇总」：宽表，4 指标 × (淘宝/抖音/拼多多/小红书/总计)
  - 每个平台一张窄表：日期/销售/退款/净销售额/支付订单数
净销售额、平台总计列、周总计、当月汇总全部用公式联动。
跨月周完整保留 7 天；当月汇总只加归属本月的日期行。
见 docs/逻辑规则.md §4/§5。
"""
from __future__ import annotations
import os
import datetime as _dt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from . import calendar_utils as cal

# ---------- 样式 ----------
_THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
TITLE_FONT = Font(bold=True, size=14, color="FFFFFF")
TITLE_FILL = PatternFill("solid", fgColor="4472C4")
HEAD_FONT = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="5B9BD5")
SUB_FILL = PatternFill("solid", fgColor="DEEBF7")
SUM_FILL = PatternFill("solid", fgColor="FFF2CC")      # 周总计
MONTH_FILL = PatternFill("solid", fgColor="FCE4D6")    # 当月汇总
BLANK_FILL = PatternFill("solid", fgColor="F2F2F2")    # 留空项(扣除仅退款/退货率/增长率)
CENTER = Alignment(horizontal="center", vertical="center")
# 金额单元格：显示成整数(#,##0)，但存的仍是完整精度值——点中方格时公式栏会显示真实的小数值。
MONEY_FMT = "#,##0"
INT_FMT = "#,##0"
DATE_FMT = "m月d日"

# 每个周块后面的固定统计行
BLOCK_TAIL = ["总计", "扣除仅退款", "退货率", "增长率"]
BLANK_ROWS = {"扣除仅退款", "退货率", "增长率"}  # 永远留空


def _num(cell, value, fmt, formula=False):
    if formula:
        cell.value = value
    elif value is not None:
        cell.value = value
    cell.number_format = fmt
    cell.border = BORDER
    cell.alignment = CENTER


def build_month_workbook(year: int, month: int, master, platforms):
    wb = Workbook()
    wb.remove(wb.active)
    blocks = cal.month_week_blocks(year, month)

    _render_summary(wb, year, month, master, platforms, blocks)
    for p in platforms:
        _render_platform(wb, year, month, master, p, blocks)
    return wb


# ============ 宽表：各平台销售汇总 ============
def _render_summary(wb, year, month, master, platforms, blocks):
    ws = wb.create_sheet("各平台销售汇总")
    np = len(platforms)                      # 平台数
    group_w = np + 1                         # 每个指标组宽度(含总计列)
    groups = ["销售（支付金额）", "退款（退款金额）", "净销售额 = 销售 - 退款", "支付订单数"]
    # 列布局：A=日期; 每组 group_w 列
    # 组起始列(1-based): 销售=2, 退款=2+gw, 净额=2+2gw, 订单=2+3gw
    def group_start(gi):
        return 2 + gi * group_w

    last_col = group_start(3) + group_w - 1

    # --- row1 大标题 ---
    ws.cell(1, 1)
    c = ws.cell(1, 2, "各平台销售业绩")
    c.font = TITLE_FONT; c.fill = TITLE_FILL; c.alignment = CENTER
    ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=last_col)
    for col in range(2, last_col + 1):
        ws.cell(1, col).fill = TITLE_FILL

    # --- row2 指标组表头(合并) / row3 平台列 ---
    a2 = ws.cell(2, 1, "日期"); a2.font = HEAD_FONT; a2.fill = HEAD_FILL; a2.alignment = CENTER; a2.border = BORDER
    a3 = ws.cell(3, 1, ""); a3.fill = HEAD_FILL; a3.border = BORDER
    ws.merge_cells("A2:A3")
    sub_labels = platforms + ["总计"]
    for gi, gname in enumerate(groups):
        gs = group_start(gi)
        ge = gs + group_w - 1
        h = ws.cell(2, gs, gname); h.font = HEAD_FONT; h.fill = HEAD_FILL; h.alignment = CENTER
        ws.merge_cells(start_row=2, start_column=gs, end_row=2, end_column=ge)
        for col in range(gs, ge + 1):
            ws.cell(2, col).fill = HEAD_FILL; ws.cell(2, col).border = BORDER
        for j, lab in enumerate(sub_labels):
            cc = ws.cell(3, gs + j, lab); cc.font = Font(bold=True); cc.fill = SUB_FILL
            cc.alignment = CENTER; cc.border = BORDER

    # 组内列号助手
    def col_sales(pi):  return group_start(0) + pi
    def col_refund(pi): return group_start(1) + pi
    def col_net(pi):    return group_start(2) + pi
    def col_ord(pi):    return group_start(3) + pi
    def col_total(gi):  return group_start(gi) + np  # 该组“总计”列

    r = 4
    month_rows = []          # 归属本月的日期行号
    for block in blocks:
        day_rows = []
        for d in block:
            in_m = cal.in_month(d, year, month)
            a = ws.cell(r, 1, d)
            a.number_format = DATE_FMT; a.border = BORDER; a.alignment = CENTER
            if not in_m:
                a.font = Font(color="A6A6A6")   # 非本月的天，日期灰显
            for pi, p in enumerate(platforms):
                row = master.get(d, p)
                sv = row["sales"] if row else None
                rv = row["refund"] if row else None
                ov = row["orders"] if row else None
                cs = get_column_letter(col_sales(pi))
                cr = get_column_letter(col_refund(pi))
                _num(ws.cell(r, col_sales(pi)), sv, MONEY_FMT)
                _num(ws.cell(r, col_refund(pi)), rv, MONEY_FMT)
                # 净额=销售-退款（有任一数据才写公式）
                if row:
                    _num(ws.cell(r, col_net(pi)), f"={cs}{r}-{cr}{r}", MONEY_FMT, formula=True)
                else:
                    _num(ws.cell(r, col_net(pi)), None, MONEY_FMT)
                _num(ws.cell(r, col_ord(pi)), ov, INT_FMT)
            # 每组“总计”列 = 4 平台之和
            _row_group_totals(ws, r, group_start, np, group_w)
            day_rows.append(r)
            if in_m:
                month_rows.append(r)
            r += 1

        # --- 周块尾部 4 行 ---
        for tail in BLOCK_TAIL:
            a = ws.cell(r, 1, tail); a.font = Font(bold=True); a.alignment = CENTER; a.border = BORDER
            if tail == "总计":
                a.fill = SUM_FILL
                for col in range(2, last_col + 1):
                    letter = get_column_letter(col)
                    f = f"=SUM({letter}{day_rows[0]}:{letter}{day_rows[-1]})"
                    _num(ws.cell(r, col), f, _fmt_for_col(col, group_start, np, group_w), formula=True)
                    ws.cell(r, col).fill = SUM_FILL
            else:
                for col in range(2, last_col + 1):
                    cc = ws.cell(r, col); cc.border = BORDER; cc.fill = BLANK_FILL
            r += 1

    # --- 当月汇总（只加归属本月的日期行）---
    a = ws.cell(r, 1, "当月汇总"); a.font = Font(bold=True); a.alignment = CENTER; a.border = BORDER; a.fill = MONTH_FILL
    for col in range(2, last_col + 1):
        letter = get_column_letter(col)
        if month_rows:
            f = "=" + "+".join(f"{letter}{rr}" for rr in month_rows)
        else:
            f = None
        _num(ws.cell(r, col), f, _fmt_for_col(col, group_start, np, group_w), formula=bool(f))
        ws.cell(r, col).fill = MONTH_FILL

    # --- 周报（该月所有「已结束且有数据」的完整周，一句话 + 净额环比）---
    _render_weekly_report(ws, year, month, master, platforms, last_col, start_row=r + 2)

    # 列宽
    ws.column_dimensions["A"].width = 11
    for col in range(2, last_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 12
    ws.freeze_panes = "B4"


def _render_weekly_report(ws, year, month, master, platforms, last_col, start_row):
    """在「各平台销售汇总」sheet 最下方渲染周报区块（标题行 + 每周一行，整行合并、自动换行）。"""
    from . import weekly_report
    lines = weekly_report.weekly_lines_for_month(master, year, month, platforms)
    if not lines:
        return
    r = start_row
    # 标题行
    h = ws.cell(r, 1, "周报（一句话 + 净额环比）")
    h.font = HEAD_FONT; h.fill = HEAD_FILL; h.alignment = CENTER; h.border = BORDER
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
    for col in range(1, last_col + 1):
        ws.cell(r, col).fill = HEAD_FILL; ws.cell(r, col).border = BORDER
    r += 1
    # 每周一行：整行合并 + 左对齐 + 自动换行
    left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)
    for (_monday, line) in lines:
        c = ws.cell(r, 1, line)
        c.alignment = left_wrap; c.border = BORDER
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
        for col in range(1, last_col + 1):
            ws.cell(r, col).border = BORDER
        ws.row_dimensions[r].height = 42
        r += 1


def _row_group_totals(ws, r, group_start, np, group_w):
    """写某数据行每个指标组的“总计”列(=4平台之和)。净额组总计=销售总计-退款总计更稳，这里直接SUM4平台。"""
    for gi in range(4):
        gs = group_start(gi)
        total_col = gs + np
        first = get_column_letter(gs)
        last = get_column_letter(gs + np - 1)
        fmt = INT_FMT if gi == 3 else MONEY_FMT
        # 只有该行有任意平台值时才给总计公式；否则留空
        has = any(ws.cell(r, gs + k).value not in (None, "") for k in range(np))
        if has:
            _num(ws.cell(r, total_col), f"=SUM({first}{r}:{last}{r})", fmt, formula=True)
        else:
            _num(ws.cell(r, total_col), None, fmt)


def _fmt_for_col(col, group_start, np, group_w):
    """订单数组用整数格式，其余货币格式。"""
    order_start = group_start(3)
    return INT_FMT if col >= order_start else MONEY_FMT


# ============ 窄表：单平台 ============
def _render_platform(wb, year, month, master, platform, blocks):
    ws = wb.create_sheet(platform)
    # row1 标题
    c = ws.cell(1, 1, f"{platform}平台销售业绩")
    c.font = TITLE_FONT; c.fill = TITLE_FILL; c.alignment = CENTER
    ws.merge_cells("A1:E1")
    for col in range(1, 6):
        ws.cell(1, col).fill = TITLE_FILL
    # row2 表头
    heads = ["日期", "销售（支付金额）", "退款（退款金额）", "净销售额 = 销售 - 退款", "支付订单数"]
    for j, h in enumerate(heads, start=1):
        cc = ws.cell(2, j, h); cc.font = HEAD_FONT; cc.fill = HEAD_FILL; cc.alignment = CENTER; cc.border = BORDER

    r = 3
    month_rows = []
    for block in blocks:
        day_rows = []
        for d in block:
            in_m = cal.in_month(d, year, month)
            a = ws.cell(r, 1, d); a.number_format = DATE_FMT; a.border = BORDER; a.alignment = CENTER
            if not in_m:
                a.font = Font(color="A6A6A6")
            row = master.get(d, platform)
            _num(ws.cell(r, 2), row["sales"] if row else None, MONEY_FMT)
            _num(ws.cell(r, 3), row["refund"] if row else None, MONEY_FMT)
            if row:
                _num(ws.cell(r, 4), f"=B{r}-C{r}", MONEY_FMT, formula=True)
            else:
                _num(ws.cell(r, 4), None, MONEY_FMT)
            _num(ws.cell(r, 5), row["orders"] if row else None, INT_FMT)
            day_rows.append(r)
            if in_m:
                month_rows.append(r)
            r += 1
        for tail in BLOCK_TAIL:
            a = ws.cell(r, 1, tail); a.font = Font(bold=True); a.alignment = CENTER; a.border = BORDER
            if tail == "总计":
                a.fill = SUM_FILL
                for col in range(2, 6):
                    letter = get_column_letter(col)
                    fmt = INT_FMT if col == 5 else MONEY_FMT
                    _num(ws.cell(r, col), f"=SUM({letter}{day_rows[0]}:{letter}{day_rows[-1]})", fmt, formula=True)
                    ws.cell(r, col).fill = SUM_FILL
            else:
                for col in range(2, 6):
                    cc = ws.cell(r, col); cc.border = BORDER; cc.fill = BLANK_FILL
            r += 1
    # 当月汇总
    a = ws.cell(r, 1, "当月汇总"); a.font = Font(bold=True); a.alignment = CENTER; a.border = BORDER; a.fill = MONTH_FILL
    for col in range(2, 6):
        letter = get_column_letter(col)
        fmt = INT_FMT if col == 5 else MONEY_FMT
        f = ("=" + "+".join(f"{letter}{rr}" for rr in month_rows)) if month_rows else None
        _num(ws.cell(r, col), f, fmt, formula=bool(f))
        ws.cell(r, col).fill = MONTH_FILL

    ws.column_dimensions["A"].width = 11
    for col in "BCDE":
        ws.column_dimensions[col].width = 16
    ws.freeze_panes = "A3"


# ============ 对外入口 ============
def month_file_path(output_dir: str, year: int, month: int) -> str:
    return os.path.join(output_dir, f"{year}年{month:02d}月各平台销售汇总.xlsx")


def render_month_file(output_dir: str, year: int, month: int, master, platforms) -> str:
    wb = build_month_workbook(year, month, master, platforms)
    path = month_file_path(output_dir, year, month)
    os.makedirs(output_dir, exist_ok=True)
    try:
        wb.save(path)
        return path
    except PermissionError:
        # 月表被 Excel/WPS 打开占用：改存带时间戳的备用文件，避免数据丢失
        import datetime
        alt = path[:-5] + "_请关闭原文件后替换_" + datetime.datetime.now().strftime("%H%M%S") + ".xlsx"
        wb.save(alt)
        return alt
