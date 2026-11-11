# -*- coding: utf-8 -*-
"""周报生成：一句话 + 环比（净销售额口径）。见 docs/逻辑规则.md §5.2。
- 周 = 周一~周日。只对「已结束的完整周」生成（周日 < 今天）。
- 环比：本周净销售额 vs 上一周（周一-7）净销售额；整体与各平台均按净额算。
- 首周（上一周在主数据里没有任何数据）→ 不输出对比句。
- 两处消费：写文本文件(outputs/周报/周报_YYYY-Www_MMDD-MMDD.md) + 嵌入月表「各平台销售汇总」sheet 最下方。
"""
from __future__ import annotations
import os
import datetime as _dt
from . import calendar_utils as cal

ONE_DAY = _dt.timedelta(days=1)


def _money(x: float) -> str:
    """金额格式化：整数元（不显示小数点），带千分位。"""
    return f"{x:,.0f}元"


def _pct_phrase(cur: float, prev: float, flat_threshold: float = 2.0):
    """环比措辞。prev<=0 时无法算百分比：cur>0→'新增'，否则 None(不可比)。
    |变化|<阈值→'持平'；否则'增长X%'/'下降X%'(整数)。"""
    if prev <= 0:
        return "新增" if cur > 0 else None
    change = (cur - prev) / prev * 100.0
    if abs(change) < flat_threshold:
        return "持平"
    return (f"增长{round(abs(change))}%" if change > 0 else f"下降{round(abs(change))}%")


def week_totals(master, monday: _dt.date, platforms):
    """某周(周一起7天)各平台 + 全平台的合计。返回 {'total':{...}, 'by':{平台:{...}}}。"""
    sunday = monday + 6 * ONE_DAY
    by = {}
    tot_sales = tot_refund = 0.0
    for p in platforms:
        agg = master.sum_range(monday.isoformat(), sunday.isoformat(), p)
        by[p] = agg
        tot_sales += agg["sales"]
        tot_refund += agg["refund"]
    return {
        "total": {"sales": tot_sales, "refund": tot_refund, "net": tot_sales - tot_refund},
        "by": by,
    }


def week_has_data(master, monday: _dt.date, platforms) -> bool:
    return week_totals(master, monday, platforms)["total"]["sales"] != 0 or \
        any(master.get((monday + i * ONE_DAY).isoformat(), p) for i in range(7) for p in platforms)


def build_week_line(master, monday: _dt.date, platforms) -> str:
    """生成该周一句话周报（含净额环比）。调用方需保证该周已结束。"""
    sunday = monday + 6 * ONE_DAY
    cur = week_totals(master, monday, platforms)
    prev = week_totals(master, monday - 7 * ONE_DAY, platforms)
    rng = f"{monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}"

    total_sales = cur["total"]["sales"]
    total_net = cur["total"]["net"]
    daily = total_net / 7.0
    head = (f"【{rng}】全平台营业额 {_money(total_sales)}，"
            f"净营业额 {_money(total_net)}，日均净额 {_money(daily)}。")

    prev_has = week_has_data(master, monday - 7 * ONE_DAY, platforms)
    if not prev_has:
        return head + "（首周，无上周对比）"

    overall = _pct_phrase(total_net, prev["total"]["net"])
    parts = []
    for p in platforms:
        ph = _pct_phrase(cur["by"][p]["net"], prev["by"][p]["net"])
        parts.append(f"{p}{ph}" if ph else f"{p}无可比")
    overall_txt = f"整体净额较上周{overall}" if overall else "整体净额与上周不可比"
    return head + overall_txt + "，其中" + "、".join(parts) + "。"


def _ended(monday: _dt.date, today: _dt.date) -> bool:
    """该周(周日)是否已结束(严格早于今天)。"""
    return (monday + 6 * ONE_DAY) < today


def weekly_lines_for_month(master, year: int, month: int, platforms, today: _dt.date = None):
    """月表底部要显示的周报行：该月所有「已结束且有数据」的完整周，各一行。
    返回 [(monday, line), ...] 按时间升序。"""
    today = today or _dt.date.today()
    out = []
    for block in cal.month_week_blocks(year, month):
        monday = block[0]
        if _ended(monday, today) and week_has_data(master, monday, platforms):
            out.append((monday, build_week_line(master, monday, platforms)))
    return out


# ---------- 文本文件 ----------
def week_md_name(monday: _dt.date) -> str:
    sunday = monday + 6 * ONE_DAY
    iso = cal.iso_week_key(monday)   # 如 2026-W32
    return f"周报_{iso}_{monday.strftime('%m%d')}-{sunday.strftime('%m%d')}.md"


def report_dir(output_dir: str) -> str:
    return os.path.join(output_dir, "周报")


def write_week_md(output_dir: str, master, monday: _dt.date, platforms) -> str:
    """把某周的周报写成 md 文本文件（幂等，覆盖同名）。返回文件路径。"""
    d = report_dir(output_dir)
    os.makedirs(d, exist_ok=True)
    line = build_week_line(master, monday, platforms)
    sunday = monday + 6 * ONE_DAY
    path = os.path.join(d, week_md_name(monday))
    content = (f"# 周报 {cal.iso_week_key(monday)}"
               f"（{monday.isoformat()} ~ {sunday.isoformat()}）\n\n"
               f"{line}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def write_completed_weeks(output_dir: str, master, dates, platforms, today: _dt.date = None):
    """给一批被抓日期，把其中「已结束且有数据」的完整周各写一份 md。返回写出的路径列表。"""
    today = today or _dt.date.today()
    mondays = []
    for ds in dates:
        m = cal.week_monday(cal.parse_date(ds))
        if m not in mondays:
            mondays.append(m)
    written = []
    for m in mondays:
        if _ended(m, today) and week_has_data(master, m, platforms):
            written.append(write_week_md(output_dir, master, m, platforms))
    return written
