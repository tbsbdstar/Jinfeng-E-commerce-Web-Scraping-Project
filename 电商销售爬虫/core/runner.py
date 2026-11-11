# -*- coding: utf-8 -*-
"""执行器核心：命令行菜单 + 抓取调度 + 定时任务。被各平台/统一执行器复用。
- 普通平台(小红书/抖音/拼多多)：脚本自动开无头浏览器抓取。
- 淘宝：CDP 连接真人登录的 Chrome(端口9222)抓取。
所有平台写同一份主数据 + 月度Excel。
"""
from __future__ import annotations
import sys
import os
import time
import socket
import subprocess
import datetime as _dt

from core import config, notify
from core import calendar_utils as cal
from core.browser import BrowserSession
from core.master_data import MasterData
from core import excel_render
from platforms import get_crawler
from platforms.base import LoginRequired

CDP_PORT = 9222
CDP = f"http://127.0.0.1:{CDP_PORT}"   # 用 127.0.0.1 避免 localhost 解析成 IPv6(::1) 连不上


def _cdp_port_open() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", CDP_PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _ensure_taobao_chrome(c, logger) -> bool:
    """确保淘宝调试 Chrome(9222)在跑：没开就用 淘宝_cdp 持久 profile 自动拉起并等端口就绪。
    登录态在该 profile 里持久，所以定时任务能无人值守(仅首次或失效时需人工登录一次)。"""
    if _cdp_port_open():
        return True
    chrome = c.get("chrome_path")
    prof = os.path.join(c["browser_profiles_dir"], "淘宝_cdp")
    if not chrome or not os.path.exists(chrome):
        logger.error("未找到 Chrome 可执行文件，无法自动开淘宝调试浏览器")
        return False
    logger.info("淘宝调试 Chrome 未开，自动拉起中…")
    try:
        subprocess.Popen(
            [chrome, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={prof}",
             "https://sycm.taobao.com/portal/home.htm"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except Exception as e:
        logger.error(f"自动拉起淘宝 Chrome 失败：{e}")
        return False
    for _ in range(30):                # 最多等 ~30s
        if _cdp_port_open():
            time.sleep(4)              # 端口起来后再给页面点加载时间
            return True
        time.sleep(1)
    logger.error("淘宝 Chrome 端口 30s 内未就绪")
    return False


# ---------- 抓取 ----------
def _crawl_taobao(date_str, master, logger, c):
    from playwright.sync_api import sync_playwright
    _ensure_taobao_chrome(c, logger)   # 没开就自动拉起(定时无人值守)
    p = sync_playwright().start()
    try:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception:
            raise LoginRequired("未连接到淘宝 Chrome（自动拉起失败），请双击『淘宝Chrome.bat』并登录一次")
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "sycm.taobao" in pg.url), None) or ctx.pages[-1]
        cr = get_crawler("淘宝", page, logger)
        cr.crawl_method = c.get("crawl_method", "auto")   # api|page|auto
        data = cr.crawl(date_str)
        master.upsert(date_str, "淘宝", data["sales"], data["refund"], data["orders"])
        notify.clear_marker(c["output_dir"], "淘宝")   # 成功则清除失效标记
        return data
    except LoginRequired as e:
        # 淘宝登录过期/连不上：和其它平台一样生成标记文件 + 桌面通知，别让用户只能靠查数据才发现
        notify.notify_platform_failed(c["output_dir"], logger, "淘宝", date_str, str(e))
        raise
    finally:
        p.stop()


def _crawl_normal(platform, date_str, master, logger, c):
    prof = os.path.join(c["browser_profiles_dir"], platform)
    with BrowserSession(prof, c["chrome_path"], headless=True) as page:
        cr = get_crawler(platform, page, logger)
        cr.crawl_method = c.get("crawl_method", "auto")   # api|page|auto
        # 判定登录：负载高(如千牛占资源)时首页可能没加载完导致误判"未登录"，
        # 因此最多试 3 次、每次间隔递增，都说未登录才真判失败——避免一次抖动就误报缺数。
        logged = False
        for i in range(3):
            if cr.is_logged_in():
                logged = True
                break
            if i < 2:
                logger.warning(f"[{platform}] 第{i+1}次检测未登录，{(i+1)*5}s 后重试确认（防负载误判）")
                page.wait_for_timeout((i + 1) * 5000)
        if not logged:
            notify.notify_platform_failed(c["output_dir"], logger, platform, date_str, "登录态失效，需重新登录")
            raise LoginRequired(f"{platform} 未登录")
        data = cr.crawl(date_str)
        master.upsert(date_str, platform, data["sales"], data["refund"], data["orders"])
        notify.clear_marker(c["output_dir"], platform)
        return data


