# -*- coding: utf-8 -*-
# [Change Log]
# Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.796（数字员工办公室）
# Description: 数字员工办公室大屏 · 把生图原图处理成能上屏的素材（开发机上跑一次，产物进 frontend/public/office-art/）。
#   原图是业务方用生图工具出的（B 半写实插画风 + 品牌布局 v4），放在「05 数字员工办公室/03_Source_Materials/大屏素材/」，
#   尺寸和格子都不是整数（坐姿每格 443.5、走动每格 295.67），人物在格子里的位置也有细微偏差——直接按固定像素裁会抖。
#   这里做四件事：
#     1. 图集按整图等分裁格 → 每格按「人物底边贴格子下沿、头部水平居中」重新对齐 → 统一成 256×256 → 拼回图集（WebP）。
#     2. 空椅子裁成和坐姿图里椅背一样大的取景。
#     3. 底图转 WebP；另抠一层「前景」＝桌上的显示器（人坐在桌子后面，显示器应该挡在人前面）。
#     4. 品牌 LOGO 原文件原样拷贝（不裁、不重绘、不压缩）。
#   用法：python tools/office_art_pack.py <原图文件夹> <输出文件夹>
#   依赖 Pillow + numpy（只在开发机用，不是后端运行依赖）。
import os
import shutil
import sys
import zipfile

import numpy as np
from PIL import Image

CELL = 256
BG_NAME = "office-bg-brand-v3.png"
V4_ZIP = "星期零风控中心数字员工办公室_品牌布局_v4_无顶部横幅_20261004.zip"


def _bbox(a, thr=24):
    ys, xs = np.where(a > thr)
    if not len(xs):
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _cells(im, cols, rows):
    w, h = im.size
    for r in range(rows):
        for c in range(cols):
            yield r, c, im.crop((round(c * w / cols), round(r * h / rows), round((c + 1) * w / cols), round((r + 1) * h / rows)))


def _clean_edges(a):
    """原图不是严格按格子画的：上一排的鞋底、下一排的头顶、隔壁的手会伸进这一格的边上。
    贴着格子边、又和主体隔着一段空白的碎片一律清掉（否则小人头顶会多出两道黑线，底边对齐也会被带偏）。"""
    a = a.copy()
    m = a > 24
    for axis in (0, 1):
        on = m.any(axis=1 - axis)                 # axis=0 看各行有没有内容，axis=1 看各列
        n = len(on)
        lim = int(n * 0.28)
        if on[:3].any():
            for i in range(3, lim):
                if not on[i:i + 5].any():
                    (a.__setitem__((slice(0, i), slice(None)), 0) if axis == 0 else a.__setitem__((slice(None), slice(0, i)), 0))
                    break
        if on[-3:].any():
            for j in range(n - 3, n - lim, -1):
                if not on[j - 5:j].any():
                    (a.__setitem__((slice(j, n), slice(None)), 0) if axis == 0 else a.__setitem__((slice(None), slice(j, n)), 0))
                    break
    return a


def _drop_edge_chips(a, sides="tlrb"):
    """贴着格子边、和主体不相连的小碎块（上一排的鞋底、下一排的头顶尖）——哪怕只隔一两行也清掉。
    做法：从格子四条边上的不透明像素出发找连通块，面积不到整格人物 4% 的算碎块，连同它周围 1 像素一起抹掉。"""
    a = a.copy()
    m = a > 24
    h, w = m.shape
    total = int(m.sum())
    if not total:
        return a
    seen = np.zeros_like(m)
    starts = []
    if "t" in sides:
        starts += [(0, x) for x in range(w)]
    if "b" in sides:
        starts += [(h - 1, x) for x in range(w)]
    if "l" in sides:
        starts += [(y, 0) for y in range(h)]
    if "r" in sides:
        starts += [(y, w - 1) for y in range(h)]
    for sy, sx in starts:
        if not m[sy, sx] or seen[sy, sx]:
            continue
        stack, comp = [(sy, sx)], []
        seen[sy, sx] = True
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(comp) < total * 0.04:
            ys, xs = [c[0] for c in comp], [c[1] for c in comp]
            a[max(0, min(ys) - 1):max(ys) + 2, max(0, min(xs) - 1):max(xs) + 2] = 0
    return a


