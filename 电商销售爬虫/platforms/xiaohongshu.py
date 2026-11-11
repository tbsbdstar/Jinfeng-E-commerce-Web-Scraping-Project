# -*- coding: utf-8 -*-
"""小红书（千帆）抓取模块（接口版）。payGmv=销售、refundPayGmv=退款(退款时间)、payPkgCnt=订单。
数据源：**明文接口** POST ark.xiaohongshu.com/api/edith/butterfly/data?type=sellerCarrierChannelOverall，
  body filterMap 用 dateType:0/dateSelectType:"custom"/startDate=endDate=YYYY-MM-DD 取任意单日
  （跨月无障碍，替代早期"自定义日历坐标/文本抓取"方案，与抖音/拼多多一致）。
  在已登录 ark 页面里 page.evaluate(fetch, credentials:'include') 调用。金额单位即「元」，无需换算。
退款口径（2026-08-13 改）：用「退款金额(退款时间)」=接口 refundPayGmv（=card+note之和），
  而非旧的「退款金额(支付时间)」=payRefundAmt。两口径接口都有，页面也都显示。
"""
from __future__ import annotations
import re
import json
from .base import PlatformCrawler, LoginRequired
from core import calendar_utils as cal


# 页面上下文里 POST butterfly/data 取「商家经营数据总览」。args=[ymd(YYYY-MM-DD)]。
_FETCH_JS = r"""
async (ymd) => {
  const url = "https://ark.xiaohongshu.com/api/edith/butterfly/data?type=sellerCarrierChannelOverall";
  const body = {requestBody:{blockElements:[
    {blockKey:"sellerCarrierChannelOverall",
     filterMap:{dateType:0,dateSelectType:"custom",startDate:ymd,endDate:ymd,sellerManageType:"all"}}
  ]},type:"sellerCarrierChannelOverall"};
  try {
    const r = await fetch(url, {method:"POST", headers:{"content-type":"application/json"},
      body:JSON.stringify(body), credentials:"include"});
    return {status: r.status, body: await r.text()};
  } catch (e) { return {error: String(e)}; }
}
"""


