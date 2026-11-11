# -*- coding: utf-8 -*-
"""拼多多抓取模块（接口版）。直接调 queryMallTradeList 接口拿明文数据，
不再用 OCR/截图——避开了字体加密、数字滚动动画、消息弹窗遮挡等一堆坑，又快又准。
字段：payOrdrAmt=销售、sucRfOrdrAmt1d=退款、payOrdrCnt=订单、stateDate=日期。
参数：{queryType:0, queryDate:"YYYY-MM-DD"}，返回 result.dayList 按天列表。
老方法(_crawl_page)：数据页数字有字体反爬，用「截图+OCR」读，接口失效时兜底(慢且需 winsdk OCR)。
"""
from __future__ import annotations
import os
import re
import json
import datetime
from .base import PlatformCrawler, LoginRequired
from core import calendar_utils as cal

API = "https://mms.pinduoduo.com/sydney/api/mallTrade/queryMallTradeList"

_VIEWPORT = {"width": 1680, "height": 950}


def _first_num_seg(raw: str) -> str:
    """取第一段连续的数字/分隔符（遇中文/字母即停，甩掉“较前1日X%”）。"""
    s = raw.replace(" ", "")
    m = re.search(r"\d", s)
    if not m:
        return ""
    s = s[m.start():]
    m2 = re.match(r"[\d，,、．。\.]+", s)
    return m2.group(0) if m2 else ""


def _parse_line(lines, is_int: bool = False):
    """取第一个含数字的行解析（主数字单独成行；“较前1日”在别的行，避免粘连）。"""
    for ln in lines:
        seg = _first_num_seg(ln)
        parts = re.findall(r"\d+", seg)
        if not parts:
            continue
        if is_int:
            return int("".join(parts))
        if len(parts) >= 2 and len(parts[-1]) <= 2:   # 末段1-2位视为小数
            return float("".join(parts[:-1]) + "." + parts[-1])
        return float("".join(parts))
    return None