def _place(cell, scale, mode, pad_bottom=0, keep_bottom=False):
    """把一格里的人物缩放后放进 256×256：底边贴下沿；mode=head 按头部（上 45%）水平居中，mode=body 按整体居中。
    keep_bottom=True（坐姿）：人物本来就在腰线处被裁平、贴着格子下沿，下边不当碎片清。"""
    a0 = np.asarray(cell.split()[-1])
    a0 = _drop_edge_chips(a0, "tlr" if keep_bottom else "tlrb")     # 坐姿下沿本来就贴边（腰线裁平），不查下边
    a = _clean_edges(a0)
    if keep_bottom:                                # 坐姿：只清上、左、右三边的碎片
        keep = a0.copy(); keep[: int(a0.shape[0] * 0.5)] = a[: int(a0.shape[0] * 0.5)]
        cols = (a > 24).any(axis=0); keep[:, ~cols] = 0
        a = keep
    cell = cell.copy(); cell.putalpha(Image.fromarray(a))
    bb = _bbox(a)
    out = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    if not bb:
        return out
    x0, y0, x1, y1 = bb
    if mode == "head":
        top = a[y0:y0 + max(8, int((y1 - y0) * 0.45))]
        xs = np.where(top > 24)[1]
        cx = (xs.min() + xs.max() + 1) / 2 if len(xs) else (x0 + x1) / 2
    else:
        cx = (x0 + x1) / 2
    crop = cell.crop((x0, y0, x1, y1))
    w, h = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    crop = crop.resize((w, h), Image.LANCZOS)
    ox = round(CELL / 2 - (cx - x0) * scale)
    out.alpha_composite(crop, (max(0, min(CELL - w, ox)), max(0, CELL - pad_bottom - h)))
    return out


def sheet(src, dst, cols, rows, mode, pad_bottom=0, keep_bottom=False):
    im = Image.open(src).convert("RGBA")
    scale = CELL / (im.size[0] / cols)
    atlas = Image.new("RGBA", (CELL * cols, CELL * rows), (0, 0, 0, 0))
    for r, c, cell in _cells(im, cols, rows):
        atlas.alpha_composite(_place(cell, scale, mode, pad_bottom, keep_bottom), (c * CELL, r * CELL))
    atlas.save(dst, "WEBP", quality=90, method=6)
    return os.path.getsize(dst)


# 只有定妆照（一张正面站姿）、动作图集还没出的人物：先用定妆照凑一套「替身图集」顶上，脸和衣服是对的，但不会动——
# 坐姿 8 格全是同一张上半身（头到胸口，手在桌下），走动 18 格全是同一张站姿（走路是平移，不迈腿）。
# 动作图集（char-NN-seated.png / char-NN-walk.png）到了以后重跑本脚本，会用真图集盖掉替身。
STANDIN = {10: "新增人物_10-12_20261004/char-10-casual-v3.png", 11: "新增人物_10-12_20261004/char-11-casual-v3.png",
           12: "新增人物_10-12_20261004/char-12-casual-v3.png"}       # 业务方 2026-10-05 发来的是「休闲服装 v3」这一版