def _fmt(x):
    return f"{x:,.0f}"   # 群报告金额显示整数（不带小数点），带千分位


def build_push_text(master, dates, platforms):
    """企业微信推送文案：日报/区间汇总（各平台销售/退款/净/订单 + 合计）+ 已结束完整周的周报(净额环比)。"""
    from core import weekly_report as wr
    ds = sorted(set(cal.parse_date(d).isoformat() for d in dates))
    single = len(ds) == 1
    head = f"📊 电商销售日报 · {ds[0]}" if single else f"📊 电商销售汇总 · {ds[0]}~{ds[-1]}（{len(ds)}天）"
    lines = [head]
    tot_s = tot_r = 0.0
    tot_o = 0
    dup = []   # 与前一日销售+退款完全雷同的平台（可能未更新/错位）
    prev_day = (cal.parse_date(ds[0]) - _dt.timedelta(days=1)).isoformat() if single else None
    for p in platforms:
        agg = master.get(ds[0], p) if single else None
        if single:
            s = agg["sales"] if agg else 0.0
            r = agg["refund"] if agg else 0.0
            o = agg["orders"] if agg else 0
            pv = master.get(prev_day, p)
            if agg and pv and (s, r) != (0.0, 0.0) and s == pv["sales"] and r == pv["refund"]:
                dup.append(p)
        else:
            a = master.sum_dates(ds, p)
            s, r, o = a["sales"], a["refund"], a["orders"]
        tot_s += s; tot_r += r; tot_o += o
        lines.append(f"· {p}：销售 {_fmt(s)} / 退款 {_fmt(r)} / 净 {_fmt(s - r)} / 订单 {o}")
    lines.append(f"— 合计：销售 {_fmt(tot_s)} / 退款 {_fmt(tot_r)} / 净 {_fmt(tot_s - tot_r)} / 订单 {tot_o}")

    # 数据核对提醒：某平台今日销售+退款与前一日完全相同 → 可能平台未更新或抓取错位，提示人工核对
    if dup:
        lines.append(f"\n⚠ 数据核对提醒：{'、'.join(dup)} 今日销售和退款与前一日（{prev_day}）完全相同，"
                     f"可能平台数据未更新或抓取异常，请人工到后台核对该平台数据。")

    # 已结束完整周的周报（净额环比）——通常周一抓完周日时出现
    weeks = []
    for d in ds:
        m = cal.week_monday(cal.parse_date(d))
        if m not in weeks:
            weeks.append(m)
    wr_lines = []
    today = _dt.date.today()
    for m in sorted(weeks):
        if (m + _dt.timedelta(days=6)) < today and wr.week_has_data(master, m, platforms):
            wr_lines.append("· " + wr.build_week_line(master, m, platforms))
    if wr_lines:
        lines.append("\n📅 周报（净额环比）：")
        lines.extend(wr_lines)
    return "\n".join(lines)


