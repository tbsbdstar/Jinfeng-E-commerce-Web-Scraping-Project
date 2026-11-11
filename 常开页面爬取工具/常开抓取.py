# -*- coding: utf-8 -*-
"""常开浏览器 · 抓取工具。
CDP 连接常开 Chrome(9333)，逐平台：找到对应标签页 → 检测登录 →
  未登录：推企业微信群报错 + 跳过；已登录：走【现有 _crawl_api 取数逻辑】→ 写主数据。
抓完复用现有渲染月表/周报/企业微信推送。
- 完全复用电商爬虫的 platforms/core，数据写进同一份 outputs，报表/群与现有一致。
- 只改"浏览器来源"：从"每次开无头"改成"连常开 Chrome 的对应标签页"。
用法：
    python 常开抓取.py            # 命令行菜单
    python 常开抓取.py --auto      # 定时入口：抓昨天
"""
import sys
import os
import time

ECOM = r"D:\claude workspace1\电商销售爬虫"
sys.path.insert(0, ECOM)
from core import config, notify, excel_render, weekly_report, runner  # noqa: E402
from core import calendar_utils as cal  # noqa: E402
from core.master_data import MasterData  # noqa: E402
from platforms import get_crawler  # noqa: E402
from platforms.base import DataNotReady  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 9333
CDP = f"http://127.0.0.1:{PORT}"
PLATFORMS = ["淘宝", "抖音", "拼多多", "小红书"]
# 各平台标签页的域名特征（在常开 Chrome 的标签里按此找对应页）
DOMAIN = {"小红书": "xiaohongshu.com", "抖音": "jinritemai.com",
          "拼多多": "pinduoduo.com", "淘宝": "taobao.com"}
# 抓前强制刷新的数据页 + 就绪标志文本（常开标签久了会陈旧，刷新拿到新页面上下文/令牌后昨天数据才出）
REFRESH = {
    "淘宝":   ("https://sycm.taobao.com/portal/home.htm",              None),
    "抖音":   ("https://compass.jinritemai.com/shop",                  "成交金额"),
    "拼多多": ("https://mms.pinduoduo.com/sycm/stores_data/operation", "成交金额"),
    "小红书": ("https://ark.xiaohongshu.com/app-datacenter/overview",  "经营数据概览"),
}


def _push_fail(c, logger, title, msg):
    """整轮抓取起不来时推企业微信群叫人处理（推送失败不影响主流程）。"""
    try:
        from core import wecom
        body = ("【电商抓取失败·" + title + "】" + chr(10) + msg + chr(10) + chr(10)
                + "处理后请用『常开抓取.bat』选 1 补抓昨天。")
        wecom.push(c.get("wecom"), logger=logger, text=body)
    except Exception as e:
        logger.warning("失败提醒推送失败：" + str(e))


def _find_page(ctx, platform):
    """在常开 Chrome 的标签里找该平台的页；没有就新开一个（is_logged_in 会导航它到数据页）。"""
    marker = DOMAIN[platform]
    for pg in ctx.pages:
        try:
            if marker in (pg.url or ""):
                return pg
        except Exception:
            pass
    return ctx.new_page()


def _refresh(page, platform, logger):
    """抓前强制刷新该平台数据页并等它加载出来——常开标签开久了直接读会是陈旧内容
    （抖音实测：不刷新读不到前一天数据，刷新后才有）。gotot 数据页 = 一次全新加载。"""
    url, ready = REFRESH.get(platform, (None, None))
    if not url:
        return
    try:
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        if ready:                       # 等关键文字出现 = 页面数据真加载好了
            w = 0
            while w < 40:
                page.wait_for_timeout(2000)
                w += 2
                try:
                    if ready in (page.inner_text("body") or ""):
                        break
                except Exception:
                    pass
        else:
            page.wait_for_timeout(5000)
    except Exception as e:
        logger.warning(f"[{platform}] 刷新数据页出错（继续尝试抓取）：{e}")


# 页面确实被踢到登录页的特征（命中才算"登录态失效"）
_LOGIN_URL_MARKS = ("login", "signin", "sign_in", "passport", "sso", "account/auth")