def standin(src, seat_dst, walk_dst):
    im = Image.open(src).convert("RGBA")
    a = np.asarray(im.split()[-1])
    x0, y0, x1, y1 = _bbox(a)
    H = y1 - y0
    hx = np.where(a[y0:y0 + int(H * .4)] > 24)[1]
    hx0, hx1 = hx.min(), hx.max() + 1                    # 头（含头发）的左右边
    cx = (hx0 + hx1) / 2
    # 走动格：整个人缩到和别人站姿差不多高（约 240），脚离格子下沿 6
    k = 240 / H
    fig = im.crop((x0, y0, x1, y1)).resize((round((x1 - x0) * k), 240), Image.LANCZOS)
    cell = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    cell.alpha_composite(fig, (round(CELL / 2 - (cx - x0) * k), CELL - 6 - 240))
    walk = Image.new("RGBA", (CELL * 6, CELL * 3), (0, 0, 0, 0))
    for i in range(18):
        walk.alpha_composite(cell, ((i % 6) * CELL, (i // 6) * CELL))
    walk.save(walk_dst, "WEBP", quality=90, method=6)
    # 坐姿格：按头的宽度对齐到别人坐姿的大小（头宽约 150），从头顶截到胸口（手垂在更下面，截不到）
    k = 150 / (hx1 - hx0)
    cut = y0 + int(H * .54)
    top = im.crop((x0, y0, x1, cut)).resize((round((x1 - x0) * k), round((cut - y0) * k)), Image.LANCZOS)
    cell = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    cell.alpha_composite(top, (round(CELL / 2 - (cx - x0) * k), CELL - top.size[1]))
    seat = Image.new("RGBA", (CELL * 4, CELL * 2), (0, 0, 0, 0))
    for i in range(8):
        seat.alpha_composite(cell, ((i % 4) * CELL, (i // 4) * CELL))
    seat.save(seat_dst, "WEBP", quality=90, method=6)
    return os.path.getsize(seat_dst) + os.path.getsize(walk_dst)


def chair(src, dst):
    """空椅子：取景对齐到坐姿图——椅背和坐姿图里的一样宽，露出来的高度一样（下面被桌子挡住的部分裁掉）。"""
    im = Image.open(src).convert("RGBA")
    a = np.asarray(im.split()[-1])
    x0, y0, x1, y1 = _bbox(a)
    scale = 0.244                                  # 原图椅背宽约 640px → 坐姿图里约 156px
    keep = int(104 / scale)                        # 椅背顶到桌沿这一段（坐姿图里约 100px 高）
    crop = im.crop((x0, y0, x1, min(y1, y0 + keep)))
    crop = crop.resize((round(crop.size[0] * scale), round(crop.size[1] * scale)), Image.LANCZOS)
    out = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    out.alpha_composite(crop, ((CELL - crop.size[0]) // 2, CELL - crop.size[1]))
    out.save(dst, "WEBP", quality=90, method=6)


# 12 张桌子（3 排 × 4 列）在底图上的位置：(左, 右, 桌面后沿 y)。品牌布局 v3/v4 底图 1672×941 上量的。
DESKS = [(333, 549, 318), (577, 785, 318), (813, 1019, 318), (1047, 1253, 318),
         (323, 547, 481), (575, 787, 481), (815, 1023, 481), (1052, 1266, 481),
         (316, 544, 645), (572, 786, 645), (816, 1032, 645), (1061, 1279, 645)]


def foreground(bg, dst):
    """前景层：每张桌子右侧的显示器（深色）。人坐在桌子后面，显示器在桌上、离观众更近，要盖在人上面。"""
    rgb = np.asarray(bg.convert("RGB")).astype(np.int16)
    h, w = rgb.shape[:2]
    alpha = np.zeros((h, w), np.uint8)
    for x0, x1, yt in DESKS:
        bx0, bx1, by0, by1 = x0 + 128, min(w, x1 + 12), yt - 52, yt + 34      # 显示器只会出现在这一块
        reg = rgb[by0:by1, bx0:bx1]
        dark = (reg.max(axis=2) < 118) & ((reg[..., 2] - reg[..., 0]) < 34)    # 深灰黑、且不是偏蓝的地毯阴影
        alpha[by0:by1, bx0:bx1][dark] = 255
    # 填掉显示器内部的小洞、去掉零星噪点：3×3 先膨胀再腐蚀，再腐蚀一次去毛边里的地毯色
    m = Image.fromarray(alpha)
    from PIL import ImageFilter
    m = m.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(0.6))
    fg = bg.convert("RGBA")
    fg.putalpha(m)
    fg.save(dst, "WEBP", quality=92, method=6)
    return int((np.asarray(m) > 128).sum())


def main(src, out):
    os.makedirs(out, exist_ok=True)
    bg_path = os.path.join(src, "品牌布局_v3_20261004", BG_NAME)
    logo_path = os.path.join(src, "品牌布局_v3_20261004", "logo-whole-original.jpg")
    if not os.path.exists(bg_path):                # 文件夹没解开就直接从 v4 压缩包里读
        zf = zipfile.ZipFile(os.path.join(src, V4_ZIP))
        tmp = os.path.join(out, "_tmp")
        os.makedirs(tmp, exist_ok=True)
        for i in zf.infolist():
            name = os.path.basename(i.filename)
            if name in (BG_NAME, "logo-whole-original.jpg"):
                open(os.path.join(tmp, name), "wb").write(zf.read(i))
        bg_path, logo_path = os.path.join(tmp, BG_NAME), os.path.join(tmp, "logo-whole-original.jpg")
    bg = Image.open(bg_path)
    n = 0
    if not os.environ.get("ART_ONLY"):               # 只重做某几个人时，底图 / 前景 / LOGO / 椅子不动
        bg.convert("RGB").save(os.path.join(out, "bg.webp"), "WEBP", quality=93, method=6)
        n = foreground(bg, os.path.join(out, "fg.webp"))
        shutil.copyfile(logo_path, os.path.join(out, "logo.jpg"))          # 品牌 LOGO 原样拷贝
        chair(os.path.join(src, "chair-empty.png"), os.path.join(out, "chair.webp"))
    total, done = 0, []
    # 人物：原图文件夹和它下面各层子文件夹里，凡是 char-NN-seated.png + char-NN-walk.png 成对的都处理（后来追加的人物放在子文件夹里也认）
    dirs = [src] + sorted(os.path.join(r, d) for r, ds, _ in os.walk(src) for d in ds)       # 原图文件夹和它下面各层子文件夹
    only = set(int(x) for x in os.environ.get("ART_ONLY", "").split(",") if x.strip())      # ART_ONLY=10,11 只重做这几个人（整套重跑要几分钟）
    for i in range(1, 100):
        if only and i not in only:
            continue
        for d in dirs:
            seat, walk = os.path.join(d, "char-%02d-seated.png" % i), os.path.join(d, "char-%02d-walk.png" % i)
            if os.path.exists(seat) and os.path.exists(walk):
                total += sheet(seat, os.path.join(out, "c%02d-seat.webp" % i), 4, 2, "head", keep_bottom=True)
                total += sheet(walk, os.path.join(out, "c%02d-walk.webp" % i), 6, 3, "body", pad_bottom=6)
                done.append(i)
                break
        else:
            if i in STANDIN and os.path.exists(os.path.join(src, STANDIN[i])):       # 动作图集没到、只有定妆照：先上替身
                total += standin(os.path.join(src, STANDIN[i]), os.path.join(out, "c%02d-seat.webp" % i), os.path.join(out, "c%02d-walk.webp" % i))
                done.append("%d(替身)" % i)
    shutil.rmtree(os.path.join(out, "_tmp"), ignore_errors=True)
    size = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out))
    print("底图 %s，前景像素 %d，人物 %s 共 %d 张图集 %.1f MB，输出文件夹共 %.1f MB" % (bg.size, n, done, len(done) * 2, total / 1e6, size / 1e6))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
