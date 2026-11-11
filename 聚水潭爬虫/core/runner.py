# -*- coding: utf-8 -*-
"""
调度：抓取(逐日×每个产品×实发/实退) -> 主数据 -> 渲染周表。含命令行菜单与每日定时。
产品清单来自模板『分类编码』sheet，增删产品无需改代码（当前 12 个）。
数据有一天延时：默认抓『昨天』(脚本运行日的前一天)。
"""
from __future__ import annotations
import os, sys, subprocess
from typing import List
from . import config, notify, calendar_utils as cal, category, excel_render, wecom
from .master_data import MasterData
from .browser import BrowserSession
from .jst_report import JstReport, LoginRequired


def _open_with_retry(rpt, cn, log, times=3, delays=(20, 60, 120)):
    """打开报表带重试：定时任务常在开机初期跑，网络/系统未就绪会导致页面加载不出来。
    登录过期(LoginRequired)不重试——重试也没用，直接抛出去提示重登。"""
    import time as _t
    last = None
    for i in range(times):
        try:
            return rpt.open()
        except LoginRequired:
            raise
        except Exception as e:
            last = e
            if i < times - 1:
                d = delays[min(i, len(delays) - 1)]
                log.warning(f"[{cn}] 打开报表失败({i+1}/{times})：{e}；{d}s 后重试（可能是开机初期网络未就绪）")
                print(f"【{cn}】报表打开失败，{d} 秒后重试（{i+1}/{times}）……")
                _t.sleep(d)
    raise last


def _day_vector(master, d, products, channels):
    """某天的数据向量：[(渠道,产品) -> (实发, 实退)]，用于判空/判重复。"""
    return {(ch, p): (master.qty(d, ch, p, "shipped"), master.qty(d, ch, p, "returned"))
            for ch in channels for p in products}


def _check_data_anomaly(c, master, dates, log, push=True):
    """抓完自检两类数据异常，发现就推企业微信群叫人工核查：
       ① 当天实发合计为 0（整天没数=多半没真正取到）
       ② 当天数据与前一天完全相同（报表没刷新/取到了旧数据）
    只提醒、不改数据；实退经常整天为 0 属正常，故判空以『实发』为准。"""
    import datetime as _dt
    products = category.product_names(c["template_file"])
    channels = c["channels"]
    alerts = []
    for d in dates:
        cur = _day_vector(master, d, products, channels)
        ship = sum(v[0] for v in cur.values())
        ret = sum(v[1] for v in cur.values())
        if ship == 0:
            alerts.append(f"· {d}：实发合计为 0（实退 {ret}），疑似没取到数据")
            continue                      # 已判空，不再比对重复
        prev = (cal.parse_date(d) - _dt.timedelta(days=1)).isoformat()
        if master.has_date(prev):
            if _day_vector(master, prev, products, channels) == cur:
                alerts.append(f"· {d}：与前一天 {prev} 的数据【完全相同】（实发合计 {ship}），疑似报表未刷新")
    if not alerts:
        return
    text = ("⚠️【聚水潭数据异常·请人工核查】\n" + "\n".join(alerts) +
            "\n\n请到聚水潭手动查一下该日数据是否真的如此；"
            "若确实取错，双击『运行.bat』选 2 重抓该天即可。")
    log.error("数据异常：\n" + "\n".join(alerts))
    print("⚠ 数据异常：\n" + "\n".join(alerts))
    if not push:            # 手动回补时(push=False)只记录不推群，避免刷屏
        return
    notify.send_toast("聚水潭数据异常", alerts[0])
    wecom.push(c["wecom"], text=text, logger=log)


def _weeks_touching(dates):
    """收集这些日期涉及到的所有『周一』(去重)，用于渲染受影响的周表。"""
    seen = []
    for d in dates:
        m = cal.week_monday(cal.parse_date(d))
        if m not in seen:
            seen.append(m)
    return seen


