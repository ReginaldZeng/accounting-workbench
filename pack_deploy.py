# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-08-05 | Author: Claude / c | Version: V2.191
# Description: 一键打服务器部署包（后端 zip → 04_部署包/）。此前历次手工打包，口径靠记忆，
#              本脚本把 V2.177 包的白名单口径固化下来，并新增两件事：
#              ① 自动写入 version_stamp.json——服务器上没有 .git，没有它左下角显示不出版本
#                （V2.176 的遗留项，就此闭环）；
#              ② 打包前校验前端产物比源码新（static/ 已出库，忘构建的话包里就是旧界面）。
#              安全口径（白名单制，永不误收）：conf.ini（金蝶密钥）、workbench.db（账号库）、
#              bank_uploads/（流水原件）、ec_uploads/、tempatt_uploads/（打卡原件，含人事敏感信息）、
#              真实 config/ledger/claims JSON 一概不进包。
#              用法：双击 pack_deploy.bat（先自动构建前端再打包）。
# Date: 2026-08-12 | Author: Claude / c | Version: V2.277
# Description: 加【分支闸】——不在 main 上拒绝打包（本地自测走 --allow-branch，包名带分支标记）。
#              起因：服务器跑在未合并分支上、又传了另一条分支的包，两个包各自都是"完整的
#              工作台"只是各缺对方，一覆盖就把对方的菜单与路由整块抹掉。一个应用只能有
#              一个出包源，规矩固化成脚本，靠人记规矩防不住。
import os
import re
import json
import zipfile
import datetime
import subprocess
import sys

# 中文 Windows 控制台默认 GBK，特殊字符会炸——统一按 UTF-8 出，编不出的替换
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
BK = os.path.join(ROOT, "01_Current_Deliverables", "app", "backend")
OUT_DIR = os.path.join(ROOT, "04_部署包")

# ── 白名单（对齐 V2.177 手工包，另加 core.py / routers/ / version_stamp.json）──
# ⚠ 顶层是**白名单**，新增顶层模块必须往这里加一行，否则静默漏掉——
#   routers/kernels/tools 是按目录通配收的，不吃这个坑；顶层文件会。
#   V2.241 实翻车：report_export.py 已提交入库，打出来的包里却没有它。
#   下方 main() 末尾有一道自检，会把"已跟踪但没进包"的顶层 .py 报出来，别关掉它。
TOP_FILES = ["app.py", "core.py", "db.py", "kingdee_client.py", "mailer.py",
             "notifier.py", "report_export.py", "sample_data.py", "conf.ini.example",
             "requirements.txt", "version_stamp.json",
             "gateway.py", ".env.example"]   # V2.302 AI 网关（独立进程 8020；真 .env/gateway.db 不进包）
DIR_GLOBS = {
    "kernels": lambda n: n.endswith(".py"),
    "routers": lambda n: n.endswith(".py"),
    "tools": lambda n: n.endswith(".py"),
    "templates": lambda n: n.endswith(".xlsx"),    # 计提长表模板等下载件（别放 static——build 会清空）
    "static": lambda n: True,                      # 构建产物整个进
}
SAMPLE_WHITELIST = ["cost_ledger_config.json", "ledger_authoritative.README.txt",
                    "ledger_authoritative.sample.json", "logistics_recon_极鲜达.json"]


