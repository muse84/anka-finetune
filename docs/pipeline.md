# 数据管线与训练流程

## 1. 总体流程

```
自建语料源（对话整理 / 人物考据 / 关系QA / 合成样本）
        │
        ▼
sharegpt.jsonl（全量源，1825 条，统一 ShareGPT 结构）
        │  build_anka_v25_enhance.py（幂等构建 + 精确断言）
        ▼
split_v17.py（train / val / test 切分，固定种子 20261004）
        │   train 2339 行（含身份类过采样 boost 572）
        │   val / test 各 31 行（同源同分布，按 source 块切分防泄漏）
        ▼
verify_v6.py（完整性校验：行数断言 / kind 分布 / 重复检测 / 撞压测题检测）
        │
        ▼
to_swift_messages.py（ShareGPT → ms-swift messages 格式）
        │
        ▼
云端一键打包（run_all.sh：配置区集中 + 包指纹 + 脚本自证 + pack_check.py 全部通过才允许上传）
```

## 2. 数据分类（kind）与设计意图

| kind | 条数(train) | 设计意图 |
|---|---|---|
| dialogue | 1136 | 日常对话风格锚定 |
| chat | 342 | 自由聊天话题漂移能力 |
| knowledge | 157 | 角色世界观知识 |
| care | 195 | 关怀行为表达（show don't tell） |
| identity | 152 | 身份认知：对自身虚拟属性的平稳诚实认知 |
| peer_bound | 126 | 队友/他人边界 + 拒绝类样本 |
| base_id | 148 | 身份锚定（少量过采样 ×4） |
| boundary | 30 | 越界纠缠的真实拒绝场景（先短拒、再稳住、不破防） |
| origin | 40 | 剧情长叙（低配比，防背诵） |
| mail | 13 | 异步消息形态 |

## 3. 质量控制措施

1. **三轮审查**：表层污染审查 → 6 维度内容质量审查（教学信号密度/风格一致性/user 轮质量/多样性/长度风险/轮次结构）→ 社区成熟实践对标（LIMA/LimaRP、类脑社区经验、ArliAI RPMax）。
2. **n-gram 口癖检测**：LLM 辅助生成的样本必须过重复句式检测（如 6-gram 命中阈值），防止教出模型新口癖。
3. **撞压测题检测**：构建脚本自动读压测集 user 轮做字面比对，训练集与压测题逐字撞题即拒绝写入（防"背答案"假评估）。
4. **幂等构建**：所有增量构建脚本带幂等保护（检测已并入标记即拒绝重复执行）+ 净增条数精确断言。
5. **种子固定**：切分种子固定，同源数据多次重建保证 train/val/test 稳定可复现。

## 4. 训练配置摘要

```text
model: Qwen3 9B (open weights)
tuner: lora   rank 32   alpha 64   dropout 0.05   all-linear
epochs: 4     batch: 8   grad_accum: 2 (effective 16)
max_length: 3072   attn: sdpa   group_by_length: true
add_non_thinking_prefix: true   loss_scale: ignore_empty_think
gradient_checkpointing: true    （显存峰值 ~41GB）
```

## 5. 评估方法

- 训练中：val 集损失监控；
- 训练后：固定压测题集（身份认知 / 情感边界 / 通用能力分卷）人工评估，与微调前底模和上一版 checkpoint 对比；
- 已验证结果：上一版（train 2142）身份压测从底模 1/20 提升至 15/20，回复长度与边界行为显著改善。