def crawl_dates(dates: List[str], push: bool = True, metrics=None, ctx=None):
    """metrics=None 抓实发+实退；也可只抓其一，如 metrics=("returned",)（口径变更后重跑用）。

    ctx=None  ：自己开无头浏览器（默认，独立运行时用）。
    ctx=浏览器上下文：复用外部已登录的浏览器（『常开页面爬取工具』通过 CDP 连常开 Chrome 时传入），
                     此时不新开浏览器、结束也不关它，只用它的标签页取数。"""
    c = config.load_config()
    log = notify.get_logger(c["logs_dir"], "jst")
    groups = category.load_groups(c["template_file"])     # [(产品名, [编码])]，数量随分类编码变
    channels = c["channels"]
    suffix = c["channel_shop_suffix"]
    master = MasterData(c["master_file"])
    plan = [(m, cn) for m, cn in (("shipped", "实发"), ("returned", "实退"))
            if metrics is None or m in metrics]
    dates = [cal.parse_date(d).isoformat() for d in dates]
    total = len(dates) * len(groups) * len(plan)
    est_min = max(1, round(total * 1.3 / 60))
    log.info(f"开始抓取 {len(dates)} 天 × {len(groups)} 产品 × 实发/实退：{dates[0]}~{dates[-1]}")
    print(f"共 {total} 次查询，预计约 {est_min} 分钟，全程后台无窗口、请勿关闭本窗口，会持续打印进度……")

    profile = os.path.join(c["browser_profiles_dir"], "聚水潭")
    ok_cnt = 0; fail = []; done = 0

    def _do(ctx):
        """真正的抓取循环。ctx=浏览器上下文（自开的无头 / 外部常开 Chrome 均可）。
        返回 True 表示正常跑完，False 表示因登录过期或报表打不开提前退出。"""
        nonlocal ok_cnt, fail, done
        for metric, cn in plan:
            page = ctx.new_page()          # 每个报表独立标签页，互不干扰
            rpt = JstReport(page, log, metric, channels)
            print(f"正在打开【{cn}】报表并加载首屏（约 15 秒，属正常）……")
            try:
                _open_with_retry(rpt, cn, log)
            except LoginRequired as e:
                log.error(f"[{cn}] 登录已过期：{e}")
                notify.send_toast("聚水潭抓取·登录过期", "登录态已过期，请双击『登录.bat』重新登录后再抓。")
                wecom.push(c["wecom"], logger=log, text=(
                    "⚠️【聚水潭抓取失败·登录已过期】\n"
                    f"计划抓取 {dates[0]}" + (f"~{dates[-1]}" if len(dates) > 1 else "") + " 的数据，但聚水潭登录态已失效。\n"
                    "请在跑数据的那台电脑上双击『登录.bat』重新登录，"
                    "然后双击『运行.bat』选 1 补抓即可（次日 10:00 会自动恢复）。"))
                try: page.close()
                except Exception: pass
                return False
            except Exception as e:
                log.error(f"[{cn}] 打开报表失败：{e}")
                notify.send_toast("聚水潭抓取", f"{cn}报表打不开，请先双击『登录.bat』登录后重试")
                try: page.close()
                except Exception: pass
                return False
            for d in dates:
                for name, codes in groups:
                    try:
                        res = rpt.query(d, codes)
                        for ch, qty in res.items():
                            master.upsert(d, ch, name, **{metric: qty})
                        ok_cnt += 1; done += 1
                        log.info(f"[{done}/{total}][{cn}] {d} {name}: " +
                                 " ".join(f"{k}={v}" for k, v in res.items()))
                    except Exception as e:
                        done += 1; fail.append(f"{cn}/{d}/{name}")
                        log.error(f"[{done}/{total}][{cn}] {d} {name} 失败：{e}")
                master.save()      # 每天存一次，抓一半断了也不白抓
            try: page.close()
            except Exception: pass
        return True

    try:
        if ctx is not None:
            # 复用外部浏览器（常开 Chrome，经 CDP 传入）——用完不关它
            if not _do(ctx):
                return []
        else:
            with BrowserSession(profile, c["chrome_path"], headless=c.get("headless", True)) as page0:
                if not _do(page0.context):
                    return []
    finally:
        master.save()

    # 抓完自检：数据全空 / 与前一天完全相同 → 推群叫人工核查（不阻断后续渲染推送）
    try:
        _check_data_anomaly(c, master, dates, log, push=push)
    except Exception as e:
        log.warning(f"数据异常自检失败（不影响数据）：{e}")

    # 渲染受影响的周表，并写入「本周小结」(累计到已抓的最后一天，每天覆盖前一天)
    outdir = c["output_dir"]
    outs = []
    for m in _weeks_touching(dates):
        out = os.path.join(outdir, excel_render.week_out_name(m))
        note = _cumulative_note(c, master, m)      # Excel 里仍是「周一~已抓最后一天」的累计小结
        path = excel_render.render(c["template_file"], out, master, m, channels, suffix, note=note)
        outs.append(path)
        log.info(f"已渲染周表(含本周小结)：{path}")
        # 推送到企业微信群：文字=本次抓到的那几天(不累计，见 _crawled_note) + 同一份周表附件
        if push:
            day_note = _crawled_note(c, master, m, dates)
            if day_note:
                wecom.push(c["wecom"], text=day_note, file_path=path, logger=log)
    msg = f"完成：成功 {ok_cnt} 组" + (f"，失败 {len(fail)} 组" if fail else "，全部成功")
    log.info(msg)
    if c["notify"].get("toast", True):
        notify.send_toast("聚水潭抓取完成", msg + f"\n周表：{os.path.basename(outs[-1]) if outs else '无'}")
    return outs


