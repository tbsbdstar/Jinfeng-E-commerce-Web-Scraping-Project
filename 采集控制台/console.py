# -*- coding: utf-8 -*-
"""多平台电商经营数据自动采集与统计分析系统 V1.0 —— 可视化操作界面。

一个独立的小程序，**不修改现有工具的任何文件**：所有抓取都是以子进程调用
worker.py，由它 import 现有工具的模块并调用其函数，等同于你手动双击 bat。

六个页签：
    启动         —— 选平台、选时间范围、选运行方式，一键采集，实时看输出
    浏览器/登录  —— 管理常驻浏览器、检测各平台登录状态、配置采集账号
    数据         —— 每日数据总览，发现缺口并一键补采
    定时         —— 查看/修改 Windows 计划任务
    日志         —— 翻阅各来源的运行日志
    报错         —— 汇总各日志里的错误与警告 + 登录失效标记，一屏看完
"""
import datetime as dt
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
WORKER = os.path.join(HERE, "worker.py")
SETTINGS = os.path.join(HERE, "settings.json")
LOGDIR = os.path.join(HERE, "logs")

import paths                     # 三个采集工程的位置：同级目录 → paths.json → 历史默认
ECOM, JST, CQ = paths.ECOM, paths.JST, paths.CQ

ECOM_OUT = os.path.join(ECOM, "outputs")
JST_OUT = os.path.join(JST, "outputs")
ECOM_MASTER = os.path.join(ECOM_OUT, "daily_master.csv")
JST_MASTER = os.path.join(JST_OUT, "jst_master.csv")

ECOM_PLATFORMS = ["淘宝", "抖音", "拼多多", "小红书"]
WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]
CDP_PORT = 9333
NOWIN = 0x08000000            # CREATE_NO_WINDOW：子进程不弹黑窗

UI_FONT = ("微软雅黑", 9)
UI_BOLD = ("微软雅黑", 9, "bold")
MONO = ("Consolas", 9)

LOG_SOURCES = [
    ("控制台", LOGDIR, "console_"),
    ("电商采集", os.path.join(ECOM, "logs"), "抓取_"),
    ("聚水潭采集", os.path.join(JST, "logs"), "抓取_"),
]
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s*(.*)$")


# ---------------------------------------------------------------- 小工具
def today():
    return dt.date.today()


def iso(d):
    return d.isoformat()


def week_monday(d):
    return d - dt.timedelta(days=d.weekday())


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        os.makedirs(HERE, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def console_log(text):
    """控制台自己的操作日志（与被调用工具的日志分开存）。"""
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        p = os.path.join(LOGDIR, f"console_{today():%Y%m%d}.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [INFO] {text}\n")
    except Exception:
        pass


def powershell(cmd, timeout=60):
    """跑一段 PowerShell 并拿 UTF-8 文本（中文任务名要靠这个不乱码）。"""
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
                           capture_output=True, timeout=timeout, creationflags=NOWIN)
        return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode
    except Exception as e:
        return "", str(e), -1


def cdp_alive():
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("Browser"))
    except Exception:
        return False


def open_folder(path):
    if os.path.isdir(path):
        os.startfile(path)
    else:
        messagebox.showwarning("打不开", f"目录不存在：\n{path}")


def open_file(path, what="文件"):
    if path and os.path.isfile(path):
        os.startfile(path)
    else:
        messagebox.showwarning("打不开", f"没找到{what}：\n{path or '（还没生成）'}")


def newest(folder, must_have, ext=".xlsx"):
    """目录里名字含 must_have 的最新文件（按修改时间）。找不到返回 ''。"""
    try:
        cand = [os.path.join(folder, f) for f in os.listdir(folder)
                if must_have in f and f.endswith(ext) and not f.startswith("~$")]
        return max(cand, key=os.path.getmtime) if cand else ""
    except Exception:
        return ""


