# -*- coding: utf-8 -*-
"""常开浏览器 · 聚水潭抓取工具（实发/实退）。

CDP 连接常开 Chrome(9333) → 复用它已登录的聚水潭会话 → 走【聚水潭爬虫现有全套逻辑】
（逐产品查实发/实退 → 写主数据 → 渲染周表 → 推企业微信群：当日数据文字 + 周表附件）。

与独立版（D:\\claude workspace1\\聚水潭爬虫\\运行.bat）的唯一区别：
  独立版每次开一个无头浏览器（用自己的 browser_profiles/聚水潭 登录态，闲置久了易过期）；
  本版不开浏览器，直接借用常开 Chrome 里那个一直开着的聚水潭标签页的登录态。
数据、周表、周报、推送目标群 **完全一致**（写同一份 outputs），两者可互为备份、随时切换。

用法：
    python 常开抓取-聚水潭.py            # 命令行菜单
    python 常开抓取-聚水潭.py --auto      # 定时入口：抓昨天（周一另出上周周报）
"""
import sys
import os
import time
import datetime as _d

JST = r"D:\claude workspace1\聚水潭爬虫"
sys.path.insert(0, JST)

from core import runner, config, notify, wecom  # noqa: E402
from core import calendar_utils as cal          # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 9333
CDP = f"http://127.0.0.1:{PORT}"


def _cdp_alive() -> bool:
    """先用 DevTools 的 HTTP 端点探活：能返回版本号说明 Chrome 本身是好的。
    （实测遇到过端口通、HTTP 正常，但 Playwright 的 CDP 握手卡死，故两者分开判断。）"""
    import urllib.request, json as _json
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=8) as r:
            return bool(_json.loads(r.read().decode("utf-8")).get("Browser"))
    except Exception:
        return False


def _connect(p, tries=3, timeout_ms=45000):
    """连 CDP：带超时 + 重试。connect_over_cdp 默认不超时，遇到半开连接会一直挂着
    （2026-08-19 实测卡了 3 分钟才失败），故显式给 timeout 并重试。"""
    last = None
    for i in range(tries):
        try:
            return p.chromium.connect_over_cdp(CDP, timeout=timeout_ms)
        except Exception as e:
            last = e
            print(f"   CDP 连接失败({i+1}/{tries})：{str(e)[:60]}")
            if i < tries - 1:
                time.sleep(10)
    raise last


def _with_cdp(fn):
    """连上常开 Chrome，把它的上下文交给 fn(ctx)。结束只断开 CDP，不关那个 Chrome。"""
    p = sync_playwright().start()
    try:
        try:
            browser = _connect(p)
        except Exception as e:
            if _cdp_alive():
                msg = (f"常开 Chrome 还开着(端口 {PORT} 能正常响应)，但调试连接握手失败："
                       f"{str(e)[:80]}\n多为上次抓取残留的半开连接所致。"
                       "处理：把常开 Chrome 窗口关掉，再双击『常开登录.bat』重新启动"
                       "（登录态不会丢），然后手动补抓一次。")
            else:
                msg = (f"连不上常开 Chrome（端口 {PORT} 无响应）。"
                       "请双击『常开登录.bat』启动常开 Chrome 并登录聚水潭。")
            print("[X] " + msg)
            try:
                c = config.load_config()
                wecom.push(c["wecom"], text="【聚水潭·常开抓取失败】\n" + msg,
                           logger=notify.get_logger(c["logs_dir"], "常开聚水潭"))
            except Exception:
                pass
            return None
        ctx = browser.contexts[0]
        return fn(ctx)
    finally:
        p.stop()      # 只断开连接，常开 Chrome 继续开着

def crawl(dates, push=True, metrics=None):
    """抓指定日期（复用聚水潭 runner 的全部逻辑，只把浏览器换成常开 Chrome）。"""
    return _with_cdp(lambda ctx: runner.crawl_dates(dates, push=push, metrics=metrics, ctx=ctx))


