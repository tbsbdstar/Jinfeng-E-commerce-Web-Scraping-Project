# -*- coding: utf-8 -*-
"""
聚水潭报表驱动（复用已登录的持久化浏览器会话）。

两个报表同一套驱动：
- 实发：subject=adsorder     （筛选『发货时间』），读列『实发数量』
- 实退：subject=adsaftersale （筛选『登记时间』），读列『实退数量』

驱动方式（经抓包验证）：直连报表URL(绕过epaas菜单，快且稳) ->
在 filter.aspx 帧里设 order_date_begin/end + sku_id -> 点『生成报表』(filter帧内 DIV.btn_search) ->
生成会让 channel.aspx 帧带新随机数重载 -> 读 channel 帧表格，按表头定位『渠道』『实发/实退数量』列。
"""
from __future__ import annotations
import time
from typing import Dict, List


class LoginRequired(Exception):
    """登录态失效/过期，需要重新登录（登录.bat）。"""


BI = "https://bi.erp321.com/app/daas/report/subject/{subject}/default.aspx?_c=jst-epaas&epaas=true"

SUBJECTS = {
    # date_type=报表左上角『时间类型』下拉要选的项(None=用页面默认)；date_field=选中后日期框的 name(仅作校验)
    "shipped": {"subject": "adsorder", "qty_col": "实发数量",
                "date_type": None, "date_field": "send_date"},          # 发货时间(默认)
    "returned": {"subject": "adsaftersale", "qty_col": "实退数量",
                 "date_type": "搜进仓时间", "date_field": "io_date"},    # 进仓时间(2026-08-13 起，原为登记时间created)
}

# 切换『时间类型』下拉（自定义下拉：SPAN#date_desc 显示当前项，选项是 DIV.idtype）
_SWITCH_DATE_TYPE = r"""(label)=>{
  const cur=(document.getElementById('date_desc')||{}).innerText||'';
  if(cur.trim() && label.includes(cur.trim())) return {ok:true, already:true, desc:cur.trim()};
  const items=[...document.querySelectorAll('.idtype')];
  const hit=items.find(e=>(e.innerText||'').trim()===label);
  if(!hit) return {ok:false, found:items.map(e=>(e.innerText||'').trim())};
  const trig=document.getElementById('date_desc');
  if(trig){ (trig.parentElement||trig).click(); }
  hit.click();
  return {ok:true, desc:(document.getElementById('date_desc')||{}).innerText||''};
}"""

# 点『生成报表』：filter 帧里 class 含 btn_search 的元素（抓包确认是 DIV.btn_search）
_CLICK_GEN = r"""()=>{
  let b=document.querySelector('.btn_search');
  if(!b){
    b=[...document.querySelectorAll('a,button,div,span,input')]
       .find(e=>((e.innerText||e.value||'').trim()==='生成报表'));
  }
  if(b){b.click(); return true;}
  return false;}"""

# 设置日期(begin=end=同一天) + 商品编码
_SET_FILTER = r"""(a)=>{
  const s=(id,v)=>{const e=document.getElementById(id);
    if(e){e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));
          e.dispatchEvent(new Event('change',{bubbles:true}));}};
  s('order_date_begin',a.d);s('order_date_end',a.d);s('sku_id',a.c);
  return {b:(document.getElementById('order_date_begin')||{}).value,
          e:(document.getElementById('order_date_end')||{}).value,
          sku:(document.getElementById('sku_id')||{}).value};}"""

