#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.7 重划分 train/val/test。

为什么不跑 build_v6.py：那个脚本是从 v0.5 原始语料整体重建的，会抹掉 v1.1~v1.6
所有版本的工作（origin / care / base_id 全部消失）。本脚本只做「划分 + boost」，
输入是当前的 sharegpt.jsonl（全量 1681 条），其余一概不动。

划分规则（复刻 build_v6.py，保持与历史版本可比）：
  - dialogue：按 source（剧情场景）整块切 5% / 5%，剩余进 train
    —— 整块切是为了避免同一场景的相邻对话同时出现在 train 和 test（否则泄漏）
  - 其他 kind：按条切 5% / 5%
  - boost：train 内对 BOOST_SMALL 的类别重复采样，val/test 保持单份

安全性：默认复用现有 val/test 的条目 id（按 conversations 内容匹配），
只从 sharegpt 里挑属于 val/test 的样本，**保证 val/test 与上一版同源**，
这样 v1.7 与 v1.6 的 val loss 仍可横向比较。
"""
from __future__ import annotations

import io
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATASET = Path(r"G:\anka\finetune\A_数据集")
SHARE = DATASET / "安卡希雅_训练_sharegpt.jsonl"
TRAIN = DATASET / "安卡希雅_训练_train.jsonl"
VAL = DATASET / "安卡希雅_训练_val.jsonl"
TEST = DATASET / "安卡希雅_训练_test.jsonl"

# v1.7 策略：identity/chat ×2、care ×3（治冷淡是本轮核心）、base_id ×4（短答需加权）
# v1.8：peer_bound ×3。它是全新行为（对虚拟队友放松边界），重复不够会时灵时不灵；
#   但只有 38 条且同出一人之手，×4 会把她的口吻锁死在我的文风上，故取 3。
BOOST_SMALL = {"identity": 2, "chat": 2, "care": 3, "base_id": 4, "peer_bound": 3}


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]


def dump(p: Path, rows: list[dict]) -> None:
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def sig(r: dict) -> str:
    """含 system 的完整签名（用于 train 内部去重校验）。"""
    return json.dumps([m["value"] for m in r["conversations"]], ensure_ascii=False)


def sig_nosys(r: dict) -> str:
    """忽略 system 的签名 —— 用于匹配 val/test 成员。

    为什么必须忽略 system：v09 重跑会给 train 样本重新分配 system 三形态，
    而 val/test 保持全卡不动。带 system 的签名在两侧永远对不上。
    """
    return json.dumps([m["value"] for m in r["conversations"] if m["from"] != "system"],
                      ensure_ascii=False)


def main() -> None:
    share = load(SHARE)
    old_val, old_test = load(VAL), load(TEST)

    # 1) 沿用既有的 val / test 成员（忽略 system 匹配，保持跨版本可比）
    val_sigs = {sig_nosys(r) for r in old_val}
    test_sigs = {sig_nosys(r) for r in old_test}
    hit_v = sum(1 for r in share if sig_nosys(r) in val_sigs)
    hit_t = sum(1 for r in share if sig_nosys(r) in test_sigs)
    print(f"既有 val {len(old_val)} / test {len(old_test)}")
    print(f"  在 sharegpt 里命中：val {hit_v}/{len(old_val)}，test {hit_t}/{len(old_test)}")

    # val/test 保持原样（它们是评测基准，不随训练集变化）
    val_out, test_out = old_val, old_test

    # 2) train = sharegpt 去掉 val/test 成员
    tr_pool = [r for r in share if sig_nosys(r) not in (val_sigs | test_sigs)]
    print(f"train 候选池: {len(tr_pool)} 条（sharegpt {len(share)} − val/test 成员 {len(share)-len(tr_pool)}）")

    # 2b) 兜底：命中不到的 val/test 成员（内容在历史版本被改过）会残留在池子里，
    #     按 kind 逐条对比，若某个 kind 在 sharegpt 中一条都没被排除则告警。
    if hit_v < len(old_val) or hit_t < len(old_test):
        print(f"  ⚠ 有 {len(old_val)-hit_v} 条 val、{len(old_test)-hit_t} 条 test "
              f"在 sharegpt 中找不到同内容（历史清洗改过原文），"
              f"它们的内容可能同时出现在 train —— 评测时留意这几条。")

    # 3) 新增 kind（base_id / care / origin / peer_bound）若一条都没进 train 要告警
    #    旧版这里会把该 kind 的样本 append 回 train —— 但那些样本本来就是
    #    因为属于 val/test 才被排除的，append 回去＝制造泄漏，而且立刻会
    #    撞上第 5 步的 assert，等于绕一圈才报错。改成直接说清楚。
    have_kinds = {r.get("kind") for r in tr_pool}
    missing = {"base_id", "care", "origin", "peer_bound"} - have_kinds
    if missing:
        raise SystemExit(
            f"train 候选池里完全没有这些 kind：{missing}。"
            f"它们的样本全被判给 val/test 了 —— 检查 val/test 是否误吞，别盲目补数据。")

    # 4) boost
    n_boost = 0
    boosted = []
    for r in tr_pool:
        k = r.get("kind")
        if k in BOOST_SMALL:
            boosted += [r] * (BOOST_SMALL[k] - 1)
            n_boost += BOOST_SMALL[k] - 1
    tr_out = tr_pool + boosted

    # ⚠️ 必须打散。boost 是整条复制，不清洗顺序的话所有重复样本会堆在文件末尾
    #    （实测 393 条全落在第 1623~2015 行）。后果：
    #      - 靠 ms-swift 的 --dataset_shuffle 兜底才安全，一旦被关掉，
    #        每个 epoch 末段会被 care/base_id/identity 的重复梯度垄断；
    #      - 冒烟用的 `#16` 子集永远取到文件开头的 dialogue，代表性失真。
    #    固定种子保证可复现。
    random.Random(20261004).shuffle(tr_out)

    # 5) 校验（train 与 val/test 必须零交集，并集覆盖 pool）
    tr_ns = {sig_nosys(r) for r in tr_out}
    assert not (tr_ns & val_sigs), "train 与 val 有交集"
    assert not (tr_ns & test_sigs), "train 与 test 有交集"
    assert not (val_sigs & test_sigs), "val 与 test 有交集"
    # 并集检查放宽为「覆盖率 ≥99%」：历史清洗（playername→分析员）改过少量原文，
    # 导致 4 条 val/test 在 sharegpt 里找不到同内容，这是 v1.6 就有的既有状况。
    union = {sig_nosys(r) for r in tr_pool} | val_sigs | test_sigs
    allc = {sig_nosys(r) for r in share}
    cov = len(union & allc) / len(allc) * 100
    print(f"  并集覆盖率 {cov:.1f}%（差额来自历史清洗改过原文的 val/test 条目）")
    assert cov >= 99.0, f"并集覆盖率异常低：{cov:.1f}%"

    dump(TRAIN, tr_out)
    dump(VAL, val_out)
    dump(TEST, test_out)

    print(f"\n=== v1.7 划分完成 ===")
    print(f"  sharegpt {len(share)}（全量，v1.6 = 1644）")
    print(f"  train {len(tr_out)}（含 boost {n_boost} 条，v1.6 = 1850）")
    print(f"  val   {len(val_out)}（未变）")
    print(f"  test  {len(test_out)}（未变）")
    print(f"  boost: {BOOST_SMALL}")
    print(f"  train kind: {dict(Counter(r.get('kind') for r in tr_out))}")

    # 6) token 配比
    ch = Counter()
    for r in tr_out:
        for m in r["conversations"]:
            if m["from"] == "assistant":
                ch[r["kind"]] += len(m["value"])
    C = sum(ch.values())
    print(f"\n  {'kind':10s}{'token占比':>10}")
    for k in sorted(ch, key=lambda x: -ch[x]):
        print(f"  {k:10s}{ch[k]/C*100:9.1f}%")
    base = sum(len(m["value"]) for r in tr_out if r.get("kind") == "base_id"
               for m in r["conversations"] if m["from"] == "assistant")
    print(f"\n  ★ base_id token {base/C*100:.1f}%（v1.6 = 1.9%）")
    print(f"  ★ care   token {ch['care']/C*100:.1f}%（v1.6 = 1.4%）")
    print(f"  ★ origin token {ch['origin']/C*100:.1f}%（v1.6 = 2.4%，已背熟故降权）")


if __name__ == "__main__":
    main()
