# -*- coding: utf-8 -*-
"""淘宝（生意参谋）抓取模块（接口版）。payAmt=销售、rfdSucAmt=退款(完结时间)、subPayOrdSubCnt=支付子订单数。
⚠ 淘宝登录反爬最强、自动化滑块过不去：**仍须 CDP 连接「真人登录的 Chrome」**（淘宝Chrome.bat + port 9222）。
  self.page 由执行器通过 connect_over_cdp 传入。登录态无法脚本化，这点与另三家不同。
但**取数已改用明文接口**（告别点日历/关弹窗/文本抓/强制刷新）：在 sycm 域内 page.evaluate fetch
  `portal/long/period/nodistinct/coreIndex/getShopMainIndexes/v3.json`
  ?dateType=compareRange&dateRange=DATE|DATE,DATE|DATE&device=0&token=<_tb_token_ cookie>（credentials:'include'）。
  compareRange 传相同起止日=取该单日；token 从 document.cookie 的 _tb_token_ 读。
  (该接口不认 dateType=day，必须 compareRange。)
"""
from __future__ import annotations
import re
import json
from .base import PlatformCrawler, LoginRequired
from core import calendar_utils as cal


# 页面上下文里 fetch 店铺核心指标。args=[ds(YYYY-MM-DD)]。token 从 _tb_token_ cookie 取。
_FETCH_JS = r"""
async (ds) => {
  const m = document.cookie.match(/_tb_token_=([^;]+)/);
  const token = m ? m[1] : "";
  const base = "https://sycm.taobao.com/portal/long/period/nodistinct/coreIndex/getShopMainIndexes/v3.json";
  const qs = `needCycleCrc=true&dateType=compareRange&dateRange=${ds}%7C${ds}%2C${ds}%7C${ds}`
           + `&device=0&token=${token}&_=${Date.now()}`;
  try {
    const r = await fetch(base + "?" + qs, {credentials: "include", headers: {"accept": "application/json"}});
    return {status: r.status, token: token, body: await r.text()};
  } catch (e) { return {error: String(e)}; }
}
"""