def run_auto():
    """定时入口：平日抓昨天；**周一改为把上周整周重新校验一遍**，再出周报。

    为什么周一要整周复核（2026-08-31 决定）：
      日常单次抓取仍会偶发漏记——8/17~8/30 两周事后复核补回 17 套实退、13 个格子，
      而同期用"连续跑完"的方式抓的 8/1~8/16 只漏 1 套。冷启动首查最容易踩中渲染未完成。
      周一花一次整周复核就能把这类残留清掉，周报也才是准的。
    为什么不必"先抓昨天再校验"：
      昨天(周日)本来就属于上周，已包含在整周里，单独再抓一次是重复劳动。
    耗时：约 7~8 分钟（7天 × 产品数 × 实发/实退；11 个产品实测 154 次 / 7.0 分钟），
      加上出周报约 1 分钟，周一整体约 8 分钟；10:15 启动约 10:23 推周报。
    校验期间 push=False，周报统一推一次，群里不会多出文件。"""
    is_monday = _d.date.today().weekday() == 0
    yday = cal.yesterday().isoformat()
    if not is_monday:
        crawl([yday], push=True)
        return

    wm = cal.week_monday(_d.date.today()) - _d.timedelta(days=7)   # 上周一
    week = [d.isoformat() for d in cal.week_days(wm)]
    print(f"周一：先把上周 {week[0]} ~ {week[-1]} 整周校验一遍（约 7 分钟）……", flush=True)
    outs = crawl(week, push=False)
    if outs:                                 # 抓失败(连不上/登录过期)时不发，免得推一堆 0
        try:
            runner.push_crawled_note([yday])       # 周日的日数据（文字，无附件）
        except Exception as e:
            print(f"周日日数据推送失败：{e}")
    try:
        runner.make_weekly_report()          # 缺的天它会自己补；此时整周已校验过
    except Exception as e:
        print(f"周报生成失败：{e}")


def menu():
    while True:
        print("\n====== 常开浏览器抓取 · 聚水潭(实发/实退) ======")
        print(" 1) 抓昨天")
        print(" 2) 抓指定某天       （如 2026-08-16）")
        print(" 3) 抓一段日期范围   （如 2026-08-01 2026-08-09）")
        print(" 4) 生成上周一句话周报（也可指定某周内任一天）")
        print(" 5) 管理产品分类编码（增删产品/商品编码，改完下次采集生效）")
        print(" 0) 退出")
        ch = input("请选择：").strip()
        try:
            if ch == "1":
                crawl([cal.yesterday().isoformat()])
            elif ch == "2":
                d = input("日期 YYYY-MM-DD：").strip()
                crawl([cal.parse_date(d).isoformat()])
            elif ch == "3":
                s = input("起始 YYYY-MM-DD：").strip()
                e = input("结束 YYYY-MM-DD：").strip()
                crawl([x.isoformat() for x in cal.date_range(cal.parse_date(s), cal.parse_date(e))])
            elif ch == "4":
                d = input("上周留空直接回车；或输入要统计那周内任一天 YYYY-MM-DD：").strip()
                wm = cal.week_monday(cal.parse_date(d)) if d else \
                    (cal.week_monday(_d.date.today()) - _d.timedelta(days=7))
                runner.make_weekly_report(wm.isoformat())
            elif ch == "5":
                from core import category as _cat
                _cat.manage_cli(config.load_config()["template_file"])
            elif ch == "0":
                break
            else:
                print("无效选择")
        except Exception as e:
            print(f"出错：{e}")


def _disable_quick_edit():
    """关掉本窗口的「快速编辑」，防止误点一下就把整轮采集冻住。

    与 常开抓取.py 里同名函数完全一致（两个脚本各自独立、不互相 import，故各留一份）。
    起因见 常开抓取.py 的注释：2026-09-08 定时任务窗口进入"选择"模式，
    程序卡在 print 上 3 分半、CPU 仅 0.9 秒，不按 Esc 会一直卡到看门狗强杀。
    只改本进程自己的控制台模式，不动注册表、不影响其它窗口。
    """
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)                     # STD_INPUT_HANDLE
        if not h or h == ctypes.c_void_p(-1).value:
            return False
        mode = ctypes.c_uint()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080                # 不带这个位，改快速编辑不生效
        new = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
        return bool(k32.SetConsoleMode(h, new))
    except Exception:
        return False


if __name__ == "__main__":
    if _disable_quick_edit():
        print("（已关闭本窗口的快速编辑：点窗口不会再冻结输出；复制请用右键→标记）\n")
    if "--auto" in sys.argv:
        run_auto()
    else:
        menu()