def crawl_dates(platforms, dates, push=True):
    """抓取 platforms × dates，写主数据并刷新相关月份的月表；收尾推送企业微信群。
    push=False 用于补抓时避免逐次刷屏（可在末尾单独推）。"""
    c = config.load_config()
    logger = notify.get_logger(c["logs_dir"], "run")
    master = MasterData(c["master_file"])
    ok, fail = [], []
    total = len(dates) * len(platforms)
    idx = 0
    for ds in dates:
        for p in platforms:
            idx += 1
            print(f"\n→ [{idx}/{total}] 正在抓取【{p}】{ds}，约 20-40 秒，请稍候……", flush=True)
            try:
                data = _crawl_taobao(ds, master, logger, c) if p == "淘宝" else _crawl_normal(p, ds, master, logger, c)
                logger.info(f"[{p}] {ds} 成功：销售 {data['sales']} / 退款 {data['refund']} / 订单 {data['orders']}")
                # 立即写：哪个平台抓到就先保存主数据 + 刷新该月月表
                master.save()
                dt = cal.parse_date(ds)
                excel_render.render_month_file(c["output_dir"], dt.year, dt.month, master, c["platforms"])
                print(f"   ✅ 完成并已写入月表：销售 {data['sales']} / 退款 {data['refund']} / 订单 {data['orders']}", flush=True)
                ok.append(f"{p}:{ds}")
            except Exception as e:
                logger.error(f"[{p}] {ds} 失败：{e}")
                print(f"   ❌ 失败：{e}", flush=True)
                fail.append(f"{p}:{ds}")

    # 收尾统一重渲染：抓取过程中每天只刷「该天所属月」，但跨月周会把邻月的天灰显补进邻月表，
    # 那些灰显格若在抓完邻月边界日之前定格就会漏更新（见 calendar_utils.months_touching）。
    # 这里把所有被抓日期涉及到的月份（含相邻月）都用完整主数据再渲一次，确保跨月格子最终一致。
    months = []
    for ds in dates:
        for ym in cal.months_touching(cal.parse_date(ds)):
            if ym not in months:
                months.append(ym)
    for (yy, mm) in months:
        excel_render.render_month_file(c["output_dir"], yy, mm, master, c["platforms"])

    # 周报文本文件：把被抓日期里「已结束的完整周」各写一份 md（幂等；月表底部也已同步嵌入周报）
    try:
        from core import weekly_report
        written = weekly_report.write_completed_weeks(c["output_dir"], master, dates, c["platforms"])
        for pth in written:
            print(f"   📝 周报已更新：{pth}", flush=True)
    except Exception as e:
        logger.warning(f"周报生成失败（不影响数据）：{e}")

    # 抓完自检：数据全空 / 与前一天完全相同 → 推群叫人工核查（不阻断推送）
    try:
        check_data_anomaly(c, master, dates, platforms, logger)
    except Exception as e:
        logger.warning(f"数据异常自检失败（不影响数据）：{e}")

    # 企业微信群推送：日报/周报文字 + 最近日期所属月的月表 xlsx（配置 wecom.enabled=true 才推）
    if push and ok:
        push_report(c, master, dates, logger)

    print(f"\n===== 完成：成功 {len(ok)} 条，失败 {len(fail)} 条 =====" + (f"（失败：{fail}）" if fail else ""), flush=True)


def check_data_anomaly(c, master, dates, platforms, logger):
    """抓完自检两类数据异常，发现就推企业微信群叫人工核查：
       ① 当天某平台销售额为 0（整天没数=多半没真正取到）
       ② 当天某平台数据与前一天完全相同（页面/接口返回了旧数据）
    只提醒、不改数据。"""
    import datetime as _dt
    from core import wecom
    alerts = []
    for ds in dates:
        for p in platforms:
            row = master.get(ds, p)
            if not row:
                # 该平台当天压根没抓到数据(抓取报错/登录失败等)——这就是"当天数据为空"，必须报警
                alerts.append(f"· {ds} {p}：**当天没有任何数据**(抓取失败)，需人工补抓")
                continue
            cur = (row["sales"], row["refund"], row["orders"])
            if cur[0] == 0:
                alerts.append(f"· {ds} {p}：销售额为 0（退款{cur[1]} 订单{cur[2]}），疑似没取到数据")
                continue
            prev = (cal.parse_date(ds) - _dt.timedelta(days=1)).isoformat()
            prow = master.get(prev, p)
            if prow and (prow["sales"], prow["refund"], prow["orders"]) == cur:
                alerts.append(f"· {ds} {p}：与前一天 {prev} 数据【完全相同】"
                              f"（销售{cur[0]} 退款{cur[1]} 订单{cur[2]}），疑似取到旧数据")
    if not alerts:
        return
    text = ("⚠️【电商数据异常·请人工核查】\n" + "\n".join(alerts) +
            "\n\n请到对应平台后台核对该日数据；若确实取错，用抓取工具选 2 重抓该天即可。")
    logger.error("数据异常：\n" + "\n".join(alerts))
    notify.send_toast("电商爬虫·数据异常", alerts[0])
    try:
        wecom.push(c.get("wecom"), text=text, logger=logger)
    except Exception as e:
        logger.warning(f"数据异常推送失败：{e}")