# ---------- 定时 ----------
def _task_name():
    return "聚水潭爬虫_实发实退"


def _schedule_create(script_path, time_str):
    tr = f'"{sys.executable}" "{script_path}" --auto'
    r = subprocess.run(["schtasks", "/create", "/tn", _task_name(), "/tr", tr, "/sc", "daily",
                        "/st", time_str, "/f"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"❌ 设定失败：{r.stderr or r.stdout}")
        return
    _harden_task()
    print(f"✅ 已设定：每天 {time_str} 自动抓昨天（任务名 {_task_name()}）")
    print("   已启用：错过时间自动补跑、用电池时也运行（关机/休眠错过当天会在开机后补跑）")


def _harden_task():
    """加固计划任务：错过启动后尽快补跑 + 不受电池限制。用 PowerShell 改任务设置。"""
    ps = (f"$n='{_task_name()}';"
          "$s=New-ScheduledTaskSettingsSet -StartWhenAvailable "
          "-DontStopIfGoingOnBatteries -AllowStartIfOnBatteries "
          "-ExecutionTimeLimit (New-TimeSpan -Hours 2);"
          "Set-ScheduledTask -TaskName $n -Settings $s | Out-Null")
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f"（任务加固失败，不影响基本定时：{e}）")


def _schedule_delete():
    r = subprocess.run(["schtasks", "/delete", "/tn", _task_name(), "/f"], capture_output=True, text=True)
    print(f"✅ 已取消定时" if r.returncode == 0 else f"（无定时或取消失败：{(r.stderr or r.stdout).strip()}）")


# ---------- 菜单 ----------
def menu(script_path):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    while True:
        print("\n====== 聚水潭 实发/实退 周数据抓取 ======")
        print(" 1) 抓昨天（默认，数据有一天延时）")
        print(" 2) 抓指定某天       （如 2026-08-10）")
        print(" 3) 抓一段日期范围   （如 2026-08-04 2026-08-10）")
        print(" 4) 设为每天定时自动抓（周一自动出上周周报）")
        print(" 5) 取消定时")
        print(" 6) 生成上周一句话周报（也可指定某周内任一天）")
        print(" 7) 测试企业微信群推送（发一条测试消息到群）")
        print(" 8) 管理产品分类编码（增删产品/商品编码，改完下次采集生效）")
        print(" 0) 退出")
        ch = input("请选择：").strip()
        try:
            if ch == "1":
                crawl_dates([cal.yesterday().isoformat()])
            elif ch == "2":
                d = input("日期 YYYY-MM-DD：").strip()
                crawl_dates([cal.parse_date(d).isoformat()])
            elif ch == "3":
                s = input("起始 YYYY-MM-DD：").strip()
                e = input("结束 YYYY-MM-DD：").strip()
                dates = [x.isoformat() for x in cal.date_range(cal.parse_date(s), cal.parse_date(e))]
                crawl_dates(dates)
            elif ch == "4":
                t = input("每天几点抓（HH:MM，如 09:30）：").strip()
                _schedule_create(script_path, t)
            elif ch == "5":
                _schedule_delete()
            elif ch == "6":
                import datetime as _d
                d = input("上周留空直接回车；或输入要统计那周内任一天 YYYY-MM-DD：").strip()
                wm = cal.week_monday(cal.parse_date(d)) if d else \
                    (cal.week_monday(_d.date.today()) - _d.timedelta(days=7))
                make_weekly_report(wm.isoformat())
            elif ch == "7":
                c = config.load_config()
                if not c["wecom"].get("enabled"):
                    print("企业微信未启用：请先在 config.json 把 wecom.enabled 设为 true 并填 webhook。")
                else:
                    log = notify.get_logger(c["logs_dir"], "jst")
                    wecom.test(c["wecom"], logger=log)
                    print("已发送测试消息，请到群里查看是否收到。")
            elif ch == "8":
                category.manage_cli(config.load_config()["template_file"])
            elif ch == "0":
                break
            else:
                print("无效选择")
        except Exception as e:
            print(f"出错：{e}")


# ---------- 一句话周报 ----------
def _fmt_range(a, b):
    """标题里的日期范围文字：同一天→『8.18』，跨天→『8.18-8.20』。"""
    return f"{a.month}.{a.day}" if a == b else f"{a.month}.{a.day}-{b.month}.{b.day}"


