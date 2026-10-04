# -*- coding: utf-8 -*-
"""v0.7 数据集校验：结构 / 残留 / 去重 / 划分一致性 / 小类重复采样 / RM 恒约线覆盖"""
import json, os, re, collections, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# 修订（2026-10-02）：原 BASE 写死桌面那份 2026-09-29 冻结快照，
# 导致本脚本一直在校验**过期数据**（表现为「对话场景数 76」而非 v1.1 的 174，
# 「system 唯一值 1」而非三形态）。现改为相对脚本定位 finetune\A_数据集。
BASE = str(Path(__file__).resolve().parent.parent.parent / 'A_数据集')
SNAP = os.path.join(BASE, '历史快照_618条', '安卡希雅_训练_sharegpt_bak_v06.jsonl')


def load(p):
    return [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]


def sig(r):
    return json.dumps([m['value'] for m in r['conversations']], ensure_ascii=False)


def sig_nosys(r):
    """忽略 system 的签名。

    为什么泄漏检查必须用它：v09 会给 train 样本重分配 system 三形态，
    而 val/test 恒为全卡 —— 用含 system 的签名比对，两边**永远不可能相等**，
    真混进 train 的 val 内容也会被判成「无交集」。这是个会让检查完全失效的坑，
    v1.7 起统一改用这个口径（与 split_v17.py 保持一致）。
    """
    return json.dumps([m['value'] for m in r['conversations'] if m['from'] != 'system'],
                      ensure_ascii=False)


# v1.7 的 boost 策略（与 split_v17.py 的 BOOST_SMALL 保持一致）。
# 旧版这里硬写「应仅 identity / chat」，v1.7 起 care/base_id 的 boost 是设计意图，
# 继续按旧期望会报假警报。
BOOST_SMALL = {'identity': 2, 'chat': 2, 'care': 3, 'base_id': 4,
               'peer_bound': 3}  # v1.8，须与 split_v17.py 同步


full = load(os.path.join(BASE, '安卡希雅_训练_sharegpt.jsonl'))
tr = load(os.path.join(BASE, '安卡希雅_训练_train.jsonl'))
va = load(os.path.join(BASE, '安卡希雅_训练_val.jsonl'))
te = load(os.path.join(BASE, '安卡希雅_训练_test.jsonl'))
old = load(SNAP)

print('=' * 70)
print('v0.7 数据集校验')
print('=' * 70)
print('总条数          : %d   (v0.6 = %d)' % (len(full), len(old)))
# boost 条数 = train 里多出来的重复份额。
# 旧口径 len(tr)+len(va)+len(te)-len(full) 会**多算 4 条**：
# 那 4 条 val/test 在历史清洗（playername→分析员）时被改过原文，
# 在 sharegpt 里匹配不上，既不在 train 也不计入 val/test 的可用成员，
# 于是被算成了 boost。（真实值 393，旧口径给出 397。）
uniq_tr = len({sig(r) for r in tr})
n_boost = len(tr) - uniq_tr
n_boost_old = len(tr) + len(va) + len(te) - len(full)
print('train/val/test  : %d / %d / %d' % (len(tr), len(va), len(te)))
print('train 去重      : %d 唯一 + %d 重复 = %d' % (uniq_tr, n_boost, len(tr)))
if n_boost != n_boost_old:
    print('  注：旧口径算出 %d，多出的 %d 条来自上述匹配不上的 val/test'
          % (n_boost_old, n_boost_old - n_boost))
# 期望值要用**去重后的 train**计数：boost 只对池子里的样本做，
# 落在 val/test 里的同类样本不参与（这正好解释了上面那 4 条的差额）。
cnt_uniq = collections.Counter()
_seen = set()
for _r in tr:
    _s = sig(_r)
    if _s in _seen:
        continue
    _seen.add(_s)
    cnt_uniq[_r.get('kind')] += 1
exp_boost = sum((BOOST_SMALL[k] - 1) * cnt_uniq[k] for k in BOOST_SMALL)
print('boost 期望      : %d  实际 %d  %s'
      % (exp_boost, n_boost, '✓' if exp_boost == n_boost else '✗ 对不上'))
c_tr = collections.Counter(sig(r) for r in tr)
dup_kinds = sorted({r.get('kind') for r in tr if c_tr[sig(r)] > 1})
print('被重复的类别    : %s' % dup_kinds)
print('  期望          : %s  %s'
      % (sorted(BOOST_SMALL),
         '✓' if set(dup_kinds) <= set(BOOST_SMALL) else '✗ 有非预期类别在被 boost'))

# 划分无交集 —— 必须用忽略 system 的签名，否则这项检查形同虚设（见 sig_nosys 注释）
s_tr, s_va, s_te = ({sig_nosys(r) for r in tr}, {sig_nosys(r) for r in va},
                    {sig_nosys(r) for r in te})
print('划分两两无交集  : %s   (train∩val %d / train∩test %d / val∩test %d)'
      % (not (s_tr & s_va) and not (s_tr & s_te) and not (s_va & s_te),
         len(s_tr & s_va), len(s_tr & s_te), len(s_va & s_te)))