# ------------------------------------------------- 读主数据（只读，不写）
def read_ecom_master():
    """{日期: {平台: (销售, 退款, 订单)}}"""
    import csv
    out = {}
    try:
        with open(ECOM_MASTER, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                d = (r.get("date") or "").strip()
                if len(d) != 10:                       # 跳过早期斜杠格式的脏行
                    continue
                out.setdefault(d, {})[r.get("platform", "")] = (
                    r.get("sales", ""), r.get("refund", ""), r.get("orders", ""))
    except Exception:
        pass
    return out


def read_jst_master():
    """{日期: (实发合计, 实退合计)}"""
    import csv
    agg = {}
    try:
        with open(JST_MASTER, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                d = (r.get("date") or "").strip()
                if len(d) != 10:
                    continue
                s, t = agg.get(d, (0.0, 0.0))
                try:
                    s += float(r.get("shipped") or 0)
                    t += float(r.get("returned") or 0)
                except ValueError:
                    pass
                agg[d] = (s, t)
    except Exception:
        pass
    return agg


ECOM_CRED = os.path.join(ECOM, "credentials.json")   # 现有工具的凭据文件，本控制台直接编辑它
# 能自动填账号密码的平台；淘宝有滑块、聚水潭报表站不代填，都只能真人登录
AUTO_LOGIN = {"小红书": True, "抖音": True, "拼多多": True, "淘宝": False, "聚水潭": False}


def mask(s):
    """账号打码显示：只留头尾，中间用星号。密码一律不读不显示。"""
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return s[0] + "*" * (len(s) - 1)
    keep = 3 if len(s) > 9 else 2
    return s[:keep] + "*" * max(3, len(s) - keep * 2) + s[-keep:]


def read_credentials():
    """读各平台账号配置状态：{平台: (账号掩码, 是否已填密码)}。**只判断密码有没有，不取值。**"""
    out = {}
    try:
        with open(ECOM_CRED, encoding="utf-8-sig") as f:
            data = json.load(f)
        for k, v in data.items():
            if isinstance(v, dict):
                out[k] = (mask(v.get("account", "")), bool(v.get("password")))
    except Exception:
        pass
    # 聚水潭的账号写在它自己的 登录.py 里（常量），这里只读出来打码显示
    try:
        with open(os.path.join(JST, "登录.py"), encoding="utf-8") as f:
            m = re.search(r'ACCOUNT\s*=\s*"([^"]+)"', f.read())
        if m:
            out["聚水潭"] = (mask(m.group(1)), False)
    except Exception:
        pass
    return out


JST_TEMPLATE = os.path.join(JST, "每周各渠道实发_实退数据_模板.xlsx")


def load_category_mod():
    """按路径加载聚水潭的 core/category.py —— 分类编码的读写唯一由它负责。

    用 importlib 按文件加载而不是 sys.path 注入，避免和电商工程的 core 包重名打架。
    """
    p = os.path.join(JST, "core", "category.py")
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    spec = importlib.util.spec_from_file_location("jst_category", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_credentials_raw():
    """原样读出 credentials.json（含 _说明 等其它键），供编辑后回写。"""
    with open(ECOM_CRED, encoding="utf-8-sig") as f:
        return json.load(f)


def write_credentials(changes):
    """把 {平台: {"account":.., "password":..}} 合并写回 credentials.json。

    · 原有的 _说明 等键、平台顺序全部保留，只改传进来的字段；
    · password 为 None 表示"不修改"（界面里留空即为此意），绝不会因为没重填而清空；
    · 先写临时文件再原子替换，中途出错不会把凭据文件写坏；
    · 编码与现有文件一致（UTF-8 无 BOM、CRLF），保证现有登录程序照样能读。
    """
    data = read_credentials_raw()
    for pf, kv in changes.items():
        row = data.get(pf)
        if not isinstance(row, dict):
            row = {"account": "", "password": ""}
            data[pf] = row
        if kv.get("account") is not None:
            row["account"] = kv["account"]
        if kv.get("password") is not None:
            row["password"] = kv["password"]
    tmp = ECOM_CRED + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, ECOM_CRED)


def num(x):
    """数字好看点：整数不带小数，空值显示 —"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


# ---------------------------------------------------------------- 主程序
class Console(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("多平台电商经营数据自动采集与统计分析系统 V1.0")   # 与软著申请名称一致（手册截图的标题栏要对得上）
        self.geometry("1060x720")
        self.minsize(900, 600)
        self.cfg = load_settings()
        self.proc = None                 # 当前子进程
        self.jobs = []                   # 待跑任务队列
        self.q = queue.Queue()
        self.running = False
        self.line_hook = None            # 作业输出的逐行回调（登录检测用）
        self.login_state = {}            # {平台: (状态, 详情, 检测时间)}

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=UI_FONT)
        style.configure("TNotebook.Tab", padding=(16, 7))
        style.configure("Head.TLabel", font=("微软雅黑", 11, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Run.TButton", font=("微软雅黑", 10, "bold"))
        style.configure("Treeview", rowheight=24)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.tab_run = ttk.Frame(nb, padding=12)
        self.tab_lgn = ttk.Frame(nb, padding=12)
        self.tab_dat = ttk.Frame(nb, padding=12)
        self.tab_cat = ttk.Frame(nb, padding=12)
        self.tab_sch = ttk.Frame(nb, padding=12)
        self.tab_log = ttk.Frame(nb, padding=12)
        self.tab_err = ttk.Frame(nb, padding=12)
        nb.add(self.tab_run, text="启动")
        nb.add(self.tab_lgn, text="浏览器 / 登录")
        nb.add(self.tab_dat, text="数据")
        nb.add(self.tab_cat, text="产品分类")
        nb.add(self.tab_sch, text="定时")
        nb.add(self.tab_log, text="日志")
        nb.add(self.tab_err, text="报错")
        self.nb = nb
        self.TAB_LOGIN = 1
        self.TAB_CAT = 3
        self.TAB_LOG = 5          # 日志页在第几个（自动刷新只在它可见时才跑）

        self.status = tk.StringVar(value="就绪")
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(bar, textvariable=self.status, style="Hint.TLabel").pack(side="left")
        ttk.Label(bar, text="本工具只读取和调用现有采集程序，不修改它们的任何文件",
                  style="Hint.TLabel").pack(side="right")

        self.build_run()
        self.build_login()
        self.build_dat()
        self.build_cat()
        self.build_sch()
        self.build_log()
        self.build_err()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self.drain)
        self.refresh_tasks()
        self.refresh_overview()
        self.refresh_browser()

    # ============================================================ 启动页
    def build_run(self):
        f = self.tab_run
        f.columnconfigure(0, weight=1)
        f.rowconfigure(5, weight=1)          # 输出框那一行吃掉多余高度

        # --- 顶部状态条：一眼看清浏览器在线否、下次定时、数据到哪天了 ---
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.lb_cdp = ttk.Label(top, text="● 检测中…", font=UI_BOLD)
        self.lb_cdp.pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=12)
        self.lb_next = ttk.Label(top, text="下次定时：—", style="Hint.TLabel")
        self.lb_next.pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=12)
        self.lb_data = ttk.Label(top, text="数据已到：—", style="Hint.TLabel")
        self.lb_data.pack(side="left")
        ttk.Button(top, text="刷新状态", width=9,
                   command=self.refresh_overview).pack(side="right")

        # --- 平台选择 ---
        box = ttk.LabelFrame(f, text=" 一、选择采集平台 ", padding=10)
        box.grid(row=1, column=0, sticky="ew")
        self.pv = {}
        saved = self.cfg.get("platforms", ECOM_PLATFORMS + ["聚水潭"])
        row = ttk.Frame(box); row.pack(fill="x")
        ttk.Label(row, text="电商平台：", width=10).pack(side="left")
        for p in ECOM_PLATFORMS:
            v = tk.BooleanVar(value=p in saved)
            self.pv[p] = v
            ttk.Checkbutton(row, text=p, variable=v).pack(side="left", padx=(0, 14))
        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=10)
        v = tk.BooleanVar(value="聚水潭" in saved)
        self.pv["聚水潭"] = v
        ttk.Checkbutton(row, text="聚水潭（实发/实退）", variable=v).pack(side="left")
        ttk.Button(row, text="全不选", width=7,
                   command=lambda: self.set_all(False)).pack(side="right")
        ttk.Button(row, text="全选", width=6,
                   command=lambda: self.set_all(True)).pack(side="right", padx=6)

        # --- 时间范围 ---
        box2 = ttk.LabelFrame(f, text=" 二、选择时间范围 ", padding=10)
        box2.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        r1 = ttk.Frame(box2); r1.pack(fill="x")
        ttk.Label(r1, text="开始日期：", width=10).pack(side="left")
        self.d1 = tk.StringVar(value=iso(today() - dt.timedelta(days=1)))
        ttk.Entry(r1, textvariable=self.d1, width=13, justify="center").pack(side="left")
        ttk.Label(r1, text="   结束日期：").pack(side="left")
        self.d2 = tk.StringVar(value=iso(today() - dt.timedelta(days=1)))
        ttk.Entry(r1, textvariable=self.d2, width=13, justify="center").pack(side="left")
        self.days_hint = ttk.Label(r1, text="", style="Hint.TLabel")
        self.days_hint.pack(side="left", padx=12)
        self.d1.trace_add("write", lambda *a: self.update_hint())
        self.d2.trace_add("write", lambda *a: self.update_hint())

        r2 = ttk.Frame(box2); r2.pack(fill="x", pady=(8, 0))
        ttk.Label(r2, text="快捷选择：", width=10).pack(side="left")
        for text, fn in [
            ("昨天", lambda: self.set_range(1, 1)),
            ("前天", lambda: self.set_range(2, 2)),
            ("最近3天", lambda: self.set_range(3, 1)),
            ("最近7天", lambda: self.set_range(7, 1)),
            ("本周", self.this_week),
            ("上周", self.last_week),
            ("本月", self.this_month),
            ("上月", self.last_month),
        ]:
            ttk.Button(r2, text=text, width=8, command=fn).pack(side="left", padx=(0, 5))
        ttk.Label(box2, text="数据有一天延时：平台当天的数据要到次日才结算完整，所以默认抓昨天。",
                  style="Hint.TLabel").pack(anchor="w", pady=(8, 0))

        # --- 运行选项 ---
        box3 = ttk.LabelFrame(f, text=" 三、运行方式 ", padding=10)
        box3.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        r3 = ttk.Frame(box3); r3.pack(fill="x")
        ttk.Label(r3, text="浏览器：", width=10).pack(side="left")
        self.mode = tk.StringVar(value=self.cfg.get("mode", "cdp"))
        ttk.Radiobutton(r3, text="常开浏览器（推荐，复用已登录的标签页）",
                        variable=self.mode, value="cdp").pack(side="left", padx=(0, 18))
        ttk.Radiobutton(r3, text="独立无头（自己开后台浏览器，常开的挂了时用）",
                        variable=self.mode, value="headless").pack(side="left")
        ttk.Button(r3, text="检测登录状态", width=13,
                   command=self.goto_login).pack(side="right")
        r4 = ttk.Frame(box3); r4.pack(fill="x", pady=(8, 0))
        ttk.Label(r4, text="选项：", width=10).pack(side="left")
        self.push = tk.BooleanVar(value=self.cfg.get("push", False))
        ttk.Checkbutton(r4, text="抓完推送企业微信群（手动补抓建议不勾，免刷屏）",
                        variable=self.push).pack(side="left", padx=(0, 18))
        self.restart = tk.BooleanVar(value=False)
        ttk.Checkbutton(r4, text="抓前重启常开浏览器（连不上/卡死时勾）",
                        variable=self.restart).pack(side="left")

        # --- 执行 ---（单独一行，否则会和上面的运行方式框重叠）
        r5 = ttk.Frame(f); r5.grid(row=4, column=0, sticky="e", pady=(10, 0))
        self.btn_run = ttk.Button(r5, text="▶  开始采集", style="Run.TButton",
                                  width=16, command=self.start)
        self.btn_run.pack(side="left", padx=(0, 8))
        self.btn_stop = ttk.Button(r5, text="■ 停止", width=9,
                                   command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")

        # --- 实时输出 ---
        box4 = ttk.LabelFrame(f, text=" 运行输出 ", padding=(8, 6))
        box4.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        box4.rowconfigure(0, weight=1); box4.columnconfigure(0, weight=1)
        self.out = tk.Text(box4, font=MONO, wrap="word", height=12,
                           bg="#1b2127", fg="#d6dde3", insertbackground="#d6dde3",
                           relief="flat", padx=8, pady=6)
        self.out.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box4, command=self.out.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.out.config(yscrollcommand=sb.set, state="disabled")
        self.out.tag_config("ok", foreground="#7fd18c")
        self.out.tag_config("bad", foreground="#ff8b7a")
        self.out.tag_config("warn", foreground="#ffc46b")
        self.out.tag_config("head", foreground="#7fc0e8", font=("Consolas", 9, "bold"))
        self.out.tag_config("dim", foreground="#8a97a1")
        r6 = ttk.Frame(box4); r6.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(r6, text="打开：", style="Hint.TLabel").pack(side="left")
        ttk.Button(r6, text="电商销售数据", width=13,
                   command=lambda: open_folder(ECOM_OUT)).pack(side="left", padx=(0, 5))
        ttk.Button(r6, text="聚水潭实发/实退数据", width=19,
                   command=lambda: open_folder(JST_OUT)).pack(side="left", padx=(0, 5))
        ttk.Button(r6, text="本月月表", width=10,
                   command=self.open_month_xlsx).pack(side="left", padx=(0, 5))
        ttk.Button(r6, text="本周周表", width=10,
                   command=self.open_week_xlsx).pack(side="left")
        ttk.Button(r6, text="清空输出", width=9, command=self.clear_out).pack(side="right")
        self.update_hint()

    def goto_login(self):
        """从启动页跳到浏览器/登录页并顺手刷新浏览器状态。"""
        self.nb.select(self.TAB_LOGIN)
        self.refresh_browser()

    # ---------- 打开产出文件 ----------
    def open_month_xlsx(self):
        d = today()
        p = os.path.join(ECOM_OUT, f"{d.year}年{d.month:02d}月各平台销售汇总.xlsx")
        if not os.path.isfile(p):
            p = newest(ECOM_OUT, "各平台销售汇总")
        open_file(p, "本月月表")

    def open_week_xlsx(self):
        p = newest(JST_OUT, "每周各渠道实发_实退数据")
        open_file(p, "本周周表")

    # ---------- 顶部状态 ----------
    def refresh_overview(self):
        alive = cdp_alive()
        self.lb_cdp.config(text="● 常开浏览器在线" if alive else "● 常开浏览器未运行",
                           foreground="#2e7d32" if alive else "#c0392b")
        nxt = [t["下次运行"] for t in getattr(self, "tasks", []) if t.get("下次运行")]
        self.lb_next.config(text="下次定时：" + (min(nxt) if nxt else "无"))
        e, j = read_ecom_master(), read_jst_master()
        parts = []
        parts.append("电商 " + (max(e) if e else "无数据"))
        parts.append("聚水潭 " + (max(j) if j else "无数据"))
        y = iso(today() - dt.timedelta(days=1))
        lag = [n for n, m in (("电商", max(e) if e else ""), ("聚水潭", max(j) if j else "")) if m < y]
        tail = f"　⚠ {'、'.join(lag)}还缺昨天" if lag else "　（已到昨天）"
        self.lb_data.config(text="数据已到：" + "　".join(parts) + tail,
                            foreground="#c0392b" if lag else "#666666")
        if hasattr(self, "tv_dat"):
            self.refresh_overview_table()

    def set_all(self, val):
        for v in self.pv.values():
            v.set(val)

    def set_range(self, back_start, back_end):
        self.d1.set(iso(today() - dt.timedelta(days=back_start)))
        self.d2.set(iso(today() - dt.timedelta(days=back_end)))

    def this_week(self):
        m = week_monday(today())
        self.d1.set(iso(m)); self.d2.set(iso(min(today() - dt.timedelta(days=1), m + dt.timedelta(days=6))))

    def last_week(self):
        m = week_monday(today()) - dt.timedelta(days=7)
        self.d1.set(iso(m)); self.d2.set(iso(m + dt.timedelta(days=6)))

    def this_month(self):
        f = today().replace(day=1)
        self.d1.set(iso(f)); self.d2.set(iso(max(f, today() - dt.timedelta(days=1))))

    def last_month(self):
        f = today().replace(day=1) - dt.timedelta(days=1)
        self.d1.set(iso(f.replace(day=1))); self.d2.set(iso(f))

    def update_hint(self):
        try:
            a = dt.date.fromisoformat(self.d1.get().strip())
            b = dt.date.fromisoformat(self.d2.get().strip())
            n = abs((b - a).days) + 1
            warn = "　⚠ 未来日期没有数据" if max(a, b) >= today() else ""
            self.days_hint.config(text=f"共 {n} 天{warn}")
        except Exception:
            self.days_hint.config(text="日期格式应为 2026-08-20")

    # ---------- 执行控制 ----------
    def start(self):
        if self.running:
            return
        picked = [p for p, v in self.pv.items() if v.get()]
        if not picked:
            messagebox.showwarning("还没选平台", "请至少勾选一个采集平台。")
            return
        try:
            a = dt.date.fromisoformat(self.d1.get().strip())
            b = dt.date.fromisoformat(self.d2.get().strip())
        except Exception:
            messagebox.showwarning("日期不对", "日期格式应为 2026-08-20。")
            return
        if a > b:
            a, b = b, a
        if b >= today():
            if not messagebox.askyesno("日期含今天或未来",
                                       "今天的数据尚未结算完整，通常抓不到或不准。\n仍要继续吗？"):
                return
        mode = self.mode.get()
        if mode == "cdp" and not cdp_alive() and not self.restart.get():
            if not messagebox.askyesno(
                    "常开浏览器没开",
                    f"检测不到常开浏览器（端口 {CDP_PORT}）。\n\n"
                    "请先双击『常开登录.bat』启动并登录，\n"
                    "或勾选“抓前重启常开浏览器”后重试。\n\n仍要继续吗？"):
                return
        busy = [t["名称"] for t in self.tasks if t.get("状态") == "Running"]
        if busy and self.push.get():
            if not messagebox.askyesno(
                    "定时任务正在运行",
                    f"计划任务【{busy[0]}】此刻正在跑。\n"
                    "同时手动采集并推送，群里会收到两份。\n\n仍要继续吗？"):
                return

        # 组装任务队列：电商一批、聚水潭一批
        ecom = [p for p in picked if p in ECOM_PLATFORMS]
        self.jobs = []
        if self.restart.get():
            self.jobs.append((["--restart-browser"], "重启常开浏览器"))
        if ecom:
            self.jobs.append(([
                "--target", "ecom", "--mode", mode, "--platforms", ",".join(ecom),
                "--start", iso(a), "--end", iso(b), "--push", "1" if self.push.get() else "0",
            ], f"电商：{'、'.join(ecom)}"))
        if "聚水潭" in picked:
            self.jobs.append(([
                "--target", "jst", "--mode", mode,
                "--start", iso(a), "--end", iso(b), "--push", "1" if self.push.get() else "0",
            ], "聚水潭：实发/实退"))

        self.cfg.update({"platforms": picked, "mode": mode, "push": self.push.get()})
        save_settings(self.cfg)
        console_log(f"开始采集 {iso(a)}~{iso(b)}｜平台 {picked}｜模式 {mode}｜推送 {self.push.get()}")
        self.launch(self.jobs, f"══ 开始：{iso(a)} ~ {iso(b)}　共 {len(self.jobs)} 个任务 ══")

    def launch(self, jobs, banner, hook=None):
        """统一的作业启动口：采集、登录检测、重启浏览器都走这里。
        hook 为可选的逐行回调（登录检测靠它更新状态表）。"""
        if self.running:
            messagebox.showinfo("正忙", "还有任务在跑，等它结束再来。")
            return
        self.jobs = list(jobs)
        self.line_hook = hook
        self.login_issue = []
        self.running = True
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.write(banner + "\n", "head")
        self.next_job()

    def next_job(self):
        if not self.jobs:
            self.finish("全部任务已完成")
            return
        args, label = self.jobs.pop(0)
        self.status.set(f"正在执行：{label}")
        self.write(f"\n▶ {label}\n", "head")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            self.proc = subprocess.Popen(
                [PY, "-u", WORKER] + args, cwd=HERE, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=NOWIN)
        except Exception as e:
            self.write(f"启动失败：{e}\n", "bad")
            self.finish("启动失败")
            return
        threading.Thread(target=self.pump, args=(self.proc,), daemon=True).start()

    def pump(self, proc):
        try:
            for line in proc.stdout:
                self.q.put(("line", line.rstrip("\n")))
        except Exception:
            pass
        self.q.put(("exit", proc.wait()))

    def drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    if self.line_hook:
                        try:
                            self.line_hook(payload)
                        except Exception:
                            pass
                    self.note_login_issue(payload)
                    self.write(payload + "\n", self.tag_of(payload))
                else:
                    self.proc = None
                    if not self.running:
                        pass
                    elif payload not in (0,):
                        self.write(f"（该任务退出码 {payload}）\n", "dim")
                        self.next_job()
                    else:
                        self.next_job()
        except queue.Empty:
            pass
        self.after(120, self.drain)

    def note_login_issue(self, line):
        """采集输出里一旦出现登录失效字样，记下是哪个平台，跑完统一提示怎么处理。"""
        if line.startswith("[[LOGIN]]"):
            return
        if not any(k in line for k in ("登录态失效", "登录已过期", "未登录", "登录已失效", "重新登录")):
            return
        who = next((p for p in ECOM_PLATFORMS + ["聚水潭"] if p in line), "")
        if who and who not in self.login_issue:
            self.login_issue.append(who)
            self.login_state[who] = ("已失效", "采集时检出", f"{dt.datetime.now():%m-%d %H:%M:%S}")
            if hasattr(self, "tv_lgn"):
                self.render_login()

    @staticmethod
    def tag_of(s):
        if "[[DONE]]" in s or "✅" in s or "全部成功" in s:
            return "ok"
        if "[[FAIL]]" in s or "❌" in s or "失败" in s or "错误" in s or "Traceback" in s:
            return "bad"
        if "⚠" in s or "警告" in s or "未就绪" in s or "重试" in s:
            return "warn"
        if s.startswith("---") or s.startswith("==="):
            return "dim"
        return None

    def write(self, text, tag=None):
        self.out.config(state="normal")
        self.out.insert("end", text.replace("[[DONE]]", "✔").replace("[[FAIL]]", "✘"),
                        tag or ())
        self.out.see("end")
        self.out.config(state="disabled")

    def clear_out(self):
        self.out.config(state="normal"); self.out.delete("1.0", "end"); self.out.config(state="disabled")

    def stop(self):
        if not self.proc:
            return
        if not messagebox.askyesno("停止", "确定要中止当前采集吗？\n已经抓到的数据不会丢。"):
            return
        self.jobs = []
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           capture_output=True, creationflags=NOWIN)
        except Exception as e:
            self.write(f"停止失败：{e}\n", "bad")
        console_log("用户手动中止采集")
        self.finish("已中止")

    def finish(self, msg):
        self.running = False
        self.proc = None
        self.jobs = []
        self.line_hook = None
        self.btn_run.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status.set(msg)
        self.write(f"\n══ {msg} ══\n", "head")
        console_log(msg)
        self.refresh_err()
        self.refresh_overview()          # 抓完顺手刷新数据页与顶部状态
        if self.login_issue:
            who = "、".join(self.login_issue)
            self.login_issue = []
            if messagebox.askyesno(
                    "有平台登录失效",
                    f"这次采集中【{who}】登录已失效，那部分数据没抓到。\n\n"
                    "现在去「浏览器 / 登录」页处理吗？\n"
                    "（在那里重登，然后回来把这几天补抓一遍即可）"):
                self.nb.select(self.TAB_LOGIN)
                self.refresh_browser()

    # ============================================================ 浏览器/登录页
    def build_login(self):
        f = self.tab_lgn
        f.columnconfigure(0, weight=1); f.rowconfigure(2, weight=1)

        # --- 常开浏览器状态 ---
        box = ttk.LabelFrame(f, text=" 常开浏览器 ", padding=10)
        box.grid(row=0, column=0, sticky="ew")
        r = ttk.Frame(box); r.pack(fill="x")
        self.lb_br = ttk.Label(r, text="● 检测中…", font=("微软雅黑", 10, "bold"))
        self.lb_br.pack(side="left")
        ttk.Button(r, text="刷新状态", width=10, command=self.refresh_browser).pack(side="right")
        ttk.Button(r, text="打开 / 重启浏览器", width=17,
                   command=self.open_browser).pack(side="right", padx=6)
        ttk.Label(box, text="常开浏览器就是那个一直开着、5 个平台各占一个标签页的 Chrome。"
                            "抓取时复用它已登录的会话，所以它必须开着且各平台保持登录。",
                  style="Hint.TLabel", wraplength=960, justify="left").pack(anchor="w", pady=(6, 0))

        # --- 登录状态表 ---
        r2 = ttk.Frame(f); r2.grid(row=1, column=0, sticky="ew", pady=(12, 6))
        ttk.Label(r2, text="各平台登录状态", style="Head.TLabel").pack(side="left")
        self.lb_chk = ttk.Label(r2, text="", style="Hint.TLabel")
        self.lb_chk.pack(side="left", padx=12)
        ttk.Button(r2, text="检测全部", width=10,
                   command=lambda: self.check_login(None)).pack(side="right")
        ttk.Button(r2, text="检测选中", width=10,
                   command=self.check_selected).pack(side="right", padx=6)
        ttk.Button(r2, text="配置账号密码", width=14,
                   command=self.open_cred_setup).pack(side="right", padx=6)

        cols = ("平台", "登录状态", "账号", "自动登录", "最后检测", "说明")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=6, selectmode="browse")
        for c, w in zip(cols, (78, 84, 150, 84, 122, 372)):
            tv.heading(c, text=c)
            tv.column(c, width=w,
                      anchor="center" if c in ("平台", "登录状态", "自动登录") else "w")
        tv.grid(row=2, column=0, sticky="nsew")
        tv.tag_configure("ok", foreground="#2e7d32")
        tv.tag_configure("bad", foreground="#c0392b")
        tv.tag_configure("warn", foreground="#96591D")
        tv.tag_configure("unknown", foreground="#8a97a1")
        tv.bind("<Double-1>", lambda e: self.check_selected())
        self.tv_lgn = tv
        self.lb_cred = ttk.Label(f, text="", style="Hint.TLabel")
        self.lb_cred.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.render_login()

        # --- 失效了怎么办 ---
        box2 = ttk.LabelFrame(f, text=" 登录失效了怎么办 ", padding=10)
        box2.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(box2, text="① 常开浏览器模式（默认）——所有平台都在同一个 Chrome 里登录：",
                  font=UI_BOLD).pack(anchor="w")
        b1 = ttk.Frame(box2); b1.pack(fill="x", pady=(4, 10))
        ttk.Label(b1, text="　　", ).pack(side="left")
        ttk.Button(b1, text="打开登录窗口（5 个平台标签）", width=28,
                   command=self.open_browser).pack(side="left")
        ttk.Label(b1, text="　→ 在弹出的 Chrome 里把标红的平台重登一遍 → 回来点「检测全部」",
                  style="Hint.TLabel").pack(side="left")

        ttk.Label(box2, text="② 独立无头模式——先配好账号密码，再用登录工具建立登录态（三步）：",
                  font=UI_BOLD).pack(anchor="w")
        b2 = ttk.Frame(box2); b2.pack(fill="x", pady=(4, 0))
        ttk.Label(b2, text="　　").pack(side="left")
        ttk.Button(b2, text="① 配置账号密码", width=16,
                   command=self.open_cred_setup).pack(side="left", padx=(0, 6))
        ttk.Button(b2, text="② 电商登录工具", width=15,
                   command=lambda: self.run_bat(os.path.join(ECOM, "登录.bat"),
                                                "电商登录工具")).pack(side="left", padx=(0, 6))
        ttk.Button(b2, text="淘宝（真人 Chrome 过滑块）", width=24,
                   command=lambda: self.run_bat(os.path.join(ECOM, "淘宝Chrome.bat"),
                                                "淘宝调试 Chrome")).pack(side="left", padx=(0, 6))
        ttk.Button(b2, text="聚水潭登录工具", width=15,
                   command=lambda: self.run_bat(os.path.join(JST, "登录.bat"),
                                                "聚水潭登录工具")).pack(side="left")
        ttk.Label(box2, text="　「配置账号密码」只在建立登录态时用一次：登录工具据此自动填账号密码，"
                             "验证码/短信由你在弹出的窗口过一遍，之后登录态存进各项目的 browser_profiles，"
                             "抓取时只用它、不再用密码。淘宝（滑块）与聚水潭均不代填，只能真人登录。",
                  style="Hint.TLabel", wraplength=960, justify="left").pack(anchor="w", pady=(6, 0))

    def render_login(self):
        tv = self.tv_lgn
        tv.delete(*tv.get_children())
        creds = read_credentials()
        n_miss = 0
        for p in ECOM_PLATFORMS + ["聚水潭"]:
            st, detail, ts = self.login_state.get(p, ("未检测", "", ""))
            if st == "已登录":
                tag = "ok"
            elif st in ("未检测", "检测中…"):
                tag = "unknown"
            elif st == "页面未就绪":          # 不是登录问题，橙色区别于红色的"已失效"
                tag = "warn"
            else:
                tag = "bad"
            acc, has_pwd = creds.get(p, ("", False))
            auto = AUTO_LOGIN.get(p, False)
            if auto:
                mark = "支持" if has_pwd else "缺密码"
                if not has_pwd:
                    n_miss += 1
            else:
                mark = "需人工"
            tv.insert("", "end", values=(p, st, acc or "（未配置）", mark, ts, detail), tags=(tag,))
        if hasattr(self, "lb_cred"):
            if n_miss:
                self.lb_cred.config(
                    text=f"⚠ 有 {n_miss} 个平台没填密码，独立无头模式下无法自动登录——点右上「配置账号密码」补上",
                    foreground="#c0392b")
            else:
                self.lb_cred.config(
                    text="账号密码已配置（保存在 电商销售爬虫\\credentials.json，仅本机、不随工具分发）",
                    foreground="#666666")

    def open_cred_setup(self):
        """在控制台里直接改各平台账号密码，写回现有的 credentials.json（同一份凭据、同一个文件）。"""
        if not os.path.isfile(ECOM_CRED):
            if not messagebox.askyesno(
                    "还没有凭据文件",
                    f"没找到：\n{ECOM_CRED}\n\n要新建一份并开始填写吗？"):
                return
            try:
                with open(ECOM_CRED, "w", encoding="utf-8", newline="\r\n") as f:
                    json.dump({"_说明": "各平台账号密码，脚本自动登录用。仅存本机、不分发。"},
                              f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("新建失败", str(e))
                return

        win = tk.Toplevel(self)
        win.title("配置采集账号")
        win.transient(self); win.grab_set()
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=14); frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="各平台采集账号", style="Head.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frm, text="账号可随时改；密码留空＝不改动原有密码。保存后写入 "
                            "电商销售爬虫\\credentials.json（仅本机）。",
                  style="Hint.TLabel", wraplength=620, justify="left").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, 10))

        for j, t in enumerate(("平台", "账号", "密码", "自动登录")):
            ttk.Label(frm, text=t, font=UI_BOLD).grid(row=2, column=j, sticky="w",
                                                      padx=(0, 10), pady=(0, 4))
        try:
            cur = read_credentials_raw()
        except Exception as e:
            messagebox.showerror("读取失败", str(e)); win.destroy(); return

        rows = {}
        for i, p in enumerate(ECOM_PLATFORMS):
            r = 3 + i
            ttk.Label(frm, text=p).grid(row=r, column=0, sticky="w", pady=3)
            va = tk.StringVar(value=(cur.get(p) or {}).get("account", ""))
            ea = ttk.Entry(frm, textvariable=va, width=32)
            ea.grid(row=r, column=1, sticky="w", padx=(0, 10))
            vp = tk.StringVar(value="")
            ep = ttk.Entry(frm, textvariable=vp, width=22, show="●")
            ep.grid(row=r, column=2, sticky="w", padx=(0, 10))
            has = bool((cur.get(p) or {}).get("password"))
            note = "支持" if AUTO_LOGIN.get(p) else "需人工（滑块）"
            ttk.Label(frm, text=note + ("" if has else " · 未设密码"),
                      style="Hint.TLabel").grid(row=r, column=3, sticky="w")
            rows[p] = (va, vp, ep, (cur.get(p) or {}).get("account", ""))

        show = tk.BooleanVar(value=False)

        def toggle():
            for _, _, ep, _ in rows.values():
                ep.config(show="" if show.get() else "●")
        ttk.Checkbutton(frm, text="显示我输入的密码", variable=show, command=toggle).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Separator(frm, orient="horizontal").grid(row=9, column=0, columnspan=4,
                                                     sticky="ew", pady=10)
        ttk.Label(frm, text="聚水潭不在此处配置：它的登录不代填账号密码，只能真人登录，"
                            "账号写在 聚水潭爬虫\\登录.py 里。",
                  style="Hint.TLabel", wraplength=620, justify="left").grid(
            row=10, column=0, columnspan=4, sticky="w")
        ttk.Label(frm, text="⚠ 改成另一个账号后，该平台原有的登录态属于旧账号，必须重新登录一次；"
                            "且换号后抓到的是新店铺的数据，会与历史数据存进同一份主数据。",
                  style="Hint.TLabel", wraplength=620, justify="left",
                  foreground="#96591D").grid(row=11, column=0, columnspan=4,
                                             sticky="w", pady=(4, 0))

        after = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="保存后立即打开电商登录工具，重新建立登录态",
                        variable=after).grid(row=12, column=0, columnspan=4,
                                             sticky="w", pady=(8, 0))

        bar = ttk.Frame(frm); bar.grid(row=13, column=0, columnspan=4, sticky="e", pady=(14, 0))

        def save():
            changes, changed_acc, set_pwd = {}, [], []
            for p, (va, vp, _ep, old) in rows.items():
                acc = va.get().strip()
                pwd = vp.get()
                item = {}
                if acc != old:
                    item["account"] = acc
                    changed_acc.append(p)
                if pwd:
                    item["password"] = pwd
                    set_pwd.append(p)
                if item:
                    changes[p] = item
            if not changes:
                messagebox.showinfo("没有改动", "账号和密码都没有变化。", parent=win)
                return
            msg = "将要保存：\n"
            if changed_acc:
                msg += "· 修改账号：" + "、".join(changed_acc) + "\n"
            if set_pwd:
                msg += "· 更新密码：" + "、".join(set_pwd) + "\n"
            msg += "\n未填密码的平台保持原密码不变。确定保存吗？"
            if not messagebox.askyesno("确认", msg, parent=win):
                return
            try:
                write_credentials(changes)
            except Exception as e:
                messagebox.showerror("保存失败", f"凭据文件未被改动。\n\n{e}", parent=win)
                return
            # 只记哪些平台被改过，绝不记账号密码本身
            console_log(f"账号配置已更新：改账号={changed_acc}，改密码={set_pwd}")
            messagebox.showinfo("已保存", "已写入 credentials.json。", parent=win)
            win.destroy()
            self.render_login()
            if changed_acc:
                self.login_state.pop("_", None)
                for p in changed_acc:
                    self.login_state[p] = ("待重登", "账号已变更，需重新建立登录态",
                                           f"{dt.datetime.now():%m-%d %H:%M:%S}")
                self.render_login()
            if after.get():
                self.run_bat(os.path.join(ECOM, "登录.bat"), "电商登录工具")

        ttk.Button(bar, text="保存", width=10, command=save).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="取消", width=10, command=win.destroy).pack(side="left")
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + 90
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        rows[ECOM_PLATFORMS[0]][2].focus_set()

    def refresh_browser(self):
        alive = cdp_alive()
        n = ""
        if alive:
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5) as r:
                    pages = [x for x in json.loads(r.read().decode("utf-8"))
                             if x.get("type") == "page"]
                n = f"，{len(pages)} 个标签页"
            except Exception:
                pass
        self.lb_br.config(text=(f"● 在线（端口 {CDP_PORT}{n}）" if alive else "● 未运行"),
                          foreground="#2e7d32" if alive else "#c0392b")
        if hasattr(self, "lb_cdp"):
            self.lb_cdp.config(text="● 常开浏览器在线" if alive else "● 常开浏览器未运行",
                               foreground="#2e7d32" if alive else "#c0392b")
        return alive

    def open_browser(self):
        alive = cdp_alive()
        if alive:
            if not messagebox.askyesno(
                    "浏览器已在运行",
                    "常开浏览器正开着。\n\n"
                    "点「是」＝关掉后全新拉起，用于 CDP 卡死或页面无响应（登录态不会丢）；\n"
                    "点「否」＝只补齐 5 个平台标签页，不重启。"):
                self.launch([(["--open-login"], "打开登录窗口")], "══ 打开登录窗口 ══",
                            hook=None)
                self.after(9000, self.refresh_browser)
                return
            jobs = [(["--restart-browser"], "重启常开浏览器")]
        else:
            jobs = [(["--open-login"], "打开常开浏览器与登录标签页")]
        self.nb.select(0)                       # 切到启动页看实时输出
        self.launch(jobs, "══ 浏览器操作 ══")
        self.after(12000, self.refresh_browser)

    def check_selected(self):
        sel = self.tv_lgn.selection()
        if not sel:
            messagebox.showinfo("先选一行", "请先在表里点选一个平台，或直接点「检测全部」。")
            return
        self.check_login([self.tv_lgn.item(sel[0], "values")[0]])

    def check_login(self, platforms):
        pf = platforms or (ECOM_PLATFORMS + ["聚水潭"])
        mode = self.mode.get()
        if mode == "cdp" and not cdp_alive():
            messagebox.showwarning("浏览器没开",
                                   "常开浏览器未运行，无法检测它里面的登录状态。\n"
                                   "请先点「打开 / 重启浏览器」。")
            return
        now = f"{dt.datetime.now():%m-%d %H:%M:%S}"
        for p in pf:
            self.login_state[p] = ("检测中…", "", now)
        self.render_login()
        self.lb_chk.config(text=f"正在检测（{'常开浏览器' if mode == 'cdp' else '独立无头档案'}）…"
                                "　无头模式要逐个开浏览器，会慢一些")
        self.launch([([
            "--check-login", "--mode", mode, "--platforms", ",".join(pf),
        ], f"检测登录：{'、'.join(pf)}")], f"══ 检测登录状态（{mode}）══", hook=self.on_login_line)

    def on_login_line(self, line):
        """解析 worker 的 [[LOGIN]]平台|状态|说明 行，实时刷新状态表。"""
        if not line.startswith("[[LOGIN]]"):
            return
        try:
            p, st, detail = line[len("[[LOGIN]]"):].split("|", 2)
        except ValueError:
            return
        self.login_state[p] = (st, detail, f"{dt.datetime.now():%m-%d %H:%M:%S}")
        self.render_login()
        # 「页面未就绪」不是登录问题，单独提示，别让人白跑一趟去重登
        bad = [k for k, v in self.login_state.items() if v[0] == "已失效"]
        slow = [k for k, v in self.login_state.items() if v[0] == "页面未就绪"]
        other = [k for k, v in self.login_state.items() if v[0] == "检测失败"]
        if bad:
            self.lb_chk.config(text="　⚠ 需要重登：" + "、".join(bad), foreground="#c0392b")
        elif slow or other:
            self.lb_chk.config(text="　页面未就绪/检测失败：" + "、".join(slow + other)
                                    + "（不是登录问题，稍后重测即可）", foreground="#96591D")
        else:
            self.lb_chk.config(text="　全部已登录 ✔", foreground="#2e7d32")

    def run_bat(self, path, what):
        if not os.path.isfile(path):
            messagebox.showwarning("找不到工具", f"没找到{what}：\n{path}")
            return
        try:
            os.startfile(path)                  # 单独弹黑窗口，让你跟它交互
            console_log(f"打开 {what}")
            self.status.set(f"已打开{what}，请在弹出的窗口里操作；完成后回来点「检测全部」")
        except Exception as e:
            messagebox.showwarning("打不开", str(e))

    # ============================================================ 数据页
    def build_dat(self):
        f = self.tab_dat
        f.columnconfigure(0, weight=1); f.rowconfigure(1, weight=1)
        top = ttk.Frame(f); top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="每日数据总览", style="Head.TLabel").pack(side="left")
        ttk.Label(top, text="　最近").pack(side="left")
        self.dat_days = tk.StringVar(value="14")
        cb = ttk.Combobox(top, textvariable=self.dat_days, width=4, state="readonly",
                          values=["7", "14", "30", "60"])
        cb.pack(side="left"); cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_overview_table())
        ttk.Label(top, text="天").pack(side="left")
        self.only_gap = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="只看有缺口的日期", variable=self.only_gap,
                        command=self.refresh_overview_table).pack(side="left", padx=12)
        ttk.Button(top, text="刷新", width=8, command=self.refresh_overview).pack(side="right")
        ttk.Button(top, text="校验上周（约 7 分钟）", width=20,
                   command=self.verify_week).pack(side="right", padx=6)

        cols = ("日期", "周", "淘宝", "抖音", "拼多多", "小红书", "电商合计", "实发", "实退", "净发货")
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (92, 34, 88, 88, 88, 88, 96, 68, 68, 74)):
            tv.heading(c, text=c)
            tv.column(c, width=w, anchor="center" if c in ("日期", "周") else "e")
        tv.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, command=tv.yview); sb.grid(row=1, column=1, sticky="ns")
        tv.config(yscrollcommand=sb.set)
        tv.tag_configure("gap", background="#fdecea", foreground="#c0392b")
        tv.tag_configure("weekend", background="#f7f9fa")
        tv.tag_configure("today", foreground="#8a97a1")
        tv.bind("<Double-1>", self.fill_from_row)
        self.tv_dat = tv

        self.dat_sum = ttk.Label(f, text="", style="Hint.TLabel")
        self.dat_sum.grid(row=2, column=0, sticky="w", pady=(8, 0))
        bar = ttk.Frame(f); bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(bar, text="打开：", style="Hint.TLabel").pack(side="left")
        ttk.Button(bar, text="电商销售数据目录", width=17,
                   command=lambda: open_folder(ECOM_OUT)).pack(side="left", padx=(0, 5))
        ttk.Button(bar, text="聚水潭实发/实退目录", width=19,
                   command=lambda: open_folder(JST_OUT)).pack(side="left", padx=(0, 5))
        ttk.Button(bar, text="本月月表", width=10, command=self.open_month_xlsx).pack(side="left", padx=(0, 5))
        ttk.Button(bar, text="本周周表", width=10, command=self.open_week_xlsx).pack(side="left", padx=(0, 5))
        ttk.Button(bar, text="最新周报", width=10,
                   command=lambda: open_file(newest(JST_OUT, "周报", ".txt"), "周报")).pack(side="left")
        ttk.Label(f, text="红色行 = 那天有平台没数据。双击任意一行，会把它的日期填进「启动」页，直接补抓。",
                  style="Hint.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 0))

    def refresh_overview_table(self):
        days = int(self.dat_days.get())
        e, j = read_ecom_master(), read_jst_master()
        tv = self.tv_dat
        tv.delete(*tv.get_children())
        gaps = 0
        rows = 0
        for k in range(days):
            d = today() - dt.timedelta(days=k)
            ds = iso(d)
            pv = e.get(ds, {})
            jv = j.get(ds)
            vals = [pv.get(p, ("", "", ""))[0] for p in ECOM_PLATFORMS]
            has = [v not in ("", None) for v in vals]
            tot = sum(float(v) for v in vals if v not in ("", None)) if any(has) else None
            missing = (not all(has)) or (jv is None)
            is_today = d == today()
            if missing and not is_today:
                gaps += 1
            if self.only_gap.get() and not (missing and not is_today):
                continue
            rows += 1
            tags = []
            if is_today:
                tags.append("today")
            elif missing:
                tags.append("gap")
            elif d.weekday() >= 5:
                tags.append("weekend")
            net = (jv[0] - jv[1]) if jv else None
            tv.insert("", "end", tags=tuple(tags), values=(
                ds, WEEKDAY[d.weekday()],
                *[num(v) if v not in ("", None) else "—" for v in vals],
                num(tot) if tot is not None else "—",
                num(jv[0]) if jv else "—",
                num(jv[1]) if jv else "—",
                num(net) if net is not None else "—",
            ))
        msg = f"最近 {days} 天：显示 {rows} 行"
        msg += f"，其中 {gaps} 天有缺口（今天不计，数据要次日才出）" if gaps else "，无缺口 ✔"
        self.dat_sum.config(text=msg)

    def verify_week(self):
        """把上周（周一~周日）的聚水潭实发+实退整周重采一遍，清掉日常抓取的偶发漏记。

        与周一定时任务做的是同一件事，这里供随时手动触发。不推群。"""
        m = week_monday(today()) - dt.timedelta(days=7)
        rng = f"{iso(m)} ~ {iso(m + dt.timedelta(days=6))}"
        if not messagebox.askyesno(
                "校验上周",
                f"把上周（{rng}）的聚水潭实发/实退整周重新采集一遍，\n"
                "用已采到的最新值覆盖主数据，并重渲周表。\n\n"
                "· 154 次查询，实测约 7 分钟\n"
                "· 不推送企业微信群\n"
                "· 已手工改过的周表会被重新生成\n\n现在开始吗？"):
            return
        self.nb.select(0)          # 切到启动页看实时输出
        self.launch([([
            "--verify-week", "--mode", self.mode.get(),
        ], f"整周校验：{rng}")], f"══ 整周校验 {rng} ══")

    def fill_from_row(self, _evt):
        sel = self.tv_dat.selection()
        if not sel:
            return
        ds = self.tv_dat.item(sel[0], "values")[0]
        self.d1.set(ds); self.d2.set(ds)
        self.nb.select(0)
        self.status.set(f"已把 {ds} 填进启动页，勾好平台点「开始采集」即可补抓")

    # ============================================================ 产品分类页
    def build_cat(self):
        """『分类编码』维护页：产品与商品编码的增删改，保存进聚水潭的模板 Excel。

        这张表是整条链路的**唯一分类真相源**——采集抓哪些编码、周表有哪些产品列、
        日报周报文案列哪些产品，全都从它读。所以在这里改完，下次采集起全自动跟随。
        """
        f = self.tab_cat
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        top = ttk.Frame(f); top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="产品分类编码", style="Head.TLabel").pack(side="left")
        self.lb_cat = ttk.Label(top, text="", style="Hint.TLabel")
        self.lb_cat.pack(side="left", padx=12)
        ttk.Button(top, text="重新载入", width=10, command=self.cat_load).pack(side="right")
        ttk.Button(top, text="保存到模板", width=12, command=self.cat_save).pack(side="right", padx=6)
        ttk.Button(top, text="打开模板 Excel", width=15,
                   command=lambda: open_file(JST_TEMPLATE, "分类编码模板")).pack(side="right")

        ttk.Label(f, text="采集抓哪些编码、周表有哪些产品列、日报周报列哪些产品，全部由这张表决定。"
                          "改完点「保存到模板」，下次采集自动生效，不用改任何代码。",
                  style="Hint.TLabel", wraplength=1000, justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 8))

        cols = ("序", "产品名称", "订货分类", "商品编码")
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, w, a in (("序", 46, "center"), ("产品名称", 200, "w"),
                        ("订货分类", 200, "w"), ("商品编码", 220, "w")):
            tv.heading(c, text=c); tv.column(c, width=w, anchor=a)
        tv.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, command=tv.yview); sb.grid(row=2, column=1, sticky="ns")
        tv.config(yscrollcommand=sb.set)
        tv.tag_configure("newprod", background="#f2f7f2")     # 产品的第一行，浅底分组
        tv.tag_configure("dirty", foreground="#96591D")
        tv.bind("<Double-1>", lambda e: self.cat_edit())
        self.tv_cat = tv

        ops = ttk.Frame(f); ops.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(ops, text="新增产品", width=11, command=lambda: self.cat_edit(new="product")).pack(side="left")
        ttk.Button(ops, text="给选中产品加编码", width=17,
                   command=lambda: self.cat_edit(new="code")).pack(side="left", padx=6)
        ttk.Button(ops, text="修改选中", width=11, command=self.cat_edit).pack(side="left", padx=6)
        ttk.Button(ops, text="删除选中", width=11, command=self.cat_del).pack(side="left")
        ttk.Separator(ops, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(ops, text="上移", width=7, command=lambda: self.cat_move(-1)).pack(side="left", padx=3)
        ttk.Button(ops, text="下移", width=7, command=lambda: self.cat_move(1)).pack(side="left", padx=3)
        ttk.Label(ops, text="（顺序决定周表里产品列的先后）", style="Hint.TLabel").pack(side="left", padx=8)

        ttk.Label(f, text="注意：删掉某个产品只是以后不再采集，主数据里的历史记录不会被删除；"
                          "同一个商品编码不能归到两个产品下，否则会重复计数（保存时会拦下来）。",
                  style="Hint.TLabel", wraplength=1000, justify="left").grid(
            row=4, column=0, sticky="w", pady=(8, 0))

        self.cat_rows = []          # [[产品名称, 订货分类, 商品编码], ...]
        self.cat_dirty = False
        self.cat_load()

    def cat_load(self):
        if self.cat_dirty and not messagebox.askyesno(
                "有未保存的改动", "重新载入会丢弃当前未保存的改动，继续吗？"):
            return
        try:
            mod = load_category_mod()
            self.cat_rows = [list(t) for t in mod.flat_rows(JST_TEMPLATE)]
        except Exception as e:
            messagebox.showerror("读取失败", f"读不到分类编码：\n{e}")
            self.cat_rows = []
        self.cat_dirty = False
        self.cat_render()

    def cat_render(self):
        tv = self.tv_cat
        tv.delete(*tv.get_children())
        prev = None
        for i, (p, cat, code) in enumerate(self.cat_rows):
            tags = []
            if p != prev:
                tags.append("newprod")
                prev = p
            if self.cat_dirty:
                tags.append("dirty")
            tv.insert("", "end", iid=str(i), values=(i + 1, p, cat, code), tags=tuple(tags))
        prods = []
        for p, _, _ in self.cat_rows:
            if p not in prods:
                prods.append(p)
        self.lb_cat.config(
            text="%d 个产品、%d 条编码%s" % (len(prods), len(self.cat_rows),
                                          "　⚠ 有未保存的改动" if self.cat_dirty else ""),
            foreground="#96591D" if self.cat_dirty else "#666666")

    def cat_sel(self):
        s = self.tv_cat.selection()
        return int(s[0]) if s else None

    def cat_edit(self, new=None):
        """new=None 改选中行；new='product' 新增产品；new='code' 给选中产品加编码。"""
        i = self.cat_sel()
        if new is None and i is None:
            messagebox.showinfo("先选一行", "请先在表里点选要修改的那一行。")
            return
        if new == "code" and i is None:
            messagebox.showinfo("先选一行", "请先点选目标产品的任意一行，再点「给选中产品加编码」。")
            return
        if new == "product":
            init = ("", "", "")
        elif new == "code":
            init = (self.cat_rows[i][0], self.cat_rows[i][1], "")
        else:
            init = tuple(self.cat_rows[i])

        win = tk.Toplevel(self)
        win.title({"product": "新增产品", "code": "新增商品编码"}.get(new, "修改分类"))
        win.transient(self); win.grab_set(); win.resizable(False, False)
        frm = ttk.Frame(win, padding=14); frm.pack(fill="both", expand=True)
        names = []
        for p, _, _ in self.cat_rows:
            if p not in names:
                names.append(p)
        vs = []
        for r, (lab, val, hint) in enumerate((
                ("产品名称", init[0], "周表列名、日报周报里显示的就是它"),
                ("订货分类", init[1], "留空则与产品名称相同"),
                ("商品编码", init[2], "聚水潭里的商品编码，区分大小写"))):
            ttk.Label(frm, text=lab).grid(row=r, column=0, sticky="w", pady=4)
            v = tk.StringVar(value=val)
            if r == 0:
                w = ttk.Combobox(frm, textvariable=v, width=30, values=names)
            else:
                w = ttk.Entry(frm, textvariable=v, width=32)
            w.grid(row=r, column=1, sticky="w", padx=8)
            ttk.Label(frm, text=hint, style="Hint.TLabel").grid(row=r, column=2, sticky="w")
            vs.append(v)
            if (new == "product" and r == 0) or (new == "code" and r == 2) or new is None and r == 2:
                w.focus_set()

        def ok():
            p, cat, code = (x.get().strip() for x in vs)
            if not p or not code:
                messagebox.showwarning("填不全", "产品名称和商品编码都必须填。", parent=win)
                return
            cat = cat or p
            dup = [k for k, row in enumerate(self.cat_rows)
                   if row[2].lower() == code.lower() and k != (i if new is None else -1)]
            if dup:
                other = self.cat_rows[dup[0]]
                messagebox.showwarning(
                    "编码重复",
                    f"商品编码「{code}」已经归在产品「{other[0]}」下。\n"
                    "同一个编码归到两个产品会重复计数，请换一个。", parent=win)
                return
            if new is None:
                self.cat_rows[i] = [p, cat, code]
            elif new == "code":
                # 插到该产品最后一行之后，保持同产品的行连在一起
                last = max(k for k, row in enumerate(self.cat_rows) if row[0] == self.cat_rows[i][0])
                self.cat_rows.insert(last + 1, [p, cat, code])
            else:
                self.cat_rows.append([p, cat, code])
            self.cat_dirty = True
            win.destroy()
            self.cat_render()
            self.status.set("已改动，记得点「保存到模板」才会生效")

        bar = ttk.Frame(frm); bar.grid(row=3, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="确定", width=10, command=ok).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="取消", width=10, command=win.destroy).pack(side="left")
        win.update_idletasks()
        win.geometry("+%d+%d" % (self.winfo_rootx() + 150, self.winfo_rooty() + 160))

    def cat_del(self):
        i = self.cat_sel()
        if i is None:
            messagebox.showinfo("先选一行", "请先点选要删除的那一行。")
            return
        p, cat, code = self.cat_rows[i]
        same = [r for r in self.cat_rows if r[0] == p]
        extra = ("\n\n这是产品「%s」的最后一条编码，删掉等于**不再采集这个产品**。"
                 "历史数据不会被删除。" % p) if len(same) == 1 else ""
        if not messagebox.askyesno("确认删除", f"删除：{p} / {cat} / {code}{extra}"):
            return
        self.cat_rows.pop(i)
        self.cat_dirty = True
        self.cat_render()

    def cat_move(self, step):
        i = self.cat_sel()
        if i is None:
            return
        j = i + step
        if not (0 <= j < len(self.cat_rows)):
            return
        self.cat_rows[i], self.cat_rows[j] = self.cat_rows[j], self.cat_rows[i]
        self.cat_dirty = True
        self.cat_render()
        self.tv_cat.selection_set(str(j))
        self.tv_cat.see(str(j))

    def cat_save(self):
        if not self.cat_rows:
            messagebox.showwarning("不能为空", "至少要保留一个产品和一条编码。")
            return
        try:
            mod = load_category_mod()
            old = [list(t) for t in mod.flat_rows(JST_TEMPLATE)]
        except Exception as e:
            messagebox.showerror("读取失败", str(e)); return
        if old == self.cat_rows:
            messagebox.showinfo("没有改动", "分类编码与模板里的完全一致，无需保存。")
            self.cat_dirty = False; self.cat_render(); return

        def names(rows):
            out = []
            for p, _, _ in rows:
                if p not in out:
                    out.append(p)
            return out
        n_old, n_new = names(old), names(self.cat_rows)
        added = [p for p in n_new if p not in n_old]
        removed = [p for p in n_old if p not in n_new]
        msg = "将把改动写入：\n%s\n\n· 编码条数：%d → %d" % (
            JST_TEMPLATE, len(old), len(self.cat_rows))
        if added:
            msg += "\n· 新增产品：" + "、".join(added)
        if removed:
            msg += "\n· 移除产品：" + "、".join(removed) + "（历史数据保留，只是以后不再采集）"
        msg += "\n\n下次采集起生效，周表产品列与日报周报文案会自动跟随。确定保存吗？"
        if not messagebox.askyesno("确认保存", msg):
            return
        try:
            n = mod.save_flat_rows(JST_TEMPLATE, self.cat_rows)
        except Exception as e:
            messagebox.showerror("保存失败", f"模板未被改动。\n\n{e}"); return
        console_log(f"分类编码已保存：{len(old)}→{n} 条，新增 {added}，移除 {removed}")
        messagebox.showinfo("已保存", f"已写入 {n} 条。下次采集生效。")
        self.cat_dirty = False
        self.cat_load()

    # ============================================================ 定时页
    def build_sch(self):
        f = self.tab_sch
        f.columnconfigure(0, weight=1); f.rowconfigure(1, weight=1)
        top = ttk.Frame(f); top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Windows 计划任务", style="Head.TLabel").pack(side="left")
        ttk.Label(top, text="　（现有采集工具的定时，就在这里管）", style="Hint.TLabel").pack(side="left")
        ttk.Button(top, text="刷新", width=8, command=self.refresh_tasks).pack(side="right")

        cols = ("名称", "状态", "下次运行", "上次运行", "上次结果", "执行命令")
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        widths = (170, 70, 145, 145, 80, 340)
        for c, w in zip(cols, widths):
            tv.heading(c, text=c); tv.column(c, width=w, anchor="w")
        tv.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, command=tv.yview); sb.grid(row=1, column=1, sticky="ns")
        tv.config(yscrollcommand=sb.set)
        tv.tag_configure("off", foreground="#999999")
        tv.tag_configure("bad", foreground="#c0392b")
        self.tv_sch = tv
        self.tasks = []

        ops = ttk.Frame(f); ops.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(ops, text="修改运行时间：").pack(side="left")
        self.newtime = tk.StringVar(value="10:00")
        ttk.Entry(ops, textvariable=self.newtime, width=8, justify="center").pack(side="left")
        ttk.Button(ops, text="应用到选中项", command=self.change_time).pack(side="left", padx=6)
        ttk.Separator(ops, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(ops, text="立即运行一次", command=lambda: self.task_op("run")).pack(side="left", padx=3)
        ttk.Button(ops, text="启用", width=7, command=lambda: self.task_op("enable")).pack(side="left", padx=3)
        ttk.Button(ops, text="禁用", width=7, command=lambda: self.task_op("disable")).pack(side="left", padx=3)
        ttk.Button(ops, text="打开任务计划程序", command=self.open_taskschd).pack(side="right")

        tip = ("提示：这两个定时任务分别跑电商与聚水潭，已错开时间避免争抢资源。"
               "改时间会立即生效；「立即运行一次」等同于定时到点，会推送企业微信群。")
        ttk.Label(f, text=tip, style="Hint.TLabel", wraplength=980,
                  justify="left").grid(row=3, column=0, sticky="w", pady=(8, 0))

    def refresh_tasks(self):
        ps = ("Get-ScheduledTask | Where-Object { $_.TaskName -like '*爬虫*' -or "
              "$_.TaskName -like '*采集*' -or $_.TaskName -like '*常开*' } | ForEach-Object { "
              "$i = $_ | Get-ScheduledTaskInfo; "
              "[PSCustomObject]@{ Name=$_.TaskName; State=$_.State.ToString(); "
              # PS 5.1 里 if 只能靠 $( ) 当表达式用，写成 ( if ... ) 会报 'if' 不是命令
              "Next=$(if ($i.NextRunTime) { $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' }); "
              "Last=$(if ($i.LastRunTime) { $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' }); "
              "Result=$i.LastTaskResult; "
              "Action=(($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join '; ') } "
              "} | ConvertTo-Json -Compress")
        out, err, _ = powershell(ps)
        self.tasks = []
        try:
            data = json.loads(out) if out.strip() else []
            if isinstance(data, dict):
                data = [data]
            for d in data:
                self.tasks.append({"名称": d.get("Name", ""), "状态": d.get("State", ""),
                                   "下次运行": d.get("Next", ""), "上次运行": d.get("Last", ""),
                                   "上次结果": d.get("Result", ""), "执行命令": d.get("Action", "")})
        except Exception:
            pass
        tv = self.tv_sch
        tv.delete(*tv.get_children())
        for t in self.tasks:
            tag = ""
            if t["状态"] == "Disabled":
                tag = "off"
            elif str(t["上次结果"]) not in ("0", "", "None", "267011"):
                tag = "bad"
            res = {"0": "成功", "267011": "尚未运行"}.get(str(t["上次结果"]), str(t["上次结果"]))
            tv.insert("", "end", values=(t["名称"], {"Ready": "已启用", "Disabled": "已禁用",
                                                     "Running": "正在运行"}.get(t["状态"], t["状态"]),
                                         t["下次运行"], t["上次运行"], res, t["执行命令"]),
                      tags=(tag,) if tag else ())
        if not self.tasks and err.strip():
            tv.insert("", "end", values=("读取计划任务失败", err.strip()[:60], "", "", "", ""))

    def selected_task(self):
        sel = self.tv_sch.selection()
        if not sel:
            messagebox.showinfo("先选一行", "请先在列表里点选一个计划任务。")
            return None
        return self.tv_sch.item(sel[0], "values")[0]

    def change_time(self):
        name = self.selected_task()
        if not name:
            return
        t = self.newtime.get().strip()
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t):
            messagebox.showwarning("时间格式", "请填 HH:MM，例如 10:00。")
            return
        if not messagebox.askyesno("确认", f"把【{name}】的每日运行时间改为 {t} ？"):
            return
        out, err, rc = powershell(f'schtasks /change /tn "{name}" /st {t}')
        console_log(f"修改定时 {name} → {t}，返回 {rc}")
        messagebox.showinfo("结果", (out or err).strip()[:300] or ("成功" if rc == 0 else "失败"))
        self.refresh_tasks()

    def task_op(self, op):
        name = self.selected_task()
        if not name:
            return
        word = {"run": "立即运行一次", "enable": "启用", "disable": "禁用"}[op]
        extra = "\n\n注意：这等同于定时到点，会真的抓取并推送企业微信群。" if op == "run" else ""
        if not messagebox.askyesno("确认", f"确定{word}【{name}】？{extra}"):
            return
        cmd = {"run": f'schtasks /run /tn "{name}"',
               "enable": f'schtasks /change /tn "{name}" /enable',
               "disable": f'schtasks /change /tn "{name}" /disable'}[op]
        out, err, rc = powershell(cmd)
        console_log(f"{word} {name}，返回 {rc}")
        messagebox.showinfo("结果", (out or err).strip()[:300] or ("成功" if rc == 0 else "失败"))
        self.refresh_tasks()

    def open_taskschd(self):
        try:
            subprocess.Popen(["mmc", "taskschd.msc"], creationflags=NOWIN)
        except Exception as e:
            messagebox.showwarning("打不开", str(e))

    # ============================================================ 日志页
    def build_log(self):
        f = self.tab_log
        f.columnconfigure(0, weight=1); f.rowconfigure(1, weight=1)
        top = ttk.Frame(f); top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="来源：").pack(side="left")
        self.log_src = tk.StringVar(value="电商采集")
        cb = ttk.Combobox(top, textvariable=self.log_src, width=12, state="readonly",
                          values=[s[0] for s in LOG_SOURCES])
        cb.pack(side="left"); cb.bind("<<ComboboxSelected>>", lambda e: self.reload_dates())
        ttk.Label(top, text="　日期：").pack(side="left")
        self.log_date = tk.StringVar()
        self.cb_date = ttk.Combobox(top, textvariable=self.log_date, width=12, state="readonly")
        self.cb_date.pack(side="left")
        self.cb_date.bind("<<ComboboxSelected>>", lambda e: self.load_log())
        ttk.Label(top, text="　过滤：").pack(side="left")
        self.log_kw = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.log_kw, width=22); e.pack(side="left")
        e.bind("<Return>", lambda ev: self.load_log())
        ttk.Button(top, text="查找", width=6, command=self.load_log).pack(side="left", padx=4)
        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="自动刷新", variable=self.auto).pack(side="left", padx=8)
        ttk.Button(top, text="打开日志目录", command=self.open_logdir).pack(side="right")
        ttk.Button(top, text="刷新", width=7, command=self.load_log).pack(side="right", padx=6)

        self.log_text = tk.Text(f, font=MONO, wrap="none", relief="flat",
                                bg="#fbfcfc", padx=8, pady=6)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        sy = ttk.Scrollbar(f, command=self.log_text.yview); sy.grid(row=1, column=1, sticky="ns")
        sx = ttk.Scrollbar(f, orient="horizontal", command=self.log_text.xview)
        sx.grid(row=2, column=0, sticky="ew")
        self.log_text.config(yscrollcommand=sy.set, xscrollcommand=sx.set, state="disabled")
        self.log_text.tag_config("ERROR", foreground="#c0392b")
        self.log_text.tag_config("WARNING", foreground="#b9770e")
        self.log_text.tag_config("hit", background="#fff3a3")
        self.log_info = ttk.Label(f, text="", style="Hint.TLabel")
        self.log_info.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.reload_dates()
        self.after(3000, self.tick_auto)

    def src_dir(self):
        for name, d, pre in LOG_SOURCES:
            if name == self.log_src.get():
                return d, pre
        return LOGDIR, "console_"

    def reload_dates(self):
        d, pre = self.src_dir()
        files = []
        if os.path.isdir(d):
            files = sorted([x for x in os.listdir(d) if x.startswith(pre) and x.endswith(".log")],
                           reverse=True)
        dates = [re.sub(r"\D", "", x)[:8] for x in files]
        dates = [f"{x[:4]}-{x[4:6]}-{x[6:8]}" for x in dates if len(x) >= 8]
        self.cb_date["values"] = dates
        if dates:
            self.log_date.set(dates[0])
        else:
            self.log_date.set("")
        self.load_log()

    def log_path(self):
        d, pre = self.src_dir()
        ds = self.log_date.get().replace("-", "")
        return os.path.join(d, f"{pre}{ds}.log") if ds else ""

    def load_log(self):
        p = self.log_path()
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        if not p or not os.path.exists(p):
            self.log_text.insert("end", "（没有这一天的日志文件）")
            self.log_text.config(state="disabled")
            self.log_info.config(text="")
            return
        kw = self.log_kw.get().strip()
        n = shown = 0
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    n += 1
                    if kw and kw not in line:
                        continue
                    shown += 1
                    m = LINE_RE.match(line)
                    tag = m.group(2) if m and m.group(2) in ("ERROR", "WARNING") else None
                    self.log_text.insert("end", line, tag or ())
        except Exception as e:
            self.log_text.insert("end", f"读取失败：{e}")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self.log_info.config(text=f"{p}　共 {n} 行" + (f"，匹配 {shown} 行" if kw else ""))

    def tick_auto(self):
        if self.auto.get() and self.nb.index("current") == self.TAB_LOG:
            self.load_log()
        self.after(3000, self.tick_auto)

    def open_logdir(self):
        open_folder(self.src_dir()[0])

    # ============================================================ 报错页
    def build_err(self):
        f = self.tab_err
        f.columnconfigure(0, weight=1); f.rowconfigure(1, weight=1)
        top = ttk.Frame(f); top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="错误与警告汇总", style="Head.TLabel").pack(side="left")
        ttk.Label(top, text="　最近").pack(side="left")
        self.err_days = tk.StringVar(value="7")
        ttk.Combobox(top, textvariable=self.err_days, width=4, state="readonly",
                     values=["3", "7", "14", "30"]).pack(side="left")
        ttk.Label(top, text="天").pack(side="left")
        ttk.Label(top, text="　来源：").pack(side="left")
        self.err_src = tk.StringVar(value="全部")
        cbs = ttk.Combobox(top, textvariable=self.err_src, width=10, state="readonly",
                           values=["全部"] + [s[0] for s in LOG_SOURCES])
        cbs.pack(side="left"); cbs.bind("<<ComboboxSelected>>", lambda e: self.refresh_err())
        self.only_err = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="只看错误（不含警告）", variable=self.only_err,
                        command=self.refresh_err).pack(side="left", padx=12)
        ttk.Button(top, text="刷新", width=8, command=self.refresh_err).pack(side="right")

        cols = ("时间", "来源", "级别", "内容")
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (140, 90, 60, 640)):
            tv.heading(c, text=c); tv.column(c, width=w, anchor="w")
        tv.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f, command=tv.yview); sb.grid(row=1, column=1, sticky="ns")
        tv.config(yscrollcommand=sb.set)
        tv.tag_configure("ERROR", foreground="#c0392b")
        tv.tag_configure("WARNING", foreground="#b9770e")
        tv.tag_configure("LOGIN", foreground="#8e44ad")
        tv.bind("<Double-1>", self.show_err_detail)
        self.tv_err = tv

        self.err_sum = ttk.Label(f, text="", style="Hint.TLabel")
        self.err_sum.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(f, text="双击某行看完整内容。登录失效类问题：按提示在常开浏览器里重新登录该平台，"
                          "然后回到「启动」页补抓那一天即可。",
                  style="Hint.TLabel", wraplength=980, justify="left").grid(row=3, column=0,
                                                                            sticky="w", pady=(4, 0))
        self.refresh_err()

    def scan_errors(self):
        days = int(self.err_days.get())
        since = today() - dt.timedelta(days=days - 1)
        rows = []
        for name, d, pre in LOG_SOURCES:
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if not (fn.startswith(pre) and fn.endswith(".log")):
                    continue
                ds = re.sub(r"\D", "", fn)[:8]
                try:
                    fd = dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
                except Exception:
                    continue
                if fd < since:
                    continue
                try:
                    with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            m = LINE_RE.match(line.rstrip("\n"))
                            if not m:
                                continue
                            lvl = m.group(2)
                            if lvl == "ERROR" or (lvl == "WARNING" and not self.only_err.get()):
                                rows.append((m.group(1), name, lvl, m.group(3)))
                except Exception:
                    continue
        # 登录失效标记文件（现有工具写的，只读不删）
        for base, tagname in ((os.path.join(ECOM, "outputs"), "电商采集"),
                              (os.path.join(JST, "outputs"), "聚水潭采集")):
            if not os.path.isdir(base):
                continue
            for fn in os.listdir(base):
                if "需要重新登录" in fn:
                    p = os.path.join(base, fn)
                    ts = dt.datetime.fromtimestamp(os.path.getmtime(p))
                    if ts.date() >= since:
                        rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), tagname, "登录",
                                     f"{fn}（该平台登录已失效，需人工重登）"))
        rows.sort(key=lambda r: r[0], reverse=True)
        pick = self.err_src.get()
        if pick and pick != "全部":
            rows = [r for r in rows if r[1] == pick]
        return rows

    def refresh_err(self):
        try:
            rows = self.scan_errors()
        except Exception as e:
            rows = [(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", "控制台", "ERROR", f"扫描失败：{e}")]
        tv = self.tv_err
        tv.delete(*tv.get_children())
        ne = nw = nl = 0
        for t, src, lvl, msg in rows[:800]:
            tag = "LOGIN" if lvl == "登录" else lvl
            if lvl == "ERROR":
                ne += 1
            elif lvl == "WARNING":
                nw += 1
            else:
                nl += 1
            tv.insert("", "end", values=(t, src, lvl, msg[:300]), tags=(tag,))
        parts = [f"错误 {ne}"]
        if not self.only_err.get():
            parts.append(f"警告 {nw}")
        parts.append(f"登录失效 {nl}")
        self.err_sum.config(text="最近 " + self.err_days.get() + " 天：" + "　".join(parts)
                                 + ("　（无异常，一切正常）" if ne + nl == 0 else ""))

    def show_err_detail(self, _evt):
        sel = self.tv_err.selection()
        if not sel:
            return
        v = self.tv_err.item(sel[0], "values")
        win = tk.Toplevel(self)
        win.title("详情")
        win.geometry("760x300")
        t = tk.Text(win, font=MONO, wrap="word", padx=10, pady=8)
        t.pack(fill="both", expand=True)
        t.insert("end", f"时间：{v[0]}\n来源：{v[1]}\n级别：{v[2]}\n\n{v[3]}")
        t.config(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=6)

    # ============================================================
    def on_close(self):
        if self.running:
            if not messagebox.askyesno("还在采集", "采集尚未结束，确定要关闭吗？\n关闭会中止它。"):
                return
            try:
                if self.proc:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   capture_output=True, creationflags=NOWIN)
            except Exception:
                pass
        save_settings(self.cfg)
        self.destroy()


if __name__ == "__main__":
    if paths.MISSING:
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("找不到采集工程", paths.hint())
        sys.exit(1)
    Console().mainloop()