def _totals(master, dates, products, channels):
    """指定若干天：每产品 gross=各渠道各天实发合计, returned=实退合计（报表原始量，非负），
    net=gross-returned=净发货（与周表底部『净发货（总计）』同口径，实退>实发时可为负）。"""
    res = {}
    for p in products:
        g = r = 0
        for d in dates:
            for ch in channels:
                row = master.get(d, ch, p)
                g += float(row.get("shipped", 0) or 0)
                r += float(row.get("returned", 0) or 0)
        res[p] = {"gross": _clean(g), "returned": _clean(r), "net": _clean(g - r)}
    return res


def _clean(x):
    return int(x) if float(x).is_integer() else round(x, 2)


def _summary_text(c, master, dates, rng) -> str:
    """三行小结：这些天的按产品实发 / 实退 / 净发货。rng 只是行首的日期范围文字。

    口径（2026-09-10 业务方确认，与周表底部三行一一对应）：
        实发   = 报表『实发数量』直接求和（系统爬取的原始量，非负）
        实退   = 报表『实退数量』求和（同上）
        净发货 = 实发 − 实退，对应周表『净发货（总计）』；某产品实退>实发时为负，属正常。
    演变：9/10 上午曾因群里出现"-1套"把净发货整行去掉（只发实发/实退），当天业务方确认
        净发货口径保留，故改为三行并存——原始量与净值都在，便于核对。"""
    products = category.product_names(c["template_file"])
    tot = _totals(master, dates, products, channels=c["channels"])
    ship = "，".join(f"{p}{tot[p]['gross']}套" for p in products)
    ret_nz = [(p, tot[p]["returned"]) for p in products if tot[p]["returned"]]
    ret = "，".join(f"{p}{r}套" for p, r in ret_nz) if ret_nz else "无实退"
    net = "，".join(f"{p}{tot[p]['net']}套" for p in products)
    total_ship = _clean(sum(tot[p]["gross"] for p in products))
    total_ret = _clean(sum(tot[p]["returned"] for p in products))
    total_net = _clean(total_ship - total_ret)
    return (f"{rng}主要产品实发数据：{ship}（合计实发{total_ship}套）\n"
            f"{rng}主要产品实退数据：{ret}（合计实退{total_ret}套）\n"
            f"{rng}主要产品净发货数据：{net}（合计净发货{total_net}套）")


def _report_text(c, master, week_monday, end_date=None) -> str:
    """整周小结文本(周报 / Excel「本周小结」用)。数字始终对整周求和(未抓的天=0，故等于已抓天数的累计)；
    行首日期范围 = 周一 ~ end_date(默认周日；累计小结传入"当天")。"""
    import datetime as _d
    end = cal.parse_date(end_date) if end_date else (week_monday + _d.timedelta(days=6))
    return _summary_text(c, master, cal.week_days(week_monday), _fmt_range(week_monday, end))


def weekly_report_text(week_monday, end_date=None) -> str:
    c = config.load_config()
    master = MasterData(c["master_file"])
    return _report_text(c, master, cal.week_monday(cal.parse_date(week_monday)), end_date)


def _cumulative_note(c, master, week_monday):
    """本周小结(累计到已抓的最后一天)：抓到第几天就汇总周一~那天。没抓过则 None。"""
    wd = cal.week_days(week_monday)
    crawled = [d for d in wd if master.has_date(d)]
    if not crawled:
        return None
    return _report_text(c, master, week_monday, max(crawled).isoformat())


def _crawled_note(c, master, week_monday, dates):
    """每日推群的文案：只算『本次抓的那几天』里属于这一周的天，不再累计。
    （以前每天推「周一~当天」的累计数：17、17-18、17-19…，重复且越来越长；
      现在 19 号跑就只发 18 号那天的数。附件周表不变，仍是整周累计。）
    多天一起抓(补抓/范围抓)时合并成一个区间，如『8.1-8.9』。没有属于这周的天则 None。"""
    ds = sorted(d for d in (cal.parse_date(x) for x in dates) if cal.week_monday(d) == week_monday)
    if not ds:
        return None
    rng = _fmt_range(ds[0], ds[-1])
    return f"【聚水潭·{rng}数据】\n" + _summary_text(c, master, ds, rng)


def push_crawled_note(dates):
    """只把『这几天的数据文案』推群，不带附件。周一专用：
    周一那次抓取(抓周日)不推周表——周报会带同一份文件，避免群里收到两份一样的 Excel；
    但『每天发前一天的数据』这条不能断，所以周日的日数据用文字单发一条。"""
    c = config.load_config()
    log = notify.get_logger(c["logs_dir"], "jst")
    master = MasterData(c["master_file"])
    for m in _weeks_touching(dates):
        t = _crawled_note(c, master, m, dates)
        if t:
            wecom.push(c["wecom"], text=t, logger=log)