# 读表：按表头文字定位『渠道』和目标数量列，返回每行 [渠道, 数量]
_READ_TABLE = r"""(col)=>{
  const norm=s=>(s||'').trim();
  // 报表对"确实没有匹配记录"会明确提示『当前条件下，没有任何数据！』——
  // 这是唯一确定性的"真 0"信号(2026-08-20 用户发现)，比靠计时判空可靠得多。
  const bodyTxt=document.body?document.body.innerText:'';
  const noData=bodyTxt.indexOf('没有任何数据')>=0;
  const hc=tb=>{const tr=tb.querySelector('tr');
    return tr?[...tr.querySelectorAll('td,th')].map(c=>norm(c.innerText)):[];};
  let target=null,hdr=[];
  for(const tb of document.querySelectorAll('table')){
    const h=hc(tb); if(h.includes(col)){target=tb;hdr=h;break;}}
  if(!target) return {ok:false,noData:noData,tables:document.querySelectorAll('table').length};
  const iQty=hdr.indexOf(col);
  let iCh=hdr.findIndex(x=>x.includes('渠道')); if(iCh<0)iCh=0;
  const rows=[];
  for(const r of [...target.querySelectorAll('tr')].slice(1)){
    const cs=[...r.querySelectorAll('td,th')].map(c=>norm(c.innerText));
    if(cs.length<=Math.max(iQty,iCh))continue;
    if(!cs[iCh])continue;
    rows.push([cs[iCh],cs[iQty]]);
  }
  return {ok:true,noData:noData,rows};}"""


def _num(s) -> float:
    """报表单元格 -> 数量。**一律取绝对值**：实发/实退都是"经手了多少套"的量，
    口径是 |实发| + |实退|（实发0、实退1 记 1，不是 -1）。报表偶有把退货写成负数的
    表示法，若原样落库，下游总计会算成负数。"""
    try:
        s = str(s).replace(",", "").strip()
        f = abs(float(s)) if s else 0.0
        return int(f) if float(f).is_integer() else f
    except (ValueError, TypeError):
        return 0