def _login_state(cr, logger=None, tries=5):
    """判断登录状态，返回 (是否已登录, 原因)。

    原因取值：
      ""          已登录
      "logged_out"  页面确实跳到了登录页 —— 这才是真的要重登
      "not_ready"   页面没加载好 / 网络出错 —— **可重试，不该报"登录失效"**

    2026-09-03 教训：抓前重启浏览器后只等 3 秒就开抓，5 个标签页同时加载抢带宽，
    抖音、拼多多的登录判定在页面就绪前失败，被当成"登录态失效"推了群；
    而事后检测五个平台全部已登录、补抓 35 秒全成功。所以这里必须区分两种失败。
    """
    last_err = None
    for i in range(tries):
        try:
            if cr.is_logged_in():
                return True, ""
        except Exception as e:
            last_err = e
        if i < tries - 1:
            # 第 3 次起先刷一下页面再判，避免一直盯着一个没加载好的页面
            if i == 2:
                try:
                    cr.page.reload(timeout=30000, wait_until="domcontentloaded")
                except Exception as e:
                    last_err = e
            cr.page.wait_for_timeout(min(5000 * (i + 1), 15000))
    url = ""
    try:
        url = (cr.page.url or "").lower()
    except Exception:
        pass
    if any(m in url for m in _LOGIN_URL_MARKS):
        return False, "logged_out"
    if logger:
        logger.warning(f"[{cr.name}] 登录判定未通过，但当前页不是登录页"
                       f"（url={url[:80]}；最后异常={str(last_err)[:60]}）"
                       "，按『页面未就绪』处理，不判定为登录失效")
    return False, "not_ready"


def _warmup(ctx, platforms, logger, budget=150):
    """重启浏览器后的预热：等各平台标签页真正加载出就绪标志，再开始抓。

    不预热就开抓，页面还在加载时做登录判定会误判（见 _login_state 的说明）。
    budget 为总预算秒数，用完即返回，不阻塞整轮。"""
    t0 = time.time()
    logger.info("预热：等各平台数据页加载就绪……")
    for platform in platforms:
        left = budget - (time.time() - t0)
        if left <= 5:
            logger.warning("预热时间用尽，剩余平台不再等待")
            break
        url, ready = REFRESH.get(platform, (None, None))
        if not url:
            continue
        try:
            page = _find_page(ctx, platform)
            page.goto(url, timeout=min(60000, int(left * 1000)), wait_until="domcontentloaded")
            if not ready:
                page.wait_for_timeout(3000)
                continue
            waited = 0
            while waited < min(40, left):
                page.wait_for_timeout(2000)
                waited += 2
                try:
                    if ready in (page.inner_text("body") or ""):
                        break
                except Exception:
                    pass
            logger.info(f"预热 [{platform}] 用时约 {waited} 秒"
                        + ("" if waited < min(40, left) else "（未等到就绪标志）"))
        except Exception as e:
            logger.warning(f"预热 [{platform}] 出错（不影响后续抓取）：{str(e)[:80]}")
    logger.info(f"预热结束，合计 {int(time.time() - t0)} 秒")


def _crawl_wait_ready(cr, ds, logger, retries=6, wait_s=120):
    """抓取；若平台返回"数据未就绪"(如抖音昨天日结算恰在10点前后几分钟才 finalize)，
    则等待后重试，最多 retries 次（默认 6×120s≈12 分钟），直到取到或确认确实没有。
    只对 DataNotReady 重试；登录失效等其它异常立即抛出。"""
    for i in range(retries):
        try:
            return cr.crawl(ds)
        except DataNotReady as e:
            if i < retries - 1:
                logger.warning(f"[{cr.name}] {ds} 数据未就绪，{wait_s}s 后重试（{i+1}/{retries}）：{e}")
                print(f"   ⏳ {cr.name} {ds} 数据还没结算好，等 {wait_s}s 再试（{i+1}/{retries}）……", flush=True)
                cr.page.wait_for_timeout(wait_s * 1000)
            else:
                raise


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
    import time
    last = None
    for i in range(tries):
        try:
            return p.chromium.connect_over_cdp(CDP, timeout=timeout_ms)
        except Exception as e:
            last = e
            print(f"   CDP 连接失败({i+1}/{tries})：{str(e)[:60]}", flush=True)
            if i < tries - 1:
                time.sleep(10)
    raise last