def make_weekly_report(week_monday=None):
    """生成上周(默认)一句话周报：写 txt + 打印 + 桌面通知。"""
    c = config.load_config()
    log = notify.get_logger(c["logs_dir"], "jst")
    import datetime as _d
    if week_monday is None:                 # 上周 = 本周一往前 7 天（本周的前一周）
        week_monday = cal.week_monday(_d.date.today()) - _d.timedelta(days=7)
    week_monday = cal.week_monday(cal.parse_date(week_monday))

    # 若这周有些天还没抓过（且不是今天/未来日），先自动补抓，保证报表有数据
    week_dates = cal.week_days(week_monday)
    yday = cal.yesterday()
    master0 = MasterData(c["master_file"])
    missing = [d for d in week_dates if d <= yday and not master0.has_date(d)]
    if missing:
        log.info(f"该周有 {len(missing)} 天未抓过，先自动补抓：{missing[0]}~{missing[-1]}")
        print(f"该周部分日期未抓，先自动补抓 {len(missing)} 天……")
        crawl_dates([d.isoformat() for d in missing], push=False)  # 补抓不逐次推，末尾统一推周报

    m = week_monday; sun = m + __import__("datetime").timedelta(days=6)
    y, w, _ = m.isocalendar()
    # 1) 把这段话写进该周『实发数量』sheet（重渲一次，带 note；口径=累计到已抓的最后一天）
    master = MasterData(c["master_file"])
    text = _cumulative_note(c, master, week_monday) or weekly_report_text(week_monday)
    out = os.path.join(c["output_dir"], excel_render.week_out_name(week_monday))
    xlsx = excel_render.render(c["template_file"], out, master, week_monday,
                               c["channels"], c["channel_shop_suffix"], note=text)
    log.info(f"周报已写入周表『实发数量』sheet：{xlsx}")
    # 2) 另存一份 txt 便于复制
    fname = f"周报_{y}年第{w:02d}周({m.month:02d}{m.day:02d}-{sun.month:02d}{sun.day:02d}).txt"
    path = os.path.join(c["output_dir"], fname)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text + "\n")
    log.info("一句话周报：\n" + text)
    print("\n" + "=" * 50 + "\n" + text + "\n" + "=" * 50)
    if c["notify"].get("toast", True):
        notify.send_toast("聚水潭上周周报", text.split("\n")[0])
    # 3) 推送到企业微信群：周报文字 + Excel 附件
    wecom.push(c["wecom"], text="【聚水潭·上周周报】\n" + text, file_path=xlsx, logger=log)
    return path, text


def run_auto():
    """定时任务入口：平日抓昨天；**周一把上周整周重新校验一遍**，再出周报。

    为什么周一要整周复核（2026-08-31 决定）：
      日常单次抓取仍会偶发漏记——8/17~8/30 两周事后复核补回 17 套实退、13 个格子，
      而同期"连续跑完"方式抓的 8/1~8/16 只漏 1 套。冷启动首查最易踩中渲染未完成。
      周一花一次整周复核清掉残留，周报才准。
    不必"先抓昨天再校验"：昨天(周日)本就属于上周，已含在整周里。
    耗时：约 7~8 分钟（7天 × 产品数 × 实发/实退；11 个产品实测 154 次 / 7.0 分钟，2.7 秒/次）+ 周报约 1 分钟。
    校验期间 push=False，周报统一推一次，群里不会多出文件。"""
    import datetime as _d
    is_monday = _d.date.today().weekday() == 0
    yday = cal.yesterday().isoformat()
    if not is_monday:
        crawl_dates([yday], push=True)
        return

    wm = cal.week_monday(_d.date.today()) - _d.timedelta(days=7)   # 上周一
    week = [d.isoformat() for d in cal.week_days(wm)]
    print(f"周一：先把上周 {week[0]} ~ {week[-1]} 整周校验一遍（约 7 分钟）……", flush=True)
    outs = crawl_dates(week, push=False)
    if outs:                                # 抓失败(登录过期等)时不发，免得推一堆 0
        try:
            push_crawled_note([yday])       # 周日的日数据（文字，无附件）
        except Exception as e:
            print(f"周日日数据推送失败：{e}")
    try:
        make_weekly_report()                # 缺的天它会自己补；此时整周已校验过
    except Exception as e:
        print(f"周报生成失败：{e}")