class JstReport:
    def __init__(self, page, logger, metric: str, channels: List[str]):
        assert metric in SUBJECTS, metric
        self.page = page
        self.log = logger
        self.metric = metric
        self.spec = SUBJECTS[metric]
        self.channels = channels          # ["抖音","拼多多","淘宝","小红书"]
        self._last_load = 0.0             # 本报表相关请求的最近返回时刻（判断生成后刷新完成）

    # ---------- 帧定位 ----------
    def _frame(self, needle):
        for fr in self.page.frames:
            if needle in (fr.url or ""):
                return fr
        return None

    @staticmethod
    def _safe_eval(frame, js, arg=None):
        """帧内 evaluate，吞掉『导航中上下文销毁』等瞬时异常，返回 None。"""
        try:
            return frame.evaluate(js, arg) if arg is not None else frame.evaluate(js)
        except Exception:
            return None

    def open(self, timeout=90):
        url = BI.format(subject=self.spec["subject"])
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            self.page.goto(url, wait_until="commit", timeout=60000)
        # 监听本报表相关请求（含 channel.aspx 及各 tab 的数据回调），生成报表后据此判断刷新完成
        sub = self.spec["subject"]
        def on_resp(resp):
            try:
                if f"/{sub}/" in resp.url:
                    self._last_load = time.time()
            except Exception:
                pass
        self.page.context.on("response", on_resp)
        end = time.time() + timeout
        while time.time() < end:
            # 登录过期检测：未登录时报表URL会被重定向到 login.aspx / passport，秒级判定，不等满超时
            u = (self.page.url or "").lower()
            if any(k in u for k in ("login.aspx", "/login", "passport", "sso")):
                raise LoginRequired("登录态已过期（报表页被重定向到登录页），请重新登录")
            filt = self._frame(f"{sub}/filter.aspx")
            if filt and self._safe_eval(filt, "()=>!!document.getElementById('sku_id')"):
                self.log and self.log.info(f"[{self.metric}] 报表已打开")
                self._ensure_date_type(filt)     # 按口径切换『时间类型』(实退=进仓时间)
                # 关键：等首屏(默认条件)自动加载彻底结束，否则第一组会撞上这次导航
                self._settle(6)
                return True
            time.sleep(1)
        raise RuntimeError(f"[{self.metric}] 报表 filter 帧未就绪")

    def _ensure_date_type(self, filt):
        """把报表左上角『时间类型』切到本指标要求的项（实退=进仓时间）。
        切换后日期框 name 会变（登记时间=created → 进仓时间=io_date），据此校验。"""
        want = self.spec.get("date_type")
        if not want:
            return
        r = self._safe_eval(filt, _SWITCH_DATE_TYPE, want)
        time.sleep(1.2)
        name = self._safe_eval(filt, "()=>(document.getElementById('order_date_begin')||{}).name||''")
        desc = self._safe_eval(filt, "()=>(document.getElementById('date_desc')||{}).innerText||''")
        expect = self.spec.get("date_field")
        if expect and name != expect:
            msg = f"[{self.metric}] 时间类型切换未生效：期望 {want}({expect})，实际 {desc}({name})；结果={r}"
            self.log and self.log.error(msg)
            raise RuntimeError(msg)
        self.log and self.log.info(f"[{self.metric}] 时间类型已设为『{(desc or '').strip()}』(字段 {name})")

    def _settle(self, quiet=4, cap=15):
        """等『本报表相关请求安静下来』：连续 quiet 秒无新请求即认为加载完成。"""
        end = time.time() + cap
        while time.time() < end:
            if time.time() - self._last_load >= quiet:
                return
            time.sleep(0.5)

    # ---------- 查询一个产品组 ----------
    def query(self, date_str: str, codes: List[str]) -> Dict[str, float]:
        """返回 {渠道短名: 数量}，未出现的渠道=0。date_str='YYYY-MM-DD'。

        全 0 结果会**独立复核一次**：批量高频抓取时，报表"清空→填充"的空窗仍可能被读成假 0
        （实测 308 次批量里漏 1 次）。两次都为 0 才认定真的没数据；
        若复核读到数据，说明第一次是假 0，采用复核值并告警。"""
        res = self._query_retry(date_str, codes)
        if sum(res.values()) == 0:
            again = self._query_retry(date_str, codes)
            if sum(again.values()) != 0:
                self.log and self.log.warning(
                    f"[{self.metric}] {date_str} 复核纠正了一次假0：{again}")
                return again
        return res

    def _query_retry(self, date_str, codes):
        for attempt in range(3):
            res = self._query_once(date_str, codes)
            if res is not None:
                return res
            self.log and self.log.warning(f"[{self.metric}] 查询重试({attempt+1})")
            self._settle(2, 8)
        # 三次都没拿到可信结果：**抛错**而不是写 0——写 0 会污染数据且看不出来
        raise RuntimeError(f"[{self.metric}] {date_str} 查询多次未取到可信结果（拒绝写入 0）")

    def _query_once(self, date_str, codes):
        sub = self.spec["subject"]; col = self.spec["qty_col"]
        filt = self._frame(f"{sub}/filter.aspx")
        if not filt or not self._safe_eval(filt, "()=>!!document.getElementById('sku_id')"):
            self.open()
            filt = self._frame(f"{sub}/filter.aspx")
        ch = self._frame(f"{sub}/channel.aspx")
        sig_before = self._sig(ch, col)          # 生成前的表格签名(上一组的数据)
        if self._safe_eval(filt, _SET_FILTER, {"d": date_str, "c": ",".join(codes)}) is None:
            return None                          # 设参时上下文被毁，整轮重试
        before_load = self._last_load           # 生成前本报表最近请求时刻
        if not self._safe_eval(filt, _CLICK_GEN):
            return None
        time.sleep(1.0)                          # 让生成触发的刷新先启动

        # 判定刷新完成。**核心规律**(2026-08-17 实测)：报表刷新时表格「先清空、再填充」，
        # 而两个报表「真的没数据」时的表现不同：
        #   - 实发(销售主题分析)：即使当天没发货，也仍会渲染 4 个渠道行、数值为 0
        #     → 因此【0 行 = 一定还在加载中】，绝不能当成结果，否则就是假 0
        #   - 实退(售后主题分析)：真的没有售后时，表格【没有任何行】
        #     → 0 行有歧义
        # 旧逻辑把"0 行"一律当成"数据=0"，是此前大量假 0 的根源。
        #
        # **2026-08-31 再次收紧**：实退原先还留了一条退路——"表格持续为空 5 次 / 3 秒
        # 且网络安静 2.5 秒"就认定真 0。实测这条启发式仍会误判，而且会**连续两次都误判**：
        # 8/23 糯玉米机械手刹连读两次都得到全 0，而该格实际是 抖音2/淘宝1/小红书1
        # （随后连查 8 次全部复现为正确值）。这也骗过了 query() 里的"全0复核"。
        # 因此彻底取消该退路：**两个报表的 0 行都一律视为"还在加载"**，
        # 唯一能认定真 0 的信号是报表明说『当前条件下，没有任何数据！』(noData)。
        # 代价：真空日要多等到该提示出现——而提示本身就是渲染完成的标志，不会白等；
        # 到点仍拿不到可信结果就返回 None → 整轮重试 → 三次仍失败则抛错，绝不写 0。
        # 偶尔会卡在"表格空 + 没有提示"的状态（实测能自行恢复）。既然不再接受空表当 0，
        # 就在本轮内重触发一次『生成』把它推醒，避免白等到超时再走外层重试。
        deadline = time.time() + 30
        regen_at = time.time() + 15              # 到点仍无行无提示 → 重点一次生成
        regenerated = False
        last = None
        nodata_streak = 0
        while time.time() < deadline:
            if not regenerated and time.time() >= regen_at:
                regenerated = True
                self.log and self.log.warning(
                    f"[{self.metric}] {date_str} 15 秒内表格既无数据行也无『没有任何数据』提示，"
                    "重新触发一次生成")
                self._safe_eval(filt, _CLICK_GEN)
                before_load = self._last_load
                time.sleep(1.0)
            ch = self._frame(f"{sub}/channel.aspx") or ch
            r = self._safe_eval(ch, _READ_TABLE, col) if ch else None
            # 报表明说『当前条件下，没有任何数据！』= 确定性的真 0，直接采信(需已刷新+连续2次一致)
            if r and r.get("noData"):
                nodata_streak += 1
                if (self._last_load > before_load) and nodata_streak >= 2:
                    return {c: 0 for c in self.channels}
                time.sleep(0.5)
                continue
            nodata_streak = 0
            if r and r.get("ok"):
                rows = r["rows"]
                sig = self._sig_of(rows)
                reloaded = self._last_load > before_load
                if rows:                                   # ---- 有行 = 已渲染 ----
                    if sig == last and (sig != sig_before or reloaded):
                        return self._map_rows(rows)
                # ---- 0 行：实发、实退一律只当"还在加载"，继续等 noData 提示或数据行 ----
                last = sig
            time.sleep(0.5)

        # 超时兜底：只接受有行的结果；否则返回 None 触发整轮重试（宁可重试也不写假 0）
        r = self._safe_eval(ch, _READ_TABLE, col) if ch else None
        if r and r.get("noData"):
            return {c: 0 for c in self.channels}
        if r and r.get("ok") and r["rows"]:
            return self._map_rows(r["rows"])
        return None

    def _sig(self, frame, col):
        r = self._safe_eval(frame, _READ_TABLE, col) if frame else None
        try:
            return self._sig_of(r["rows"]) if r and r.get("ok") else None
        except Exception:
            return None

    @staticmethod
    def _sig_of(rows):
        return "|".join(f"{a}={b}" for a, b in rows)

    def _map_rows(self, rows) -> Dict[str, float]:
        # 累加：同一渠道可能有多行（如售后按『类型』普通退货/换货/补发拆成多行），需求和；
        # 汇总行『##』渠道为空，不匹配任何渠道前缀，自动排除。
        out = {c: 0 for c in self.channels}
        for ch_name, qty in rows:
            for c in self.channels:
                if ch_name.startswith(c):   # 报表渠道如『小红书-劲风车生活的店』按前缀匹配
                    out[c] += _num(qty)
                    break
        return out
