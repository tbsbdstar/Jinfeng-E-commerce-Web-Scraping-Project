# -*- coding: utf-8 -*-
"""
主数据层：唯一真相源。逐日逐平台记录 3 个指标。
所有 Excel/周报都从这里读；抓取只往这里 upsert。见 docs/逻辑规则.md §6。
"""
from __future__ import annotations
import csv
import os
import datetime as _dt
from typing import Dict, List, Optional
from . import calendar_utils as cal

FIELDS = ["date", "platform", "sales", "refund", "orders", "crawled_at"]


class MasterData:
    def __init__(self, path: str):
        self.path = path
        # key: (date_str, platform) -> row dict
        self._rows: Dict[tuple, dict] = {}
        self._load()

    # ---------- 读写 ----------
    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "rb") as fb:
            raw = fb.read()
        text = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return
        import io
        with io.StringIO(text) as f:
            for r in csv.DictReader(f):
                key = (r["date"], r["platform"])
                self._rows[key] = {
                    "date": r["date"],
                    "platform": r["platform"],
                    "sales": _to_float(r.get("sales")),
                    "refund": _to_float(r.get("refund")),
                    "orders": _to_int(r.get("orders")),
                    "crawled_at": r.get("crawled_at", ""),
                }

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        rows = sorted(self._rows.values(), key=lambda x: (x["date"], x["platform"]))
        with open(self.path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)

    # ---------- 更新 ----------
    def upsert(self, date, platform: str, sales: float, refund: float,
               orders: int, crawled_at: Optional[str] = None):
        """同一天同一平台重复抓 = 覆盖更新。"""
        d = cal.parse_date(date).isoformat()
        self._rows[(d, platform)] = {
            "date": d,
            "platform": platform,
            "sales": float(sales),
            "refund": float(refund),
            "orders": int(orders),
            "crawled_at": crawled_at or _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ---------- 查询 ----------
    def get(self, date, platform: str) -> Optional[dict]:
        d = cal.parse_date(date).isoformat()
        return self._rows.get((d, platform))

    def has(self, date, platform: str) -> bool:
        return self.get(date, platform) is not None

    def sum_range(self, start, end, platform: str) -> dict:
        """[start,end] 闭区间内某平台的合计（缺失的天按 0 计）。"""
        s = r = 0.0
        o = 0
        for d in cal.date_range(cal.parse_date(start), cal.parse_date(end)):
            row = self.get(d, platform)
            if row:
                s += row["sales"]; r += row["refund"]; o += row["orders"]
        return {"sales": s, "refund": r, "orders": o, "net": s - r}

    def sum_dates(self, dates: List, platform: str) -> dict:
        """指定一组日期某平台的合计（用于周总计/月汇总的自定义日期集）。"""
        s = r = 0.0
        o = 0
        for d in dates:
            row = self.get(d, platform)
            if row:
                s += row["sales"]; r += row["refund"]; o += row["orders"]
        return {"sales": s, "refund": r, "orders": o, "net": s - r}


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _to_int(v):
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0