class PinduoduoCrawler(PlatformCrawler):
    name = "拼多多"
    login_url = "https://mms.pinduoduo.com/login/"
    data_url = "https://mms.pinduoduo.com/sycm/stores_data/operation"

    def is_logged_in(self) -> bool:
        try:
            self.page.goto(self.data_url, timeout=40000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(5000)
        except Exception:
            pass
        return "login" not in (self.page.url or "").lower()

    def do_login(self, account: str, password: str):
        self.page.goto(self.login_url, timeout=60000)
        self.page.wait_for_timeout(3000)
        try:
            self.page.get_by_text("已安装去使用", exact=True).first.click(timeout=3500)
            self.page.wait_for_timeout(700)
        except Exception:
            pass
        try:
            self.page.get_by_text("账号登录", exact=True).first.click(timeout=4000)
            self.page.wait_for_timeout(800)
        except Exception:
            pass
        # 模拟真人逐字敲键盘(清空自动填充后重新输入)，降低触发验证码概率
        self._type_human("#usernameId", account)
        self._type_human("#passwordId", password)
        try:
            self.page.get_by_role("button", name="登录").first.click(timeout=6000)
        except Exception:
            self.page.locator("button.BTN_danger_5-155-0").first.click(timeout=6000)
        self.page.wait_for_timeout(2500)

    def _crawl_api(self, date) -> dict:
        ds = cal.parse_date(date).isoformat()
        page = self.page
        # 需在 mms 域下才能带 cookie 调接口
        if "mms.pinduoduo.com" not in (page.url or ""):
            page.goto(self.data_url + "?dateFlag=0", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        if "login" in (page.url or "").lower():
            raise LoginRequired("拼多多未登录，需重新登录")

        res = page.evaluate(
            "async (qd) => { const r = await fetch('" + API + "', "
            "{method:'POST', headers:{'content-type':'application/json'}, "
            "body:JSON.stringify({queryType:0, queryDate:qd}), credentials:'include'}); "
            "return await r.text(); }", ds)
        data = json.loads(res)
        if not data.get("success"):
            raise Exception(f"接口返回失败：{data.get('errorMsg')}")
        day_list = (data.get("result") or {}).get("dayList") or []
        row = next((d for d in day_list if d.get("stateDate") == ds), None)
        if row is None:
            raise Exception(f"接口 dayList 中找不到 {ds} 的数据（返回范围：{[d.get('stateDate') for d in day_list]}）")
        return {
            "sales": float(row["payOrdrAmt"]),
            "refund": float(row["sucRfOrdrAmt1d"]),
            "orders": int(row["payOrdrCnt"]),
        }

    # ---------- 抓取（老方法：截图+OCR，接口失效时兜底）----------
    # ⚠ 数据页数字有字体反爬(DOM/接口字符都是加密),故截图渲染出的数字用 Windows OCR 读。慢,且个别日期会点偏。
    def _select_date_page(self, date):
        d = cal.parse_date(date)
        page = self.page
        try:                       # 隐藏遮挡「自定义」的右上角消息浮层
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.evaluate(
                "() => { ['#umd_kits_home_entry','[class*=MsgItem]'].forEach(s=>{"
                "document.querySelectorAll(s).forEach(o=>{o.style.display='none';});}); }")
            page.wait_for_timeout(300)
        except Exception:
            pass
        for how in ("normal", "force", "js"):    # 点「自定义」：常规→force→JS 兜底
            try:
                if how == "js":
                    page.evaluate(
                        "() => { const el=[...document.querySelectorAll('div')]"
                        ".find(e=>e.children.length===0 && e.textContent.trim()==='自定义' && (e.className||'').includes('date-picker')); if(el) el.click(); }")
                else:
                    page.locator("[class*=date-picker-item-inner]", has_text="自定义").first.click(
                        timeout=4000, force=(how == "force"))
                page.wait_for_timeout(2000)
                break
            except Exception:
                continue
        first = datetime.date(d.year, d.month, 1)
        offset = first.weekday()
        pos = (d.day - 1) + offset
        x = 1338 + (pos % 7) * 36                # 右侧(本月)网格原点/间距(viewport 1680x950)
        y = 243 + (pos // 7) * 30
        page.mouse.click(x, y)
        page.wait_for_timeout(700)
        page.mouse.click(x, y)
        page.wait_for_timeout(800)
        try:
            page.get_by_text("确认", exact=True).first.click(timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        body = page.inner_text("body")           # 验证统计时间=目标日,否则抛错(绝不抓错日期)
        i = body.find("交易概况")
        seg = body[i:i + 50] if i >= 0 else ""
        if d.isoformat() not in seg:
            raise Exception(f"[拼多多] 指定日期 {d} 未生效（统计时间={seg!r}），放弃以免抓错")

    def _crawl_page(self, date) -> dict:
        from core import ocr
        page = self.page
        try:
            page.set_viewport_size(_VIEWPORT)
        except Exception:
            pass
        page.goto(self.data_url + "?dateFlag=0", timeout=60000, wait_until="domcontentloaded")
        if not self._wait_text("成交金额", 40):
            raise LoginRequired("交易概况未加载（可能登录态失效或页面异常）")
        self._select_date_page(date)
        page.wait_for_timeout(8000)              # 等选日期后加密数字重新渲染

        locs = page.evaluate(
            """() => {
              const want=['成交金额','退款金额','成交订单数']; const out={};
              for(const w of want){
                const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0 && e.textContent.trim()===w);
                if(el){const r=el.getBoundingClientRect(); out[w]={x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};}
                else out[w]=null;
              } return out;
            }""")
        tmp = os.environ.get("TEMP", ".")
        vals = {}
        for label in ["成交金额", "退款金额", "成交订单数"]:
            loc = locs.get(label)
            if not loc:
                raise Exception(f"未定位到指标标签: {label}")
            clip = {"x": max(0, loc["x"] - 12), "y": max(0, loc["y"] - 3), "width": 175, "height": 82}
            img = os.path.join(tmp, f"_pdd_{label}.png")
            page.screenshot(path=img, clip=clip)
            vals[label] = _parse_line(ocr.ocr_lines(img), is_int=(label == "成交订单数"))
        sales, refund, orders = vals["成交金额"], vals["退款金额"], vals["成交订单数"]
        if sales is None or refund is None or orders is None:
            raise Exception(f"OCR解析失败: {vals}")
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
