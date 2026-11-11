# -*- coding: utf-8 -*-
"""
读『分类编码』sheet -> 产品名称 => [商品编码] 分组（数据驱动，唯一分类真相源）。

分类编码表是多列块布局，每块三列：产品名称 | 订货分类 | 商品编码。
产品名称在纵向合并单元格里（只有块顶有值），需向下填充。
本模块自动识别所有以『产品名称』为表头的列块，无视具体有几块、几组，
所以以后你在分类编码里增删产品/编码，工具自动跟随，无需改代码。
"""
from __future__ import annotations
import os
from typing import List, Tuple
import openpyxl

SHEET = "分类编码"
COL_PRODUCT = "产品名称"
COL_CODE = "商品编码"


def _code_str(v) -> str:
    """商品编码可能是数字(如 20103183)或字符串(如 '20103183g')，统一成不带小数的字符串。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return str(v).strip()


def load_groups(template_path: str) -> List[Tuple[str, List[str]]]:
    """返回 [(产品名称, [商品编码,...]), ...]，保持分类编码表中出现的顺序。"""
    wb = openpyxl.load_workbook(template_path, data_only=True)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"模板缺少『{SHEET}』sheet：{template_path}")
    ws = wb[SHEET]

    # 1) 定位每个列块：表头行里所有『产品名称』所在列 c => (产品名称列c, 商品编码列c+2)
    header = {c: (ws.cell(1, c).value or "") for c in range(1, ws.max_column + 1)}
    blocks = []
    for c, name in header.items():
        if str(name).strip() == COL_PRODUCT:
            code_col = c + 2  # 产品名称 | 订货分类 | 商品编码
            # 校验第三列确是商品编码
            if str(ws.cell(1, code_col).value or "").strip() == COL_CODE:
                blocks.append((c, code_col))

    # 2) 逐块向下填充产品名称，收集编码
    groups = []              # [(产品名称,[codes])]
    index = {}               # 产品名称 -> groups 下标（同名合并）
    for pcol, ccol in blocks:
        cur = None
        for r in range(2, ws.max_row + 1):
            pv = ws.cell(r, pcol).value
            if pv is not None and str(pv).strip():
                cur = str(pv).strip()
            code = _code_str(ws.cell(r, ccol).value)
            if not code:
                continue
            if cur is None:
                continue
            if cur not in index:
                index[cur] = len(groups)
                groups.append((cur, []))
            lst = groups[index[cur]][1]
            if code not in lst:
                lst.append(code)
    wb.close()
    return groups


def product_names(template_path: str) -> List[str]:
    return [g[0] for g in load_groups(template_path)]


# ---------------------------------------------------------------- 编辑（增删改）
def flat_rows(template_path: str) -> List[Tuple[str, str, str]]:
    """把分类编码表读成扁平三元组 [(产品名称, 订货分类, 商品编码), ...]，供界面编辑。

    与 load_groups 的区别：这里保留「订货分类」并且不做同名合并，
    产品名称/订货分类都已向下填充好，每行都是完整的一条。
    """
    wb = openpyxl.load_workbook(template_path, data_only=True)
    ws = wb[SHEET]
    header = {c: str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)}
    out: List[Tuple[str, str, str]] = []
    for c, name in header.items():
        if name != COL_PRODUCT or str(ws.cell(1, c + 2).value or "").strip() != COL_CODE:
            continue
        cur_p = cur_c = None
        for r in range(2, ws.max_row + 1):
            pv, cv = ws.cell(r, c).value, ws.cell(r, c + 1).value
            if pv is not None and str(pv).strip():
                cur_p = str(pv).strip()
                cur_c = None                      # 换产品了，订货分类重新开始
            if cv is not None and str(cv).strip():
                cur_c = str(cv).strip()
            code = _code_str(ws.cell(r, c + 2).value)
            if code and cur_p:
                out.append((cur_p, cur_c or cur_p, code))
    wb.close()
    return out


def save_flat_rows(template_path: str, rows) -> int:
    """把扁平三元组写回『分类编码』sheet：整块重写 + 恢复纵向合并 + 沿用原有样式。

    · 用 data_only=False 打开，**保住其它 sheet 的公式**（实发/实退表底那三行是公式）；
    · 只动『分类编码』这一个 sheet 的前三列，其它一概不碰；
    · 同一产品名连续多行会重新合并，产品内同一订货分类也合并（与人工维护的样式一致）；
    · 返回写入的行数。
    """
    from copy import copy
    rows = [(str(p).strip(), str(cat).strip(), _code_str(code))
            for p, cat, code in rows if str(p).strip() and _code_str(code)]
    if not rows:
        raise ValueError("分类编码不能为空——至少要保留一个产品和一个编码")

    wb = openpyxl.load_workbook(template_path)          # 保留公式
    ws = wb[SHEET]
    ref = ws.cell(2, 1)                                  # 以第 2 行为样式模板
    style = (copy(ref.font), copy(ref.alignment), copy(ref.border))
    old_max = ws.max_row

    for m in [str(m) for m in ws.merged_cells.ranges]:   # 先全部取消合并
        ws.unmerge_cells(m)

    for i, (p, cat, code) in enumerate(rows):
        r = 2 + i
        for col, val in ((1, p), (2, cat), (3, code)):
            cell = ws.cell(r, col, val)
            cell.font, cell.alignment, cell.border = (copy(style[0]), copy(style[1]),
                                                      copy(style[2]))
    # 多余的旧行：清值 + 去边框，避免留下空的带框行
    from openpyxl.styles import Border
    for r in range(2 + len(rows), old_max + 1):
        for col in (1, 2, 3):
            cell = ws.cell(r, col)
            cell.value = None
            cell.border = Border()

    def merge_runs(col, keyfn):
        start = 0
        for i in range(1, len(rows) + 1):
            same = i < len(rows) and keyfn(rows[i]) == keyfn(rows[start])
            if not same:
                if i - start > 1:
                    ws.merge_cells(start_row=2 + start, start_column=col,
                                   end_row=2 + i - 1, end_column=col)
                start = i

    merge_runs(1, lambda t: t[0])                        # 产品名称
    merge_runs(2, lambda t: (t[0], t[1]))                # 同产品内的订货分类
    wb.save(template_path)
    wb.close()
    return len(rows)


# ---------------------------------------------------------------- 命令行维护
def _ask(prompt: str) -> str:
    """读一行输入；没有 stdin（被重定向/管道）时当作放弃，不抛堆栈。"""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def manage_cli(template_path: str) -> None:
    """命令行下查看/维护分类编码，供各脚本工具的菜单调用（可视化工具用『产品分类』页）。

    只做最常用的三件事：看当前分类、新增一条、打开模板用 Excel 手改。
    复杂的改序/批量删除请用可视化工具那一页，或直接开 Excel 改。
    """
    while True:
        try:
            groups = load_groups(template_path)
        except Exception as e:
            print(f"读取分类编码失败：{e}")
            return
        total = sum(len(cs) for _, cs in groups)
        print(f"\n===== 产品分类编码（{len(groups)} 个产品 / {total} 条编码）=====")
        for i, (name, codes) in enumerate(groups, 1):
            print(f" {i:2d}) {name}（{len(codes)} 条）: {','.join(codes)}")
        print("\n 这张表决定：采集抓哪些编码、周表有哪些产品列、日报周报列哪些产品。")
        print(" a) 新增一条（产品名称 + 订货分类 + 商品编码）")
        print(" e) 用 Excel 打开模板手工改（改完存盘即生效）")
        print(" 0) 返回")
        ch = _ask("请选择：").strip().lower()
        if ch == "0" or not ch:
            return
        if ch == "e":
            try:
                os.startfile(template_path)
                print("已打开模板。改的是『分类编码』sheet，存盘后下次采集自动生效。")
            except Exception as e:
                print(f"打开失败：{e}\n路径：{template_path}")
            return
        if ch != "a":
            print("无效选择")
            continue

        name = _ask("产品名称（周表列名，回车取消）：").strip()
        if not name:
            continue
        cat = _ask(f"订货分类（回车＝与产品名称相同『{name}』）：").strip() or name
        code = _ask("商品编码：").strip()
        if not code:
            print("商品编码不能为空")
            continue
        rows = flat_rows(template_path)
        dup = [r for r in rows if r[2].lower() == code.lower()]
        if dup:
            print(f"× 编码「{code}」已归在产品「{dup[0][0]}」下——同一编码归两个产品会重复计数，已取消。")
            continue
        # 已存在的产品：插到它最后一条之后，保持同产品的行连在一起
        last = max((i for i, r in enumerate(rows) if r[0] == name), default=None)
        if last is None:
            rows.append((name, cat, code))
            where = "作为新产品追加到末尾"
        else:
            rows.insert(last + 1, (name, cat, code))
            where = f"追加到已有产品「{name}」下"
        if _ask(f"确认{where}：{name} / {cat} / {code}？(y/N) ").strip().lower() != "y":
            print("已取消")
            continue
        try:
            n = save_flat_rows(template_path, rows)
            print(f"✅ 已保存，共 {n} 条编码。下次采集起生效（周表列与日报周报文案自动跟随）。")
        except Exception as e:
            print(f"× 保存失败，模板未被改动：{e}")


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "每周各渠道实发_实退数据_模板.xlsx")
    gs = load_groups(p)
    print(f"共 {len(gs)} 个产品分类：")
    for name, codes in gs:
        print(f"  {name}({len(codes)}): {','.join(codes)}")