def push_report(c, master, dates, logger):
    """把 dates 的日报/周报文字 + 所属月 xlsx 推送到企业微信群（wecom.enabled=true 才推）。"""
    try:
        from core import wecom
        text = build_push_text(master, dates, c["platforms"])
        latest = cal.parse_date(max(dates))
        xlsx = excel_render.month_file_path(c["output_dir"], latest.year, latest.month)
        wecom.push(c.get("wecom"), text=text, file_path=xlsx, logger=logger)
    except Exception as e:
        logger.warning(f"企业微信推送失败（不影响数据）：{e}")


# ---------- 定时任务(Windows 任务计划) ----------
def _task_name(title):
    return f"电商爬虫_{title}"


def _schedule_create(title, script_path, time_str):
    tn = _task_name(title)
    tr = f'"{sys.executable}" "{script_path}" --auto'
    r = subprocess.run(["schtasks", "/create", "/tn", tn, "/tr", tr, "/sc", "daily",
                        "/st", time_str, "/f"], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"✅ 已设定：每天 {time_str} 自动抓取（任务名 {tn}）")
    else:
        print(f"❌ 设定失败：{r.stderr or r.stdout}")


def _schedule_delete(title):
    tn = _task_name(title)
    r = subprocess.run(["schtasks", "/delete", "/tn", tn, "/f"], capture_output=True, text=True)
    print(f"✅ 已取消定时（{tn}）" if r.returncode == 0 else f"（无定时或取消失败：{(r.stderr or r.stdout).strip()}）")


# ---------- 命令行菜单 ----------
def menu(platforms, title, script_path):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    while True:
        print(f"\n====== {title} 数据抓取 ======")
        print(" 1) 抓昨天")
        print(" 2) 抓指定某天       （如 2026-08-07）")
        print(" 3) 抓一段日期范围   （如 2026-08-01 2026-08-09）")
        print(" 4) 设为每天定时自动抓")
        print(" 5) 取消定时")
        print(" 6) 测试企业微信群推送")
        print(" 0) 退出")
        ch = input("请选择：").strip()
        try:
            if ch == "1":
                crawl_dates(platforms, [cal.yesterday().isoformat()])
            elif ch == "2":
                d = input("日期 YYYY-MM-DD：").strip()
                crawl_dates(platforms, [cal.parse_date(d).isoformat()])
            elif ch == "3":
                s = input("起始 YYYY-MM-DD：").strip()
                e = input("结束 YYYY-MM-DD：").strip()
                dates = [x.isoformat() for x in cal.date_range(cal.parse_date(s), cal.parse_date(e))]
                crawl_dates(platforms, dates)
            elif ch == "4":
                t = input("每天几点抓（HH:MM，如 09:30）：").strip()
                _schedule_create(title, script_path, t)
            elif ch == "5":
                _schedule_delete(title)
            elif ch == "6":
                from core import wecom
                c = config.load_config()
                if not (c.get("wecom") or {}).get("enabled"):
                    print("⚠ 企业微信推送未开启：请在 config.json 的 wecom 里填 webhook 并把 enabled 设为 true。")
                else:
                    wecom.test(c["wecom"], logger=notify.get_logger(c["logs_dir"], "run"))
                    print("已发送测试消息，请到群里查看（若没收到，检查 webhook 是否正确）。")
            elif ch == "0":
                break
            else:
                print("无效选择")
        except Exception as e:
            print(f"出错：{e}")


def run_auto(platforms):
    """定时任务入口：抓昨天。
    数据准确性靠 build_push_text 里的「连续两天销售/退款雷同」提醒兜底（雷同=可能未更新/错位→群里提示人工核对）。"""
    crawl_dates(platforms, [cal.yesterday().isoformat()])