class TaobaoCrawler(PlatformCrawler):
    name = "淘宝"
    sycm_url = "https://sycm.taobao.com/portal/home.htm"

    def is_logged_in(self) -> bool:
        try:
            self.page.goto(self.sycm_url, timeout=40000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(5000)
        except Exception:
            pass
        url = (self.page.url or "").lower()
        return ("login" not in url) and ("havana" not in url)

    def do_login(self, account: str, password: str):
        # 淘宝不走脚本自动登录（滑块过不了）。由用户在真人 Chrome 里登录，脚本 CDP 连接。
        raise NotImplementedError("淘宝请用『淘宝Chrome.bat』在真人浏览器登录，脚本通过 CDP 连接抓取")

    def _crawl_api(self, date) -> dict:
        ds = cal.parse_date(date).isoformat()
        page = self.page
        # 需处于 sycm 域内：接口同源 + 依赖 _tb_token_ cookie
        if "sycm.taobao" not in (page.url or ""):
            page.goto(self.sycm_url, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
        if "login" in (page.url or "").lower():
            raise LoginRequired("淘宝未登录，请用『淘宝Chrome.bat』登录后重试")

        res = page.evaluate(_FETCH_JS, ds)
        if res.get("error"):
            raise Exception(f"接口请求异常：{res['error']}")
        if not res.get("token"):
            raise LoginRequired("淘宝未取到 _tb_token_（登录态失效），请用『淘宝Chrome.bat』登录后重试")
        try:
            j = json.loads(res.get("body") or "")
        except Exception as e:
            raise Exception(f"接口返回非 JSON（status={res.get('status')}）：{e}")

        content = j.get("content") or {}
        if content.get("code") not in (0, None) or content.get("hasError") or j.get("hasError"):
            msg = content.get("message") or j.get("message") or ""
            if "登录" in msg or "login" in str(msg).lower():
                raise LoginRequired(f"淘宝登录态失效：{msg}")
            raise Exception(f"接口返回错误：{msg or content.get('code')}")

        d = content.get("data")
        if not isinstance(d, dict):
            raise Exception("接口结构异常，未取到 data")

        sales = self._val(d, "payAmt")            # 支付金额
        refund = self._val(d, "rfdSucAmt")        # 退款金额（完结时间/成功退款金额）
        orders = self._val(d, "subPayOrdSubCnt")  # 支付子订单数
        if sales is None or refund is None or orders is None:
            raise Exception(f"字段提取失败: sales={sales} refund={refund} orders={orders}")
        return {"sales": round(float(sales), 2), "refund": round(float(refund), 2), "orders": int(orders)}

    @staticmethod
    def _val(d, key):
        """取 d[key]；部分指标是 {value:...}，部分是裸值。"""
        v = d.get(key)
        if isinstance(v, dict):
            return v.get("value")
        return v

    # ---------- 抓取（老方法：数据概览「自定义」日历 + 文本抓，接口失效时兜底）----------
    def _close_popups(self):
        """关闭刷新后可能弹出的营销/提示弹窗（会遮挡「自定义」等操作）。"""
        page = self.page
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            for sel in [".ant-modal-close", ".ant-modal-close-x", "[aria-label=Close]"]:
                try:
                    if page.locator(sel).count() > 0:
                        page.locator(sel).first.click(timeout=1500)
                        page.wait_for_timeout(300)
                except Exception:
                    pass
            page.evaluate(
                "() => { document.querySelectorAll('.ant-modal-wrap,.ant-modal-root,.ant-modal-mask').forEach(e=>{e.style.display='none';}); }")
            page.wait_for_timeout(300)
        except Exception:
            pass

    def _select_date_page(self, ds: str) -> str:
        """用「自定义」日历精确选单日 ds：data-value 定位 + JS 点击 + 确定 + 验证。"""
        page = self.page
        page.get_by_text("自定义", exact=True).first.click(timeout=8000)
        page.wait_for_timeout(2500)
        cells = page.locator(f'td[data-value="{ds}"]')
        n = cells.count()
        if n == 0:
            raise Exception(f"日历中找不到日期 {ds}（可能需翻月，暂只支持近月）")
        cells.first.evaluate("el => el.click()")                       # 开始
        page.wait_for_timeout(700)
        cells.nth(n - 1 if n > 1 else 0).evaluate("el => el.click()")  # 结束（同日=单日）
        page.wait_for_timeout(800)
        page.get_by_text("确 定", exact=True).first.evaluate("el => el.click()")  # 注意“确 定”带空格
        page.wait_for_timeout(5000)
        body = page.inner_text("body")
        j = body.find("统计时间")
        if ds not in body[j:j + 30]:
            raise Exception(f"选日期 {ds} 未生效（统计时间={body[j:j+30]!r}）")
        return body

    def _crawl_page(self, date) -> dict:
        ds = cal.parse_date(date).isoformat()
        page = self.page
        # 每次强制刷新到干净的数据概览（避免残留上次选的日期/状态）
        page.goto(self.sycm_url, timeout=40000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        if "login" in (page.url or "").lower():
            raise LoginRequired("淘宝未登录，请用『淘宝Chrome.bat』登录后重试")
        self._close_popups()
        body = self._select_date_page(ds)
        sales = self._num_after(body, "支付金额")
        refund = self._num_after(body, "退款金额（完结时间）")
        orders = self._num_after(body, "支付子订单数")
        if sales is None or refund is None or orders is None:
            raise Exception(f"字段提取失败: sales={sales} refund={refund} orders={orders}")
        return {"sales": sales, "refund": refund, "orders": int(orders)}

    @staticmethod
    def _num_after(text: str, label: str):
        i = text.find(label)
        if i < 0:
            return None
        m = re.search(r"([\d,]+\.?\d*)", text[i + len(label): i + len(label) + 30])
        return float(m.group(1).replace(",", "")) if m else None
