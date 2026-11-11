# -*- coding: utf-8 -*-
"""多平台电商经营数据自动采集与统计分析系统 V1.0 · 执行器（被界面以子进程方式调用，也可单独命令行用）。

只做一件事：按参数调用【现有工具】的抓取函数，把进度打到 stdout 由界面实时显示。
**不修改现有工具的任何文件**——只是 import 它们、调它们的函数，等同于你手动跑 bat。

用法示例：
    python worker.py --target ecom --mode cdp --platforms 淘宝,抖音 --start 2026-08-01 --end 2026-08-09
    python worker.py --target jst  --mode headless --start 2026-08-20 --end 2026-08-20 --push 1
    python worker.py --target jst  --weekly 2026-08-17        # 出某周周报
    python worker.py --restart-browser                        # 只重启常开浏览器
"""
import argparse
import datetime as dt
import importlib.util
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                     # 与界面共用同一套路径解析
ECOM, JST, CQ = paths.ECOM, paths.JST, paths.CQ

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def say(msg=""):
    print(msg, flush=True)


def load_module(path, name):
    """按文件路径加载模块（现有脚本是中文名，不能用普通 import）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def date_list(start, end):
    a = dt.date.fromisoformat(start)
    b = dt.date.fromisoformat(end)
    if b < a:
        a, b = b, a
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def restart_browser():
    """重启常开 Chrome 拿全新 CDP 连接（复用现有 常开抓取.py 里的 _restart_chrome）。"""
    say("正在重启常开浏览器（登录态在 profile 里，不会丢）……")
    sys.path.insert(0, ECOM)
    from core import config, notify
    mod = load_module(os.path.join(CQ, "常开抓取.py"), "cq_ecom")
    c = config.load_config()
    logger = notify.get_logger(c["logs_dir"], "采集")
    mod._restart_chrome(logger)
    say("常开浏览器已重启。")


# ------------------------------------------------------------------ 登录检测
JST_CHECK_URL = ("https://bi.erp321.com/app/daas/report/subject/adsorder/"
                 "default.aspx?_c=jst-epaas&epaas=true")


def report(platform, state, detail=""):
    """结构化输出，界面靠这行更新登录状态表。"""
    say(f"[[LOGIN]]{platform}|{state}|{detail}")


def _jst_logged_in(ctx):
    """开报表页看筛选帧出不出得来——出得来即已登录（同聚水潭 登录.py 的判断）。"""
    import time
    p = ctx.new_page()
    try:
        p.goto(JST_CHECK_URL, wait_until="domcontentloaded", timeout=40000)
        end = time.time() + 15
        while time.time() < end:
            for fr in p.frames:
                if "adsorder/filter.aspx" in (fr.url or ""):
                    return True
            if "login" in (p.url or "").lower():
                return False
            p.wait_for_timeout(800)
        return False
    finally:
        try:
            p.close()
        except Exception:
            pass


def check_login(mode, platforms):
    """逐平台检测登录状态。cdp=查常开浏览器里那些标签页；headless=查各项目自己的登录档案。"""
    sys.path.insert(0, ECOM)
    from core import config, notify
    from platforms import get_crawler
    ecom_list = [p for p in platforms if p != "聚水潭"]
    c = config.load_config()
    logger = notify.get_logger(c["logs_dir"], "采集")
    say(f"检测方式：{'常开浏览器里的标签页' if mode == 'cdp' else '各项目自己的登录档案（无头）'}")
    say("-" * 60)

    if mode == "cdp":
        from playwright.sync_api import sync_playwright
        mod = load_module(os.path.join(CQ, "常开抓取.py"), "cq_ecom")
        if not mod._cdp_alive():
            for p in platforms:
                report(p, "浏览器未运行", f"端口 {mod.PORT} 不通，先打开常开浏览器")
            say("常开浏览器没开，无法检测。请点「打开/重启常开浏览器」。")
            return
        pw = sync_playwright().start()
        try:
            browser = mod._connect(pw)
            ctx = browser.contexts[0]
            for p in ecom_list:
                say(f"→ 检测 {p} ……")
                try:
                    page = mod._find_page(ctx, p)
                    cr = get_crawler(p, page, logger)
                    # 三态：已登录 / 已失效(确实在登录页) / 页面未就绪(网络或加载问题，可重试)
                    ok, why = mod._login_state(cr, logger)
                    if ok:
                        report(p, "已登录", (page.url or "")[:80])
                    elif why == "logged_out":
                        report(p, "已失效", (page.url or "")[:80])
                    else:
                        report(p, "页面未就绪", "当前不在登录页，多半是加载慢或网络问题："
                                              + (page.url or "")[:60])
                except Exception as e:
                    report(p, "检测失败", str(e)[:80])
            if "聚水潭" in platforms:
                say("→ 检测 聚水潭 ……")
                try:
                    ok = _jst_logged_in(ctx)
                    report("聚水潭", "已登录" if ok else "已失效", "报表页可否打开")
                except Exception as e:
                    report("聚水潭", "检测失败", str(e)[:80])
        finally:
            pw.stop()
        return

    # ---- 独立无头：各项目自己的 browser_profiles ----
    from core.browser import BrowserSession
    for p in ecom_list:
        say(f"→ 检测 {p} ……")
        prof = os.path.join(c["browser_profiles_dir"], p)
        try:
            with BrowserSession(prof, c["chrome_path"], headless=True) as page:
                cr = get_crawler(p, page, logger)
                ok = cr.is_logged_in()
                report(p, "已登录" if ok else "已失效", (page.url or "")[:80])
        except NotImplementedError:
            report(p, "不适用", "淘宝无头模式过不了滑块，需用真人 Chrome 登录")
        except Exception as e:
            report(p, "检测失败", str(e)[:80])
    if "聚水潭" in platforms:
        say("→ 检测 聚水潭 ……")
        try:
            sys.path.insert(0, JST)
            import importlib
            jcfg = importlib.import_module("core.config").load_config()
            jbs = importlib.import_module("core.browser").BrowserSession
            prof = os.path.join(jcfg["browser_profiles_dir"], "聚水潭")
            with jbs(prof, jcfg["chrome_path"], headless=True) as page:
                report("聚水潭", "已登录" if _jst_logged_in(page.context) else "已失效", "报表页可否打开")
        except Exception as e:
            report("聚水潭", "检测失败", str(e)[:80])


def open_login_window():
    """打开/确保常开浏览器起来，并把各平台标签页开好，供人工登录。"""
    say("正在打开常开浏览器与各平台标签页……")
    sys.path.insert(0, ECOM)
    mod = load_module(os.path.join(CQ, "常开登录.py"), "cq_login")
    mod.main()
    say("已就绪。请在弹出的 Chrome 窗口里逐个标签登录，登录完不要关窗口，回来点「重新检测」。")


def run_ecom(mode, platforms, dates, push):
    say(f"【电商】{'常开浏览器' if mode == 'cdp' else '独立无头'}模式"
        f"｜平台 {'、'.join(platforms)}｜{dates[0]} ~ {dates[-1]}｜"
        f"{'推送企业微信' if push else '不推送'}")
    say("-" * 60)
    if mode == "cdp":
        sys.path.insert(0, ECOM)
        mod = load_module(os.path.join(CQ, "常开抓取.py"), "cq_ecom")
        mod.crawl(dates, platforms=platforms, push=push)
    else:
        sys.path.insert(0, ECOM)
        from core import runner
        runner.crawl_dates(platforms, dates, push=push)


def run_jst(mode, dates, push):
    say(f"【聚水潭】{'常开浏览器' if mode == 'cdp' else '独立无头'}模式"
        f"｜{dates[0]} ~ {dates[-1]}｜{'推送企业微信' if push else '不推送'}")
    say("-" * 60)
    if mode == "cdp":
        mod = load_module(os.path.join(CQ, "常开抓取-聚水潭.py"), "cq_jst")
        mod.crawl(dates, push=push)
    else:
        sys.path.insert(0, JST)
        from core import runner
        runner.crawl_dates(dates, push=push)


def verify_week(week_day, mode):
    """把某一周（默认上周）的实发+实退整周重采一遍，用于校验/清掉漏记。不推群。

    7 天 × 产品数 × 两个指标；11 个产品时实测 154 次查询、约 7 分钟（产品增加会等比变长）。
    与周一定时任务里做的是同一件事，这里供随时手动触发。"""
    import datetime as dtm
    sys.path.insert(0, JST)
    from core import calendar_utils as cal
    if week_day:
        wm = cal.week_monday(cal.parse_date(week_day))
    else:
        wm = cal.week_monday(dtm.date.today()) - dtm.timedelta(days=7)
    week = [d.isoformat() for d in cal.week_days(wm)]
    say(f"【整周校验】{week[0]} ~ {week[-1]}｜实发+实退 154 次查询｜约 7 分钟｜不推群")
    say("-" * 60)
    run_jst(mode, week, push=False)


def run_weekly(week_day):
    """生成某周的一句话周报（留空=上周）。走聚水潭现有 make_weekly_report。"""
    sys.path.insert(0, JST)
    from core import runner
    from core import calendar_utils as cal
    if week_day:
        wm = cal.week_monday(cal.parse_date(week_day))
    else:
        wm = cal.week_monday(dt.date.today()) - dt.timedelta(days=7)
    say(f"【聚水潭】生成 {wm.isoformat()} 那周的周报（缺的天会先自动补抓）")
    say("-" * 60)
    runner.make_weekly_report(wm.isoformat())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["ecom", "jst"])
    ap.add_argument("--mode", choices=["cdp", "headless"], default="cdp")
    ap.add_argument("--platforms", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--push", type=int, default=0)
    ap.add_argument("--weekly", nargs="?", const="", default=None)
    ap.add_argument("--restart-browser", action="store_true")
    ap.add_argument("--check-login", action="store_true")
    ap.add_argument("--verify-week", nargs="?", const="", default=None)
    ap.add_argument("--open-login", action="store_true")
    a = ap.parse_args()

    t0 = dt.datetime.now()
    say(f"开始时间：{t0.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        if a.restart_browser:
            restart_browser()
        elif a.open_login:
            open_login_window()
        elif a.check_login:
            pf = [p for p in a.platforms.split(",") if p.strip()]
            check_login(a.mode, pf or ["淘宝", "抖音", "拼多多", "小红书", "聚水潭"])
        elif a.verify_week is not None:
            verify_week(a.verify_week, a.mode)
        elif a.weekly is not None:
            run_weekly(a.weekly)
        else:
            dates = date_list(a.start, a.end)
            if a.target == "ecom":
                pf = [p for p in a.platforms.split(",") if p.strip()]
                run_ecom(a.mode, pf or ["淘宝", "抖音", "拼多多", "小红书"], dates, bool(a.push))
            else:
                run_jst(a.mode, dates, bool(a.push))
        used = (dt.datetime.now() - t0).total_seconds()
        say("-" * 60)
        say(f"[[DONE]] 任务结束，用时 {int(used // 60)} 分 {int(used % 60)} 秒")
        return 0
    except KeyboardInterrupt:
        say("[[ABORT]] 已被用户中止")
        return 130
    except Exception as e:
        say("-" * 60)
        say(f"[[FAIL]] 出错：{e}")
        say(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
