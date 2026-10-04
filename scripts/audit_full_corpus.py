# -*- coding: utf-8 -*-
"""全量语料核查：D:\SeasunCBJQos\SnowbreakText 里所有「安卡希雅系角色」台词，
是否都已进入 07_安卡希雅_主角对话对.txt（对话样本的唯一权威源）。

只查"安卡说了什么"，因为只有安卡的台词才是 SFT 的 assistant 目标。
若存在 07 里没有的安卡台词 → 即"没找到的内容"。
"""
import os
import re
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8")

CORPUS = r"D:\SeasunCBJQos\SnowbreakText"
SRC07 = r"C:\Users\曹力文\Desktop\归档_安卡希雅训练资料_20260929\B_语料源\本地语料\07_安卡希雅_主角对话对.txt"

ANKA_SPK = {"安卡希雅", "安卡希雅病号服", "安卡希雅辉夜", "时之重奏"}


def norm(t):
    t = t.strip()
    for k in ("playername", "Playername", "玩家", "指挥员"):
        t = t.replace(k, "分析员")
    return t


# 07 正文集合
have = set()
for ln in open(SRC07, encoding="utf-8"):
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", ln.strip())
    if m:
        have.add(norm(m.group(2)))
print("07 台词条目（去重后）: %d" % len(have))
print("=" * 78)

# 扫描范围：girl / main / trailer / msg / 01_raw_scene 下的原始场景
SCAN_DIRS = ["girl", "main", "trailer", "msg", "01_raw_scene"]

cand = []                      # (file, speaker, text)
files = 0
for d in SCAN_DIRS:
    root = os.path.join(CORPUS, d)
    if not os.path.isdir(root):
        continue
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if not fn.lower().endswith(".txt"):
                continue
            files += 1
            spk = None
            for ln in open(os.path.join(dp, fn), encoding="utf-8", errors="ignore"):
                s = ln.strip()
                if not s:
                    continue
                m = re.match(r"^【(.+?)】$", s)
                if m:
                    spk = m.group(1)
                    continue
                if spk in ANKA_SPK:
                    cand.append((os.path.join(d, fn), spk, s))

print("扫描原始场景文件: %d 个 | 安卡系台词候选: %d 条" % (files, len(cand)))

miss = [(f, sp, t) for f, sp, t in cand if norm(t) not in have]
# 去重后统计
uniq_miss = collections.OrderedDict()
for f, sp, t in miss:
    uniq_miss.setdefault(norm(t), (f, sp))
print("07 中缺失的安卡台词（去重后）: %d 条" % len(uniq_miss))
print("-" * 78)
for t, (f, sp) in list(uniq_miss.items())[:40]:
    print("  MISS [%s] %s" % (sp, t[:70]))
    print("       来自 %s" % f)
if len(uniq_miss) > 40:
    print("  ... 另有 %d 条" % (len(uniq_miss) - 40))
print("=" * 78)

# 反向：按来源目录统计安卡台词量，确认哪些活动/章节被 07 收走
by_dir = collections.Counter(f.split(os.sep)[0] for f, _, _ in cand)
print("安卡台词按来源目录:", dict(by_dir))
if not uniq_miss:
    print("→ 全量语料中的安卡台词 100% 已进入 07，无遗漏。")