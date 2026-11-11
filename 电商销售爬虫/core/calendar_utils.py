# -*- coding: utf-8 -*-
"""
日历工具：周分块（周一~周日）、跨月周归属、月度周块生成。
整个方案最关键的口径都在这里，务必与 docs/逻辑规则.md §4 一致。
"""
from __future__ import annotations
import datetime as _dt
from typing import List

Date = _dt.date
ONE_DAY = _dt.timedelta(days=1)


def parse_date(s: str) -> Date:
    """接受 'YYYY-MM-DD' 或 date 对象。"""
    if isinstance(s, _dt.date):
        return s
    return _dt.datetime.strptime(s.strip(), "%Y-%m-%d").date()


def week_monday(d: Date) -> Date:
    """返回 d 所在周的周一（周一为一周开始）。"""
    return d - _dt.timedelta(days=d.weekday())  # weekday(): 周一=0


def week_days(monday: Date) -> List[Date]:
    """给定周一，返回该周 7 天（周一~周日）。"""
    return [monday + i * ONE_DAY for i in range(7)]


def iso_week_key(d: Date) -> str:
    """周标识，如 '2026-W32'（ISO 周编号，便于命名/去重）。"""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def month_last_day(year: int, month: int) -> Date:
    if month == 12:
        return _dt.date(year, 12, 31)
    return _dt.date(year, month + 1, 1) - ONE_DAY


def month_week_blocks(year: int, month: int) -> List[List[Date]]:
    """
    返回某月的所有周块，每块是 7 个 date（周一~周日）。
    - 第一块 = 包含该月 1 号的那一周（可能从上月末开始，如 7/27）
    - 最后一块 = 包含该月最后一天的那一周（可能延伸到下月初）
    跨月周会完整保留 7 天（见 逻辑规则 §4）。
    """
    first = _dt.date(year, month, 1)
    last = month_last_day(year, month)
    start_monday = week_monday(first)
    end_monday = week_monday(last)
    blocks = []
    m = start_monday
    while m <= end_monday:
        blocks.append(week_days(m))
        m += 7 * ONE_DAY
    return blocks


def in_month(d: Date, year: int, month: int) -> bool:
    """d 是否归属自然月 (year, month)——用于「当月汇总」的取舍。"""
    return d.year == year and d.month == month


def months_touching(d: Date) -> List[tuple]:
    """d 会显示在哪些月份的表里（返回 (year, month) 列表）。
    某月表按「周一~周日」整周展示，跨月周会把邻月的天灰显补进来。
    因此 d 出现在「d 所在周 7 天所涉及到的每一个月」的表中——
    如 8/1 在 7/27~8/2 这一周，会同时出现在 7 月表(灰显)和 8 月表。
    抓边界日后需据此把相邻月的表也一起重渲染，否则邻月表的灰显格会漏更新。"""
    seen = []
    for day in week_days(week_monday(d)):
        ym = (day.year, day.month)
        if ym not in seen:
            seen.append(ym)
    return seen


def date_range(start: Date, end: Date) -> List[Date]:
    """闭区间 [start, end] 的每一天。"""
    if end < start:
        start, end = end, start
    out, d = [], start
    while d <= end:
        out.append(d)
        d += ONE_DAY
    return out


def yesterday(today: Date | None = None) -> Date:
    today = today or _dt.date.today()
    return today - ONE_DAY
