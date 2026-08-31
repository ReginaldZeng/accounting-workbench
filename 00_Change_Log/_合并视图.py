"""
[Change Log]
Date: 2026-08-05
Author: Claude / c
Version: V2.170
Description: 把 00_Change_Log.md(历史正文) 与 00_Change_Log/ 下的片段拼成一份完整台账视图，
             输出到项目根 00_Change_Log_合并视图.md（生成物，已 gitignore，不入库）。
             用法：python 00_Change_Log/_合并视图.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HISTORY = os.path.join(ROOT, "00_Change_Log.md")
OUT = os.path.join(ROOT, "00_Change_Log_合并视图.md")

FIELDS = ("version", "date", "line", "author", "status", "revision")


def _ver_key(v):
    """V2.170 -> (2, 170)；解析不出来的排最后，保证脚本不因命名不规范而崩。"""
    m = re.match(r"[Vv]?(\d+)\.(\d+)", (v or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def _parse(path):
    """读一个片段：抽 YAML 元信息块 + 正文。元信息缺失时按文件名兜底。"""
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    meta, body = {}, raw
    m = re.search(r"^---\s*$(.*?)^---\s*$(.*)", raw, re.S | re.M)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip().lower()
                if k in FIELDS:
                    meta[k] = v.strip()
        body = m.group(2)

    # 去掉正文开头可能残留的 [Change Log] 头
    body = re.sub(r"^\s*\[Change Log\][\s\S]*?(?=\n\s*\n)", "", body).strip()

    name = os.path.splitext(os.path.basename(path))[0]
    parts = name.split("_")
    meta.setdefault("version", parts[0] if parts else name)
    meta.setdefault("line", parts[1] if len(parts) > 1 else "")
    meta.setdefault("date", "")
    meta.setdefault("author", "")
    meta.setdefault("status", "")
    meta.setdefault("revision", "")
    meta["_body"] = body
    meta["_file"] = os.path.basename(path)
    return meta


def _row(meta):
    """拼成与历史表同构的一行（Description 单元格内不能有换行，压平）。"""
    desc = re.sub(r"\s*\n\s*", " ", meta["_body"]).replace("|", "\\|").strip()
    return "| **{version}** | {date} | {desc} | {author} | {status} | {revision} |".format(
        desc=desc, **{k: meta.get(k, "") for k in FIELDS}
    )


def main():
    if not os.path.exists(HISTORY):
        print(f"[X] 找不到历史台账：{HISTORY}")
        return 1

    frags = [
        _parse(os.path.join(HERE, fn))
        for fn in os.listdir(HERE)
        if fn.endswith(".md") and not fn.startswith(("_", "README"))
    ]
    frags.sort(key=lambda m: _ver_key(m["version"]), reverse=True)

    draft = [f for f in frags if _ver_key(f["version"]) == (-1, -1)]
    if draft:
        print("[!] 以下片段版本号未定（合并进 main 前请定号）：")
        for f in draft:
            print(f"    - {f['_file']}")

    with open(HISTORY, encoding="utf-8") as f:
        history = f.read()

    head = (
        "> ⚠️ **本文件是生成物，请勿手工编辑、请勿入库。**\n"
        "> 由 `00_Change_Log/_合并视图.py` 拼接 `00_Change_Log.md`（V2.169 及以前）"
        "与 `00_Change_Log/`（V2.170 起）生成。\n"
        f"> 本次合入片段 {len(frags)} 条。要改台账，请改片段文件。\n\n"
    )

    # 新条目插在历史表头之后（最新置顶，与既有规则一致）
    sep = "|---------|------|-------------|--------|--------|-----------------|"
    if frags and sep in history:
        block = "\n".join(_row(m) for m in frags)
        history = history.replace(sep, sep + "\n" + block, 1)
    elif frags:
        print("[!] 未在历史台账中找到版本索引表头，片段改为附加在文末。")
        history += "\n\n## 版本索引续表（V2.170 起）\n\n" + "\n".join(
            _row(m) for m in frags
        )

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(head + history)

    print(f"[OK] 已生成 {OUT}（片段 {len(frags)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