def _restart_chrome(logger):
    """定时跑前重启常开 Chrome，拿一个全新的 CDP 连接——避免 Chrome 开太久/上次抓取 python 崩溃
    残留的半开连接把 CDP 端口"焐死"导致页面操作挂死(2026-08-20 实证:全新 Chrome 就不挂)。
    登录 cookie 在 profile 里，重启不丢。"""
    import subprocess
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          "Where-Object { $_.CommandLine -like '*常开Chrome*' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
        __import__("time").sleep(4)
    except Exception as e:
        logger.warning(f"重启常开Chrome-结束旧进程出错(继续)：{e}")
    try:
        spec = importlib.util.spec_from_file_location("cq_login", os.path.join(here, "常开登录.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.main()                       # 全新拉起 + 确保各平台标签打开
        __import__("time").sleep(3)
        logger.info("已重启常开 Chrome（全新 CDP）")
    except Exception as e:
        logger.warning(f"重启常开Chrome-重新拉起出错：{e}")


def _start_watchdog(deadline_s, logger):
    """看门狗：另开一个线程，超过 deadline_s 仍没结束就强制退出进程——
    防 CDP 卡死时整轮干挂到定时任务 2h 超时。线程不受主线程阻塞 I/O 影响，能正常触发。"""
    import threading
    import time as _t

    def _kill():
        _t.sleep(deadline_s)
        logger.error(f"看门狗：抓取超过 {deadline_s}s 仍未结束，判定卡死，强制退出。")
        try:
            notify.send_toast("⚠ 电商爬虫：抓取卡死已强制结束",
                              "疑似 CDP 卡死。请关掉常开 Chrome 窗口后双击『常开登录.bat』重启。")
        except Exception:
            pass
        os._exit(2)

    threading.Thread(target=_kill, daemon=True).start()


def crawl(dates, platforms=None, push=True, warmup=False):
    """warmup=True 时先预热各平台页面再开抓——定时任务抓前会重启浏览器，
    不预热就开抓会把"页面没加载好"误判成"登录态失效"（2026-09-03 实证）。"""
    platforms = platforms or PLATFORMS
    c = config.load_config()
    logger = notify.get_logger(c["logs_dir"], "常开抓取")
    master = MasterData(c["master_file"])
    ok, fail = [], []
    _start_watchdog(1800, logger)      # 30分钟总时限(含预热)：超了强制退出
    p = sync_playwright().start()
    try:
        # 先 HTTP 探活区分两种失败：Chrome 没开 vs 开着但 CDP 握手卡死
        if not _cdp_alive():
            msg = f"常开 Chrome 没开或端口 {PORT} 不通，请双击『常开登录.bat』启动并登录。"
            print(f"❌ {msg}")
            try:
                notify.send_toast("⚠ 电商爬虫：常开浏览器未运行", msg)
            except Exception:
                pass
            _push_fail(c, logger, "常开浏览器未运行", msg)
            return
        try:
            browser = _connect(p)              # 带超时+重试,避免半开连接把整轮挂死
        except Exception as e:
            msg = (f"常开 Chrome 开着但 CDP 握手失败(可能卡死)。请关掉常开 Chrome 窗口后，"
                   f"双击『常开登录.bat』重启(登录态不会丢)。")
            print(f"❌ {msg}")
            logger.error(f"CDP 握手失败：{e}")
            try:
                notify.send_toast("⚠ 电商爬虫：CDP连接卡死需重启浏览器", msg)
            except Exception:
                pass
            _push_fail(c, logger, "CDP 连接卡死", msg)
            return
        ctx = browser.contexts[0]
        if warmup:
            _warmup(ctx, platforms, logger)
        total = len(dates) * len(platforms)
        idx = 0
        for ds in dates:
            for platform in platforms:
                idx += 1
                print(f"\n→ [{idx}/{total}] 抓取【{platform}】{ds} ……", flush=True)
                try:
                    page = _find_page(ctx, platform)
                    cr = get_crawler(platform, page, logger)
                    cr.crawl_method = c.get("crawl_method", "auto")
                    logged, why = _login_state(cr, logger)
                    if not logged:
                        if why == "logged_out":       # 页面确实在登录页 = 真要重登
                            notify.notify_platform_failed(
                                c["output_dir"], logger, platform, ds,
                                "登录态失效，请在常开 Chrome 里重登该平台")
                            print(f"   ⚠ {platform} 未登录，已推群提醒并跳过（请在常开 Chrome 里重登）")
                        else:                          # 页面没就绪/网络错误 = 可重试，不推群不建标记
                            logger.error(f"[{platform}] {ds} 页面未就绪或网络异常，本次跳过"
                                         "（未判定为登录失效，不推群；稍后补抓即可）")
                            print(f"   ⚠ {platform} 页面未就绪/网络异常，跳过（不是登录问题，稍后补抓）")
                        fail.append(f"{platform}:{ds}")
                        continue
                    _refresh(page, platform, logger)           # 抓前强制刷新数据页(拿新上下文,避免陈旧读不到昨天)
                    data = _crawl_wait_ready(cr, ds, logger)   # 未就绪会自动等待重试(主要针对抖音)
                    master.upsert(ds, platform, data["sales"], data["refund"], data["orders"])
                    notify.clear_marker(c["output_dir"], platform)
                    master.save()
                    dt = cal.parse_date(ds)
                    excel_render.render_month_file(c["output_dir"], dt.year, dt.month, master, c["platforms"])
                    # 结果同时写日志文件：窗口滚过去/关掉之后还能回查（原先只 print 不记录）
                    logger.info(f"[{platform}] {ds} 销售 {data['sales']} / "
                                f"退款 {data['refund']} / 订单 {data['orders']}")
                    print(f"   ✅ 销售 {data['sales']} / 退款 {data['refund']} / 订单 {data['orders']}")
                    ok.append(f"{platform}:{ds}")
                except Exception as e:
                    logger.error(f"[{platform}] {ds} 失败：{e}")
                    print(f"   ❌ 失败：{e}")
                    fail.append(f"{platform}:{ds}")

        # 收尾：跨月周统一重渲染 + 周报文件（复用现有）
        months = []
        for ds in dates:
            for ym in cal.months_touching(cal.parse_date(ds)):
                if ym not in months:
                    months.append(ym)
        for (yy, mm) in months:
            excel_render.render_month_file(c["output_dir"], yy, mm, master, c["platforms"])
        try:
            weekly_report.write_completed_weeks(c["output_dir"], master, dates, c["platforms"])
        except Exception as e:
            logger.warning(f"周报生成失败（不影响数据）：{e}")

        # 抓完自检：数据全空 / 与前一天完全相同 → 推群叫人工核查
        try:
            runner.check_data_anomaly(c, master, dates, platforms, logger)
        except Exception as e:
            logger.warning(f"数据异常自检失败（不影响数据）：{e}")

        # 企业微信推送（复用现有 push_report）
        if push and ok:
            runner.push_report(c, master, dates, logger)

        print(f"\n===== 完成：成功 {len(ok)} 条，失败 {len(fail)} 条 ====="
              + (f"（失败：{fail}）" if fail else ""))
    finally:
        p.stop()   # 只断开 CDP，不关常开 Chrome


def run_auto():
    """定时入口：先重启常开 Chrome 拿全新 CDP(防卡死)，**预热各平台页面**，再抓昨天。

    预热是 2026-09-03 加的：重启后只等 3 秒就开抓，页面还在加载就做登录判定，
    抖音/拼多多被误判成"登录态失效"并推了群，而当时登录其实都是好的。"""
    c = config.load_config()
    logger = notify.get_logger(c["logs_dir"], "常开抓取")
    try:
        _restart_chrome(logger)
    except Exception as e:
        logger.warning(f"重启常开Chrome失败(继续用现有连接尝试)：{e}")
    crawl([cal.yesterday().isoformat()], warmup=True)


def menu():
    while True:
        print("\n====== 常开浏览器抓取 ======")
        print(" 1) 抓昨天")
        print(" 2) 抓指定某天       （如 2026-08-16）")
        print(" 3) 抓一段日期范围   （如 2026-08-01 2026-08-09）")
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
                dates = [x.isoformat() for x in cal.date_range(cal.parse_date(s), cal.parse_date(e))]
                crawl(dates)
            elif ch == "0":
                break
            else:
                print("无效选择")
        except Exception as e:
            print(f"出错：{e}")


def _disable_quick_edit():
    """关掉**本窗口**的「快速编辑」，防止误点一下就把整轮采集冻住。

    起因（2026-09-08 实证）：定时任务弹出的控制台窗口开着快速编辑，
    只要有一次鼠标左键落进窗口客户区，控制台就进入"选择"模式（标题栏出现「选择」），
    程序下一次 print 会被**无限阻塞**——当天 10:00 那轮抓完淘宝、抖音数据也已入库，
    却卡在打印下一行上停了 3 分半（CPU 仅 0.9 秒），不按 Esc 就会一直卡到看门狗强杀。

    这里只改本进程自己的控制台模式：不动注册表、不影响你其它的 cmd/PowerShell 窗口，
    窗口照旧显示、日志照旧实时滚动，只是点它不再会暂停输出。
    副作用：本窗口里想复制文字得用『右键 → 标记』，不能直接拖选。
    """
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)                     # STD_INPUT_HANDLE
        if not h or h == ctypes.c_void_p(-1).value:
            return False
        mode = ctypes.c_uint()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False                              # 没有控制台（被重定向/pythonw）
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