class XiaohongshuCrawler(PlatformCrawler):
    name = "小红书"
    login_url = "https://customer.xiaohongshu.com/login?service=https://ark.xiaohongshu.com/ark"
    data_url = "https://ark.xiaohongshu.com/app-datacenter/overview"
    # 不设 success_url_markers：登录页URL带 service=ark 参数会误命中；用基类“离开登录页”判断。

    def is_logged_in(self) -> bool:
        try:
            self.page.goto(self.data_url, timeout=40000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(4000)
        except Exception:
            pass
        url = (self.page.url or "").lower()
        return ("login" not in url) and ("customer.xiaohongshu" not in url)

    def do_login(self, account: str, password: str):
        self.page.goto(self.login_url, timeout=60000)
        self.page.wait_for_timeout(3000)
        try:
            self.page.get_by_text("账号登录", exact=True).first.click(timeout=4000)
            self.page.wait_for_timeout(800)
        except Exception:
            pass
        self.page.fill('input[name="email"]', account)
        self.page.fill('input[name="password"]', password)
        try:
            cb = self.page.locator('input[type="checkbox"]').first
            if cb.count() > 0 and not cb.is_checked():
                cb.check(timeout=2000)
        except Exception:
            pass
        try:
            self.page.locator(".beer-login-btn").first.click(timeout=6000)
        except Exception:
            self.page.get_by_role("button", name=re.compile("登.*录")).first.click(timeout=6000)
        self.page.wait_for_timeout(2500)

    def _crawl_api(self, date) -> dict:
        page = self.page
        d = cal.parse_date(date)
        ymd = d.isoformat()   # YYYY-MM-DD

        # 需处于 ark 域内，fetch 才能带上登录 cookie
        if "ark.xiaohongshu.com" not in (page.url or ""):
            page.goto(self.data_url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        url = (page.url or "").lower()
        if ("login" in url) or ("customer.xiaohongshu" in url):
            raise LoginRequired("小红书未登录（被重定向到登录页）")

        res = page.evaluate(_FETCH_JS, ymd)
        if res.get("error"):
            raise Exception(f"接口请求异常：{res['error']}")
        try:
            j = json.loads(res.get("body") or "")
        except Exception as e:
            raise Exception(f"接口返回非 JSON（status={res.get('status')}）：{e}")

        if not j.get("success", j.get("code") == 0):
            msg = j.get("msg") or ""
            if "登录" in msg or "login" in msg.lower():
                raise LoginRequired(f"小红书登录态失效：{msg}")
            raise Exception(f"接口返回失败：code={j.get('code')} msg={msg}")

        try:
            d0 = j["data"][0]["data"][0]   # 商家经营数据总览
        except Exception as e:
            raise Exception(f"接口结构异常，未取到总览数据：{e}")

        sales = self._val(d0, "payGmv")         # 支付金额
        refund = self._val(d0, "refundPayGmv")  # 退款金额(退款时间)
        orders = self._val(d0, "payPkgCnt")     # 支付订单数(≈包裹数)
        if sales is None or refund is None or orders is None:
            raise Exception(f"字段提取失败: sales={sales} refund={refund} orders={orders}")
        return {"sales": float(sales), "refund": float(refund), "orders": int(orders)}

    @staticmethod
    def _val(d0, key):
        """取 d0[key].value（接口每指标为 {value, proportion, ratio}）。"""
        node = d0.get(key)
        if isinstance(node, dict):
            return node.get("value")
        return node

    # ---------- 抓取（老方法：页面文本，接口失效时兜底）----------
    # ark 数据页「经营数据概览」，用「自定义」日历选单日(.calendar-dayCell 文本==day)。⚠ 日历默认本月,跨往月点不到。
    def _select_date_page(self, date):
        d = cal.parse_date(date)
        page = self.page
        try:
            page.locator('input[placeholder="开始时间"]').first.click(timeout=6000)
            page.wait_for_timeout(1500)
            day = str(d.day)
            sel = page.locator(".calendar-dayCell").filter(has_text=re.compile(f"^{day}$"))
            sel.first.click(timeout=5000)              # 开始
            page.wait_for_timeout(600)
            sel.first.click(timeout=5000)              # 结束（同一天=单日）
            page.wait_for_timeout(4000)
        except Exception as e:
            self.log and self.log.warning(f"[小红书] 选日期 {d} 失败：{e}，用页面默认时间范围")

    def _crawl_page(self, date) -> dict:
        page = self.page
        page.goto(self.data_url, timeout=60000, wait_until="domcontentloaded")
        if not self._wait_text("经营数据概览", 40):
            raise LoginRequired("商品总览未加载（可能登录态失效或页面异常）")
        self._select_date_page(date)
        page.wait_for_timeout(1500)

        body = page.inner_text("body")
        start = body.find("经营数据概览")   # 避开最上方「实时总览」
        seg = body[start:] if start >= 0 else body
        sales = self._num_after(seg, "支付金额")
        refund = self._num_after(seg, "退款金额(退款时间)")
        if refund is None:
            refund = self._num_after(seg, "退款金额")
        orders = self._num_after(seg, "支付订单数")
        if sales is None or refund is None or orders is None:
            raise Exception(f"字段提取失败: sales={sales} refund={refund} orders={orders}")
        return {"sales": sales, "refund": refund, "orders": int(orders)}

    def _wait_text(self, text: str, timeout_s: int) -> bool:
        waited = 0
        while waited < timeout_s:
            self.page.wait_for_timeout(2000)
            waited += 2
            try:
                if text in self.page.inner_text("body"):
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _num_after(text: str, label: str):
        i = text.find(label)
        if i < 0:
            return None
        seg = text[i + len(label): i + len(label) + 40]
        m = re.search(r"¥?\s*([\d,]+\.?\d*)", seg)
        return float(m.group(1).replace(",", "")) if m else None
