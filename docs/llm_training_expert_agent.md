# 大模型训练专家 Agent 协议

更新日期：2026-07-09。

这个 agent 的目标是把外部 Slay the Spire 数据和本地 MCP 日志转成可训练、可验证、可逐步升权的深度学习信号。它不添加硬编码爬塔策略，不直接控制 MCP，不把外部数据伪装成本地 clean/pristine 证据。

## 边界

- 允许：读取外部 run history、静态卡牌语义、runlogger 风格逐动作轨迹、本地 MCP clean/pristine 日志、shadow advice 和 gate 报告。
- 允许：生成隔离训练 rows、scratch/shadow 模型、prediction audit、live shadow disagreement、ablation 报告和 promotion readiness。
- 禁止：修改实时 policy 来加入新的固定策略规则。
- 禁止：让外部数据进入 `clean_trainable`、`pristine`、`gate`、`learned_memory` 或默认 runtime model。
- 禁止：让模型直接执行 MCP action。升权前只能输出 shadow/assist 候选证据。

## 每轮输入

1. 本地 MCP 日志：`runs/**/*.jsonl` 和 `climb_cycle` 生成的 manifest/shadow/gate/review artifacts。
2. 外部 run history：77M metrics dump、MaT1g3R、Run History Plus 等局级历史。
3. 外部逐动作轨迹：runlogger 或未来对齐到 MCP JSONL schema 的 action trace。
4. 静态语义数据：Hugging Face `slaythespire-codex`、卡牌 tags、card embeddings、relic/potion/card metadata。
5. 参考项目：win-prediction、Slay-I/FightPredictor 只用于目标设计和离线 ablation，不直接当 runtime 标签。

## 每轮样本产物

训练专家 agent 每轮优先产出这些 rows：

- `card_reward_decisions.jsonl`：奖励拿牌/跳过。重点学习“没有好牌可以跳过”，并记录 reward options、picked、floor、character、ascension、source_weight。
- `card_purge_priors.jsonl`：删牌正样本。重点学习 Strike/Defend/诅咒/低效牌的移除倾向，但只从真实 removed cards 标注正样本。
- `purge_keep_candidates.jsonl` 或弱负样本：从 reward options、master deck、未删除牌中采样 keep candidates，必须低权重并标注 weak label。
- `deck_cycle_priors.jsonl`：过牌/消耗/循环质量。重点记录 deck_size、draw_density、exhaust_count、starter_density、purge_count、skip_rate、zero_cost_count、final_floor/victory。
- `live_shadow_disagreements.jsonl`：模型 shadow 预测和当前 policy 实际选择的差异。它是 shadow -> assist 的核心证据，不可省略。
- 未来 `external_action_trace/*.jsonl`：逐动作 imitation/value rows，必须先完成 schema 对齐和动作后状态校验。

## 模型职责

当前主模型是 `train_decision_multitask_model` 的多任务 shadow MLP：

- `take_skip`：奖励是否拿牌，目标是学习跳过坏奖励。
- `purge_remove`：候选牌是否应该被删除，目标是学习删牌价值。
- `deck_cycle_quality`：牌组循环质量，目标是学习过牌、消耗、删牌、少拿牌之间的长期关系。

后续可以扩展，但必须保持同样的隔离：

- card/relic/potion semantic encoder：使用 card metadata/embedding 生成语义特征，不产生胜负标签。
- floor win/risk model：参考 win-prediction，预测楼层级胜率或损血风险。
- combat damage/value model：参考 Slay-I/FightPredictor，预测战斗损血或动作序列价值。
- action imitation model：只在 runlogger/MCP action trace 质量足够时训练，先做 shadow replay，不直接执行。

## 每轮评估

每次训练必须写出 summary，至少包含：

- provenance：source_id、source_uri、filters、resolved_files、seen_runs、accepted_runs、source_weight。
- 数据质量：external/pristine/usable/diagnostic 分类，缺字段和 schema drift 统计。
- 训练指标：每个 head 的 examples、train/validation split、loss、accuracy/precision/recall 或 MAE。
- 负样本风险：purge weak negatives 的数量、权重、来源和是否已被 live 证据确认。
- prediction audit：高置信错误、具体错误样本、skip false positive/false negative、purge 误删风险。
- live shadow evidence：在本地 MCP 日志上的 disagreement rate、high-confidence disagreement、按楼层/act/card 聚合。
- gate 影响：Act 1 boss gate、prefix-pristine clear、死亡楼层、card reward review 是否改善。

## Shadow -> Assist 条件

默认状态永远是 `shadow_only`。进入 `assist` 必须同时满足：

- 模型 artifact 明确写有 `runtime_authority=false`、`direct_mcp_control=false`、`requires_audited_promotion=true`。
- 外部 prior 只作为低权重候选，不能覆盖本地 MCP learned delta。
- 每个待升权 head 有足够 validation examples，并达到当前代码中的基础阈值。
- `take_skip` 必须有真实 skip 负例覆盖，且在 live shadow 中证明不会系统性把坏奖励拿走。
- `purge_remove` 必须有真实删牌样本和 keep 负样本确认，且不能只依赖 weak negatives。
- `deck_cycle_quality` 必须在多批本地 MCP 日志上 MAE 稳定，并能解释过牌/消耗/删牌/少拿牌的长期收益。
- 至少一批 live shadow disagreement 报告显示 assist 候选能改进复盘质量或降低已知错误，不恶化 Act 1 boss gate。
- 升权范围只能是有界 assist，例如 tie-break、低权重 prior、review suggestion；不能直接 play_card、choose、proceed。

如果任一条件缺失，readiness 必须继续输出：

```json
{
  "status": "shadow_only",
  "can_promote_to_assist": false,
  "recommended_authority": "shadow"
}
```

## 多 Agent 分工

- 信息搜集 agent：持续发现和登记外部数据源、license/provenance、schema、字段覆盖和下载状态。
- ETL agent：把外部数据转换为隔离 rows，维护 manifest、filters、source_weight、schema drift 报告。
- 训练专家 agent：训练多任务/语义/value 模型，写 prediction audit 和 promotion readiness。
- Live shadow evaluator：只读本地 MCP 日志，生成 disagreement rows 和 ablation，不控制游戏。
- 主 agent：决定是否把某个 shadow artifact 接入 `climb_cycle` 报告或作为 assist 候选，负责最终代码集成和测试。

## 接入主流水线

建议主流水线顺序：

1. `import_external_runs` 下载/导入外部 run history，生成 external manifest。
2. `train_external_structure_priors` 生成 take/skip、purge、deck-cycle rows。
3. `train_decision_multitask_model` 训练 shadow-only 多任务模型和 predictions。
4. live/offline MCP logs 上跑 shadow disagreement，验证奖励跳过、删牌、过牌循环方向是否与真实复盘一致。
5. `climb_cycle` summary 和中文复盘写入 readiness、audit、disagreement 路径。
6. 只有连续多轮 live shadow evidence 支持时，才由主 agent 显式讨论 assist 级接入。

这个流程的核心判断是：外部数据负责“让模型先学会像玩家一样理解删牌、过牌、循环和跳过坏奖励”，本地 MCP live shadow 负责“证明它在本项目环境里真的帮得上忙”。
