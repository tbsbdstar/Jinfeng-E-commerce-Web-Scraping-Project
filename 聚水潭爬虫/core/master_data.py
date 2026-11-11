# -*- coding: utf-8 -*-
"""
主数据层：唯一真相源。逐日 × 渠道 × 产品 记录 实发/实退 数量。
抓取只往这里 upsert（同 date+channel+product 覆盖对应指标，另一指标保留）；
所有 Excel 都从这里读，重复抓不会污染。
"""
from __future__ import annotations
import csv, os, io
import datetime as _dt
from typing import Dict, Optional
from . import calendar_utils as cal

FIELDS = ["date", "channel", "product", "shipped", "returned", "crawled_at"]


class MasterData:
    def __init__(self, path: str):
        self.path = path
        self._rows: Dict[tuple, dict] = {}   # (date,channel,product) -> row
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        raw = open(self.path, "rb").read()
        text = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(enc); break
            except UnicodeDecodeError:
                continue
        if text is None:
            return
        for r in csv.DictReader(io.StringIO(text)):
            key = (r["date"], r["channel"], r["product"])
            self._rows[key] = {
                "date": r["date"], "channel": r["channel"], "product": r["product"],
                "shipped": _to_num(r.get("shipped")), "returned": _to_num(r.get("returned")),
                "crawled_at": r.get("crawled_at", ""),
            }

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        rows = sorted(self._rows.values(), key=lambda x: (x["date"], x["channel"], x["product"]))
        with open(self.path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader(); w.writerows(rows)

    def upsert(self, date, channel: str, product: str,
               shipped: Optional[float] = None, returned: Optional[float] = None):
        """合并写：只更新传入的指标，另一指标保留原值。"""
        d = cal.parse_date(date).isoformat()
        key = (d, channel, product)
        row = self._rows.get(key) or {"date": d, "channel": channel, "product": product,
                                      "shipped": 0, "returned": 0, "crawled_at": ""}
        if shipped is not None:
            row["shipped"] = _to_num(shipped)
        if returned is not None:
            row["returned"] = _to_num(returned)
        row["crawled_at"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._rows[key] = row

    def get(self, date, channel: str, product: str) -> dict:
        d = cal.parse_date(date).isoformat()
        return self._rows.get((d, channel, product)) or {"shipped": 0, "returned": 0}

    def has_date(self, date) -> bool:
        """这一天是否抓过（主数据里有任意一行）。用于判断某周是否需要补抓。"""
        d = cal.parse_date(date).isoformat()
        return any(k[0] == d for k in self._rows)

    def qty(self, date, channel: str, product: str, metric: str) -> float:
        return _to_num(self.get(date, channel, product).get(metric, 0))


def _to_num(v):
    """数量一律取绝对值，见 jst_report._num：口径是 |实发| + |实退|，主数据里不存负数。"""
    try:
        f = abs(float(v)) if v not in (None, "") else 0.0
        return int(f) if float(f).is_integer() else f
    except (ValueError, TypeError):
        return 0