allc = {sig_nosys(r) for r in full}
union = {sig_nosys(r) for r in tr} | s_va | s_te
cov = len(union & allc) / len(allc) * 100
print('覆盖 sharegpt   : %.1f%%  (%d/%d)' % (cov, len(union & allc), len(allc)))
missing = allc - union
if missing:
    print('  未覆盖样本 %d 条（应为 0；非 0 说明有样本既没进 train 也没进 val/test）' % len(missing))
miss_keep = [k for k in (allc - union)
             if any(sig_nosys(r) == k for r in va + te)]
print('  其中属于 val/test 改过原文的旧条目: %d 条（v1.6 起的历史状况，非错误）'
      % len(miss_keep))
c_va = collections.Counter(sig(r) for r in va)
c_te = collections.Counter(sig(r) for r in te)
print('val/test 内有重复: %s / %s  (应全为 False)'
      % (any(v > 1 for v in c_va.values()), any(v > 1 for v in c_te.values())))

# 剧情场景级泄漏检查（dialogue 按整场景划分）
sc = lambda rs: {r.get('source') for r in rs if r.get('kind') == 'dialogue'}
g_tr, g_va, g_te = sc(tr), sc(va), sc(te)
print('剧情场景数      : train %d / val %d / test %d' % (len(g_tr), len(g_va), len(g_te)))
print('场景泄漏        : val∩train %d / test∩train %d / val∩test %d  (应全为 0)'
      % (len(g_va & g_tr), len(g_te & g_tr), len(g_va & g_te)))

# 重复
c = collections.Counter(sig(r) for r in full)
dup = [k for k, v in c.items() if v > 1]
print('重复样本        : %d' % len(dup))

# 类别 / 来源
print('类别分布        :', dict(collections.Counter(r.get('kind') for r in full)))
kc = collections.Counter(r.get('kind') for r in full)
print('类别占比        :', {k: '%.1f%%' % (100.0 * v / len(full)) for k, v in kc.items()})
src = collections.Counter(r.get('source') for r in full)
print('来源 top15      :', src.most_common(15))

# 结构
seq = collections.Counter()
for r in full:
    seq[''.join({'system': 'S', 'user': 'U', 'assistant': 'A'}.get(m['from'], '?')
                for m in r['conversations'])] += 1
print('结构分布        :', dict(seq))

# 合法性
bad_role = [r for r in full if any(m['from'] not in ('system', 'user', 'assistant') for m in r['conversations'])]
empty = [r for r in full if any(not (m['value'] or '').strip() for m in r['conversations'])]
no_asst = [r for r in full if not any(m['from'] == 'assistant' for m in r['conversations'])]
no_user = [r for r in full if not any(m['from'] == 'user' for m in r['conversations'])]
print('非法角色        : %d' % len(bad_role))
print('空值消息        : %d' % len(empty))
print('无 assistant 轮 : %d' % len(no_asst))
print('无 user 轮      : %d' % len(no_user))

# 残留
def has(r, kw):
    return any(kw in (m['value'] or '') for m in r['conversations'])
print('playername 残留 : %d' % sum(1 for r in full if has(r, 'playername') or has(r, 'Playername')))
print('"\\N" 残留       : %d' % sum(1 for r in full if has(r, '\\N')))
print('第三人称残留    : %d' % sum(1 for r in full
                                   if any(('眼前的男人' in m['value'] or '名为我的少女' in m['value']
                                           or '那个男人' in m['value'])
                                          for m in r['conversations'] if m['from'] == 'assistant')))
print('system 唯一值   : %d' % len({r['conversations'][0]['value'] for r in full}))

# 长度
al = [len(m['value']) for r in full for m in r['conversations'] if m['from'] == 'assistant']
ul = [len(m['value']) for r in full for m in r['conversations'] if m['from'] == 'user']
print('assistant 段    : max %d / 中位 %d / 均值 %d' % (max(al), sorted(al)[len(al) // 2], sum(al) // len(al)))
print('user 段         : max %d / 中位 %d / 均值 %d' % (max(ul), sorted(ul)[len(ul) // 2], sum(ul) // len(ul)))
print('user 段 >400 字 : %d' % sum(1 for x in ul if x > 400))

# RM 恒约线覆盖
print('-' * 70)
print('恒约线（RM）覆盖：')
rm_old = sum(1 for r in old if re.search(r'RM[1-5]', r.get('source') or ''))
rm_new = sum(1 for r in full if re.search(r'RM[1-5]', r.get('source') or ''))
print('  v0.6 中 RM1-5 样本 : %d' % rm_old)
print('  v0.7 中 RM1-5 样本 : %d' % rm_new)
for i in range(1, 6):
    n = sum(1 for r in full if re.search(r'RM%d\b' % i, r.get('source') or ''))
    o = sum(1 for r in old if re.search(r'RM%d\b' % i, r.get('source') or ''))
    print('    RM%d : %3d  (v0.6 %d)' % (i, n, o))

# 全语料来源覆盖
print('-' * 70)
print('对话样本来源场景数: %d' % len({r.get('source') for r in full if r.get('kind') == 'dialogue'}))
print('校验完成。')