def _git(*args):
    # core.quotepath=false：不关的话中文路径会被转义成 "\346..." 带引号形式，
    # 已跟踪判定会把所有中文文件名误判成未入库（实翻过车：极鲜达配置被误跳过）
    try:
        r = subprocess.run(["git", "-C", ROOT, "-c", "core.quotepath=false", *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _ver_key(v):
    m = re.match(r"[Vv](\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def main():
    # 1) 前端产物新鲜度：比 frontend 源码旧就拒绝打包（防止把旧界面发上服务器）
    idx = os.path.join(BK, "static", "index.html")
    if not os.path.exists(idx):
        print("[X] 前端产物缺失（backend/static/）。先跑 build_frontend.bat 再打包。")
        return 1
    bt = os.path.getmtime(idx)
    fe = os.path.join(ROOT, "01_Current_Deliverables", "app", "frontend")
    newest = 0.0
    for sub in ("src", "index.html", "vite.config.js", "package.json"):
        p = os.path.join(fe, sub)
        if os.path.isfile(p):
            newest = max(newest, os.path.getmtime(p))
        elif os.path.isdir(p):
            for r_, _, ns in os.walk(p):
                for n in ns:
                    newest = max(newest, os.path.getmtime(os.path.join(r_, n)))
    if newest > bt:
        print("[X] 前端产物比源码旧。先跑 build_frontend.bat（或双击 pack_deploy.bat 会自动构建）。")
        return 1

    # 2) 版本戳：git 分支/提交 + 版本号（台账片段最大号 vs 提交标题 V 号，取大）
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    commit = _git("rev-parse", "--short", "HEAD")

    # 2.1) 【分支闸】不在 main 上就不给打服务器包（V2.277）
    # ——2026-08-12 实翻车：服务器长期跑在未合并的 dashboard-section-rename 分支上，
    #   业务方又传了存货台账分支打的包，两个包各自都是"完整的工作台"、只是各缺对方，
    #   一覆盖就把对方的菜单和路由整块抹掉（报表导出当场不可用）。
    #   根因是**一个应用被多条分支分别打包**：前端是整包编译的，没法按线拆分，
    #   所以任何分支包都是"缺了别人的完整工作台"。规矩只能是【只从 main 打包】。
    # 本地自测要打包 → 加 --allow-branch，包名和版本戳都会带分支标记，
    #   一眼看得出这不是主干包，传错了在服务器左下角也认得出来。
    allow_branch = "--allow-branch" in sys.argv
    if branch != "main" and not allow_branch:
        print("[X] 当前在分支 %s 上，拒绝打包。" % branch)
        print("    服务器包只能从 main 打——分支包必然缺其它线的东西，覆盖上去会把别人的功能抹掉")
        print("    （2026-08-12 实翻车过一次，报表导出被存货台账的包整块盖没）。")
        print()
        print("    要发服务器：先把本分支合进 main，在主库目录再跑一次本脚本。")
        print("    只是本地自测：python pack_deploy.py --allow-branch")
        return 1
    if branch != "main":
        print("[!] 分支包（%s）——仅供本地自测，**不要传服务器**。" % branch)
    # dirty 只看「应用目录内已跟踪文件的改动」——别处的未跟踪草稿(-uno 排除)不影响部署包成色
    dirty = bool(_git("status", "--porcelain", "-uno", "--", "01_Current_Deliverables/app"))
    cands = []
    # 扫最近 30 条提交标题的 V 号（不止 HEAD）——合并到 main 后 HEAD 常是无 V 号的 merge 提交，
    # 只看 HEAD 会把版本戳漏回台账旧片段号（实翻车：服务器页脚卡 V2.422）
    for line in _git("log", "-30", "--format=%s").split("\n"):
        m = re.search(r"[Vv]\d+\.\d+", line)
        if m:
            cands.append(m.group(0))
    frag = os.path.join(ROOT, "00_Change_Log")
    if os.path.isdir(frag):
        for fn in os.listdir(frag):
            mm = re.match(r"(V\d+\.\d+)_", fn)
            if mm:
                cands.append(mm.group(1))
    ver = max(cands, key=_ver_key) if cands else ""
    stamp = {"ver": ver, "branch": branch, "commit": commit,
             "packed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "dirty": dirty}
    with open(os.path.join(BK, "version_stamp.json"), "w", encoding="utf-8") as f:
        json.dump(stamp, f, ensure_ascii=False, indent=1)
    if dirty:
        print("[!] 工作区有未提交改动——包里内容与 git 提交不完全对应（戳记已标 dirty）。")

    # 2.5) 只收 git 已跟踪的源码文件——工作区里未提交的在途文件(别的开发线的半成品)不上服务器。
    #      static/ 与 version_stamp.json 是有意生成的产物(已 gitignore)，豁免此检查。
    rel_bk = "01_Current_Deliverables/app/backend/"
    tracked = {ln[len(rel_bk):] for ln in _git("ls-files", "--", rel_bk).split("\n") if ln.startswith(rel_bk)}

    def _shippable(rel):
        rel = rel.replace("\\", "/")
        if rel.startswith("static/") or rel == "version_stamp.json":
            return True
        if rel in tracked:
            return True
        print(f"  [!] 跳过未入库文件（不上服务器）：{rel}")
        return False

    # 3) 打 zip（结构与历次手工包一致：backend 内容平铺在 zip 根）
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    # 分支包在文件名里带分支标记——万一还是传了，服务器左下角徽标也会显示分支名（V2.176 自报）
    tag = "" if branch == "main" else "_分支" + re.sub(r"[^A-Za-z0-9一-龥]+", "-", branch.replace("claude/", ""))[:24]
    zpath = os.path.join(OUT_DIR, f"finance_workbench_backend_{ver or 'Vx'}{tag}_{today}.zip")
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in TOP_FILES:
            p = os.path.join(BK, fn)
            if os.path.exists(p) and _shippable(fn):
                z.write(p, fn); n += 1
        for d, ok in DIR_GLOBS.items():
            base = os.path.join(BK, d)
            if not os.path.isdir(base):
                continue
            for r_, dirs, ns in os.walk(base):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for name in ns:
                    if ok(name):
                        full = os.path.join(r_, name)
                        rel = os.path.relpath(full, BK).replace("\\", "/")
                        if _shippable(rel):
                            z.write(full, rel); n += 1
        for fn in SAMPLE_WHITELIST:
            p = os.path.join(BK, "sample_data", fn)
            if os.path.exists(p) and _shippable(f"sample_data/{fn}"):
                z.write(p, f"sample_data/{fn}"); n += 1

    # 3.5) 白名单自检：git 已跟踪的顶层 .py，有没有因为忘了加进 TOP_FILES 而被漏掉。
    #      这是 V2.241 的实际教训——report_export.py 明明已入库，包里却没有，
    #      装上去表面正常、一点报表导出就 500。漏掉的是【代码】，比漏配置更难查。
    missed = sorted(f for f in tracked
                    if f.endswith(".py") and "/" not in f and f not in TOP_FILES)
    if missed:
        print("[X] 下列顶层模块已入库但**没进包**（TOP_FILES 白名单漏了）：")
        for f in missed:
            print(f"      {f}")
        print("    → 把它们加进 pack_deploy.py 的 TOP_FILES，重新打包。本次产物已作废。")
        os.remove(zpath)
        return 1

    mb = os.path.getsize(zpath) / 1048576
    print(f"[OK] {os.path.basename(zpath)}  （{n} 个文件，{mb:.1f} MB）")
    print(f"     版本戳：{stamp['ver']} · {stamp['branch']} · {stamp['commit']}"
          + ("（dirty）" if dirty else ""))
    print()
    print("── 服务器更新步骤（宝塔）──────────────────────")
    print("  1. 宝塔→文件→进入项目 backend 目录，上传本 zip，解压【覆盖】")
    print("     （conf.ini / workbench.db / bank_uploads / ec_uploads / tempatt_uploads 不在包里，不会被动到）")
    print("  2. 宝塔→Python 项目→终端执行 pip install -r requirements.txt")
    print("     （包只覆盖代码不装依赖——V2.218 实翻车：链盟 .xls 因服务器缺 xlrd 全军覆没）")
    print("  3. 宝塔→Python 项目→重启")
    print("  4. 验收：/api/health 返回 200；登录后左下角显示版本号；")
    print("     侧栏底部出现 ◐ 色调切换钮（系统深色的同事点到「浅色」即恢复）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
