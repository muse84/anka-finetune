#!/usr/bin/env python3
"""
build_anka_v25_enhance.py —— v25 数据增强（2026-10-04）

做三件事（只动 sharegpt.jsonl 这一个"当前源"，train/val/test 由 split_v17.py 重建）：
  1. 修复 sharegpt 中"邮件·无声片段"条目（train 行 818 的源）：
     原 assistant 是分析员第一人称随记（角色错位）→ 替换为 _v25_draft/fix_818.jsonl 的新条目
     （随记淡化后移入 user 轮，assistant 为安卡希雅口吻回应）。
  2. 拆分 3.5-5 场景的 1006 字拼接独白条目（train 行 2010 的源）→ 替换为 3 条独立长叙样本。
  3. 并入 _v25_draft/ 的 100 条新样本：
     boundary 纠缠多轮 ×20 / 对抗 user ×20 / 长回复 ×30 / 真实多轮 ×30。

设计决策（审计记录）：
  - 新样本 system 一律沿用主库真实 mem 卡（8 种之一），不做三形态重分配——
    避免重跑 corpus 链抹掉新样本、避免扰动旧样本形态（单变量原则）。
    配比偏差量级 <1%（identity 新 10 条全 mem，设计桶 80% 内），可接受。
  - fix_2010 的 3 条 source=3.5-5，split_v17 按 source 整块切 val/test，
    落在哪个 split 由既有逻辑决定，脚本不做干预。
  - 幂等保护：检测到已并入过（存在 boundary_v25_纠缠）时拒绝重复执行。
"""
from __future__ import annotations
import json, io, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "A_数据集"
DRAFT = ROOT / "_v25_draft"
SH = ROOT / "安卡希雅_训练_sharegpt.jsonl"

def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]

def save(p: Path, rows: list[dict]) -> None:
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def asst(d: dict) -> str:
    return next(m["value"] for m in d["conversations"] if m["from"] == "assistant")

def main() -> None:
    rows = load(SH)
    n0 = len(rows)
    print(f"[0] sharegpt 当前 {n0} 条")

    # 幂等保护
    if any(d.get("source") == "boundary_v25_纠缠" for d in rows):
        sys.exit("!! 检测到 v25 样本已并入（boundary_v25_纠缠 已存在），拒绝重复执行。")

    # ---- 1. 修复 邮件·无声片段（角色错位）----
    fix818 = load(DRAFT / "fix_818.jsonl")
    assert len(fix818) == 1, "fix_818 应恰好 1 条"
    hit = [i for i, d in enumerate(rows)
           if d.get("source") == "邮件·无声片段"
           and any(m["from"] == "assistant" and m["value"].startswith("我与安卡希雅的亲密接触")
                   for m in d["conversations"])]
    assert len(hit) == 1, f"邮件·无声片段 错位条目应恰好 1 条，实际 {len(hit)}"
    old = rows[hit[0]]
    rows[hit[0]] = fix818[0]
    print(f"[1] 已替换 邮件·无声片段 错位条目（原 assistant {len(asst(old))} 字 分析员随记 → 安卡口吻回应）")

    # ---- 2. 拆分 3.5-5 的 1006 字拼接独白 ----
    fix2010 = load(DRAFT / "fix_2010.jsonl")
    assert len(fix2010) == 3, "fix_2010 应恰好 3 条"
    hit2 = [i for i, d in enumerate(rows)
            if d.get("source") == "3.5-5"
            and max(len(m["value"]) for m in d["conversations"] if m["from"] == "assistant") > 900]
    assert len(hit2) == 1, f"3.5-5 超长独白应恰好 1 条，实际 {len(hit2)}"
    old2 = rows[hit2[0]]
    rows[hit2[0]:hit2[0] + 1] = fix2010
    print(f"[2] 已拆分 3.5-5 独白条目（{len(asst(old2))} 字 → 3 条 {min(len(asst(x)) for x in fix2010)}~{max(len(asst(x)) for x in fix2010)} 字）")

    # ---- 3. 并入 100 条新样本 ----
    added = 0
    for name, expect in [("boundary_escalation.jsonl", 20), ("adversarial.jsonl", 20),
                         ("long_replies.jsonl", 30), ("multi_turn.jsonl", 30)]:
        batch = load(DRAFT / name)
        assert len(batch) == expect, f"{name} 应 {expect} 条，实际 {len(batch)}"
        rows += batch
        added += len(batch)
    print(f"[3] 已并入新样本 {added} 条")

    # ---- 4. 自检 ----
    from collections import Counter
    kc = Counter(d.get("kind") for d in rows)
    assert len(rows) == n0 + 0 + 2 + 100, f"总数异常: {n0} → {len(rows)}（期望 +102）"
    old_asst = set()
    for d in rows[:n0]:
        for m in d["conversations"]:
            if m["from"] == "assistant":
                old_asst.add(m["value"].strip())
    dup = 0
    for d in rows[n0 + 3:]:
        for m in d["conversations"]:
            if m["from"] == "assistant" and m["value"].strip() in old_asst:
                dup += 1
    assert dup == 0, f"新样本 assistant 与旧库重复 {dup} 条"
    save(SH, rows)
    print(f"[4] 自检通过：{n0} → {len(rows)} 条（+103），新样本与旧库零重复")
    print(f"    kind 分布: {dict(kc.most_common())}")
    print("完成。下一步：python split_v17.py")

if __name__ == "__main__":
    main()
