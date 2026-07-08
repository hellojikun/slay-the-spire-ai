# 第 35 轮爬塔-复盘-学习记录

时间：2026-07-07  
目标：Ironclad A0 终局胜利  
结论：本轮没有取得 A0 终局胜利。3 局均为 `clean_trainable/completed_clean`，但只有 1 局击败 Act1 Boss；Act1 Boss gate 回到 `NEEDS_WORK -> improve_act1_boss_combat`。

## 本轮状态

- 运行范围：Ironclad A0，3 次 live MCP 爬塔，`max-steps=3000`。
- 胜利：0/3。
- Act1 Boss：reached=3/3，cleared=1/2，pristine_cleared=1/2。
- 失败归因：`deck_quality` 1 局，`route_risk` 1 局，`potion_planning` 1 局。
- 执行完整性：3 局均可训练；2 局带 synthetic terminal recovery，2 局有 recovered action race，均未出现 unrecovered action。
- 静态知识：初扫 `missing=392`，补齐后 `static_knowledge_gaps logs=3 missing=0`。

## 单局摘要

- `20260707_192510_ironclad_a0`：死于 F16 Guardian。Neow 变换 Strike 后拿到 Shockwave，又拿第二张 Shockwave；路线早期打 Lagavulin 和 Gremlin Nob，输出成长不足。Perfected Strike 在 Strike 被变换/删除后价值偏低，最终 Guardian T17 死亡。主要问题是 deck quality 与 Boss 前输出不足。
- `20260707_194217_ironclad_a0`：击败 Slime Boss，Act2 死于 F29 Centurion。前期有 Bash+、Pommel Strike+、Carnage+、Cleave+、Bludgeon+，Act1 明显优于上一轮；Boss relic 选择 Coffee Dripper 后不能休息，Act2 后段低 HP 被路线锁进战斗。主要问题是 Coffee Dripper 下的路线/恢复风险。
- `20260707_200349_ironclad_a0`：死于 F16 Hexaghost。遗物有 Oddly Smooth Stone、Mummified Hand，但卡组长期只有 Bash+、Clothesline+ 与大量基础牌；Boss 入场 HP 76/80 但无药水，输出不足，T14 被击杀。主要问题是 Boss 前药水/输出准备不足。

## 启发式与搜索影响

- live 出牌仍主要由现有 policy、one-turn search、direct lethal、药水应急规则和路线评分执行。
- Guardian 局中 Perfected Strike 的选择、Hexaghost 局中 Boss 前卡组过薄，主要是 reward/route/deck heuristic 与局部价值估计共同导致，不是神经网络直接接管。
- 药水使用主要来自规则触发，例如多敌人和高 incoming 下的 Block/Steroid/Skill 类药水；本轮没有证据表明 potion tempo 模型直接控制 live MCP 点击。
- `map risk -129.0` 不是符号 bug。路线分数按 `base + lookahead_adjustment + model_adjustment` 相加，负值是惩罚；F29 那次只剩一个可选 M 节点，是被前序路线锁线后被迫进入危险战斗。

## 学习与模型影响

- Cycle35 原始 live 训练使用 `source_quality=pristine`，由于 3 局 shadow rows 都是 `usable_with_recoveries`，shadow 统计模型被跳过：`shadow=skipped rows=0`。
- Card model 正常训练：10 examples，26 deltas；learned memory 读取 3 个 completed runs 并更新。
- 外部 prior 与 external blend 正常训练，但均为 scratch/candidate，`runtime_authority=false`，`does_not_control_live_mcp=true`。
- 已追加离线验证周期 `cycle_20260707_ironclad_a0_win_35_usable_training`：重放 Cycle35 三局，使用 `source_quality=usable` 后 shadow 统计模型成功训练，accepted_rows=415。
- usable 训练产物：route-risk 58 examples，potion-tempo 199 examples，pre-boss deck-quality 3 examples，combat-search 155 examples。combat value MLP 仍按设计跳过，因为它要求 pristine source quality。

## 外部数据影响

- 外部数据源：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 数据集：`mat1g3r_200_rotating_ironclad`，来自外部 Ironclad run history，当前导入规模为 50 个 Ironclad A20 run，889 条 card reward prior rows。
- 本轮用途：训练 scratch external card prior 与 external prior blend；不进入 A0 gate、`pristine`、`clean_trainable`、learned memory 或 runtime authority。
- 外部 prior 不能解释本轮 live 输赢，也不能替代本地 A0 终局胜利证据。它只用于低权重卡牌奖励先验和离线对照。

## 数据质量升级

- 本轮补充卡牌别名：`Strike_R/打击`、`Defend_R/防御`、`Bash/痛击`、`Clothesline/金刚臂`、`Juggernaut/势不可当`、`Flex/活动肌肉`、`Reckless Charge/无谋冲锋`、`Burn/灼伤`、`Dazed/晕眩`、`Slimed/黏液`、`Decay/腐朽`、`Parasite` 的历史乱码别名等。
- 本轮补充遗物：`Oddly Smooth Stone/意外光滑的石头`、`Mummified Hand/干瘪之手`。
- 影响：后续 shadow feature 中 `card_unknown_count`、`relic_unknown_count` 不应再被这些实体污染；这是特征清洗，不是新增启发式策略。

## 后续影响

- A0 终局目标未完成，不得标记 goal complete。
- 下一轮 live cycle 应使用 `--source-quality usable` 作为 shadow 统计模型训练 lane，同时继续把 gate/pristine 统计保持独立。
- 不建议修 route-risk 符号；应关注更早避免会导致 Act2 低 HP 被锁线的分支，以及模型小样本 cap 下区分度不足的问题。
- 模型逐步接管的当前边界：route/card/potion assist 与 combat-search candidate 可以继续扩大离线验证；神经网络 combat value 仍不直接控制 `play_card`。
