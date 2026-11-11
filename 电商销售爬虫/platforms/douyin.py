# -*- coding: utf-8 -*-
"""抖音（抖店/电商罗盘）抓取模块。
字段：income_amt=成交金额(销售)、refund_amt=成交退款金额(退款时间,退款)、pay_cnt=成交订单数(订单)。
数据源：**明文接口** compass_api/shop/common/homepage/core_index_v3，
  ?begin_date=YYYY/MM/DD 00:00:00&end_date=...&date_type=2&index_selected=... —— date_type=2 支持任意历史单日，
  在已登录的 compass 页面里 page.evaluate(fetch, credentials:'include') 调用，无需 a_bogus 签名。
  (告别坐标点日历/强制刷新/固定视口，方式与拼多多一致。金额字段 unit=3 为「分」需÷100，订单 unit=5 为原始个数。)
"""
from __future__ import annotations
import re
import datetime
from .base import PlatformCrawler, LoginRequired, DataNotReady
from core import calendar_utils as cal


# 在页面上下文里 fetch 经营概况核心指标接口。args=[ymd(YYYY/MM/DD), date_type]。
# date_type=2 自定义历史(结算后最准)；=20 近1天(昨天早高峰刚过就有,页面点"近1天"用的就是它)。
# 都带显式 begin_date=end_date=指定日,返回的就是那一天(不是"最近一天",无错位)。
_FETCH_JS = r"""
async (args) => {
  const [ymd, dt] = args;
  const base = "https://compass.jinritemai.com/compass_api/shop/common/homepage/core_index_v3";
  const qs = new URLSearchParams({
    begin_date: ymd + " 00:00:00",
    end_date: ymd + " 00:00:00",
    date_type: dt,
    activity_id: "",
    index_selected: "income_amt,refund_amt,pay_cnt",
    operate_type: "0",
  });
  try {
    const resp = await fetch(base + "?" + qs.toString(),
      {credentials: "include", headers: {"accept": "application/json"}});
    return {status: resp.status, body: await resp.text()};
  } catch (e) { return {error: String(e)}; }
}
"""


class DouyinCrawler(PlatformCrawler):
    name = "抖音"
    login_url = "https://fxg.jinritemai.com/login/common?channel=zhaoshang"
    home_url = "https://fxg.jinritemai.com/"
    compass_url = "https://compass.jinritemai.com/shop"
    success_url_markers = ("/ffa/",)   # 登录成功后进入 /ffa/... 工作台

    # ---------- 登录态 ----------
    def is_logged_in(self) -> bool:
        # 访问需登录的工作台内页：未登录会被重定向到 login；已登录停在 /ffa/...
        try:
            self.page.goto("https://fxg.jinritemai.com/ffa/mshop/homepage/index",
                           timeout=40000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(5000)
        except Exception:
            pass
        return "login" not in (self.page.url or "").lower()

    def do_login(self, account: str, password: str):
        self.page.goto(self.login_url, timeout=60000)
        self.page.wait_for_timeout(2500)
        try:
            self.page.get_by_text("邮箱登录", exact=True).first.click(timeout=5000)
            self.page.wait_for_timeout(1000)
        except Exception:
            pass
        # 模拟真人逐字敲键盘(清空自动填充后重新输入)，降低触发验证码概率
        self._type_human('input[name="email"]', account)
        self._type_human('input[name="password"]', password)
        try:
            cb = self.page.locator("input.auxo-checkbox-input").first
            if cb.count() > 0 and not cb.is_checked():
                try:
                    cb.check(timeout=2000)
                except Exception:
                    cb.click(timeout=2000, force=True)
        except Exception:
            pass
        try:
            self.page.locator("button.account-center-action-button").first.click(timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(2000)

    # ---------- 抓取（接口版）----------
    def _crawl_api(self, date) -> dict:
        page = self.page
        d = cal.parse_date(date)
        ymd = d.strftime("%Y/%m/%d")

        # 需处于 compass 域内，fetch 才能带上登录 cookie（同源 + credentials:'include'）
        if "compass.jinritemai.com" not in (page.url or ""):
            page.goto(self.compass_url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        if "login" in (page.url or "").lower():
            raise LoginRequired("抖音未登录（compass 被重定向到登录页）")

        # 先试 date_type=2(自定义历史,结算后最准)；被"日期校验失败"拒(昨天早高峰还没结算)
        # 就换 date_type=20(近1天,页面同款,昨天刚过就有)。两个都带显式日期,返回的就是那天,无错位。
        import json
        j = None
        last_st, last_msg = None, ""
        for dt in ("2", "20"):
            res = page.evaluate(_FETCH_JS, [ymd, dt])
            if res.get("error"):
                raise Exception(f"接口请求异常：{res['error']}")
            try:
                jj = json.loads(res.get("body") or "")
            except Exception as e:
                raise Exception(f"接口返回非 JSON（status={res.get('status')}）：{e}")
            st = jj.get("st")
            if st in (0, None):
                j = jj
                break
            msg = jj.get("msg") or ""
            if st in (100001, 100002, 8, 9) or "登录" in msg or "login" in msg.lower():
                raise LoginRequired(f"抖音登录态失效：st={st} {msg}")
            last_st, last_msg = st, msg
            # st=100008/日期校验失败 → 换下一个 date_type 再试
        if j is None:
            # 两种 date_type 都取不到 → 该日数据平台侧确实还没就绪(宁可缺也不存错,等晚点重抓)
            raise DataNotReady(f"抖音 {ymd} 数据未就绪：st={last_st} {last_msg}")

        try:
            data0 = (j["data"]["module_data"]["homepage_core_index"]
                     ["compass_general_multi_index_card_value"]["data"][0])
        except Exception as e:
            raise Exception(f"接口结构异常，未取到指标数据：{e}")

        sales = self._amt(data0, "income_amt")   # 成交金额
        refund = self._amt(data0, "refund_amt")  # 成交退款金额（退款时间）
        orders = self._cnt(data0, "pay_cnt")     # 成交订单数
        if sales is None or refund is None or orders is None:
            raise Exception(f"字段提取失败: sales={sales} refund={refund} orders={orders}")
        return {"sales": sales, "refund": refund, "orders": int(orders)}

    # ---------- 工具 ----------
    @staticmethod
    def _node(data0, key):
        """取 data0[key].index_value.value -> {value, unit}。"""
        return data0.get(key, {}).get("index_value", {}).get("value", {})

    @classmethod
    def _amt(cls, data0, key):
        """金额字段：unit=3 为「分」需 ÷100。"""
        node = cls._node(data0, key)
        v = node.get("value")
        if v is None:
            return None
        return round(v / 100, 2) if node.get("unit") == 3 else float(v)

    @classmethod
    def _cnt(cls, data0, key):
        """计数字段：原始个数。"""
        node = cls._node(data0, key)
        v = node.get("value")
        return None if v is None else int(v)

    # ---------- 抓取（老方法：页面文本，接口失效时兜底）----------
    # compass.jinritemai.com/shop「经营概况」区，一律用「自定义」日历选确切日期(viewport1680x950)。
    # ⚠ 不用「近1天」：页面无日期标识、无法核对，而「近1天」在数据未就绪时(如早高峰)会显示前一天，
    #   盲抓会静默存错日期(2026-08-13 复盘：8/11、8/12 曾因此各错位一天)。自定义日历至少定位到目标日格子。
    def _select_date_page(self, date):
        d = cal.parse_date(date)
        page = self.page
        try:
            page.get_by_text("自定义", exact=True).first.click(timeout=6000)
            page.wait_for_timeout(2500)
            first = datetime.date(d.year, d.month, 1)
            offset = first.weekday()               # 周一=0..周日=6
            pos = (d.day - 1) + offset
            x = 927 + (pos % 7) * 36                # 日历格子原点/间距(viewport 1680x950)
            y = 276 + (pos // 7) * 36
            page.mouse.click(x, y)
            page.wait_for_timeout(600)
            page.mouse.click(x, y)
            page.wait_for_timeout(1500)
            for t in ("确定", "确认"):
                try:
                    page.get_by_text(t, exact=True).first.click(timeout=1500)
                    break
                except Exception:
                    pass
            page.wait_for_timeout(4000)
        except Exception as e:
            self.log and self.log.warning(f"[抖音] 选日期 {d} 失败：{e}，用页面默认时间范围")

    def _crawl_page(self, date) -> dict:
        page = self.page
        try:
            page.set_viewport_size({"width": 1680, "height": 950})   # 坐标法依赖固定视口
        except Exception:
            pass
        page.goto(self.compass_url, timeout=60000, wait_until="domcontentloaded")
        if not self._wait_text("成交金额", 40):
            raise LoginRequired("经营概况未加载（可能登录态失效或页面异常）")
        self._select_date_page(date)
        page.wait_for_timeout(1500)

        body = page.inner_text("body")
        start = body.find("经营概况")
        seg = body[start:] if start >= 0 else body
        sales = self._num_after(seg, "成交金额")
        refund = self._num_after(seg, "成交退款金额（退款时间）")
        orders = self._num_after(seg, "成交订单数")
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
        """取 label 之后第一个数字（去掉 ¥ 和千分位逗号）。"""
        i = text.find(label)
        if i < 0:
            return None
        seg = text[i + len(label): i + len(label) + 40]
        m = re.search(r"¥?\s*([\d,]+\.?\d*)", seg)
        return float(m.group(1).replace(",", "")) if m else None
