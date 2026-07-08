# _26 爬塔、复盘、学习阶段记录

日期：2026-07-07  
目标：A0 终局胜利，暂不进入 A4。

## 本轮结果

`cycle_20260707_ironclad_a0_win_26` 完成 3 局真实 live MCP 爬塔，3 局均为 `clean_trainable/completed_clean`，无 diagnostic、无 infra-blocked。A0 终局胜利尚未达成。

- 第 1 局：F7 死亡，失败归因 `route_risk`。
- 第 2 局：F16 The Guardian 死亡，失败归因 `deck_quality`，synthetic terminal recovery 成功。
- 第 3 局：F16 The Guardian 死亡，失败归因 `combat_planning`，`validation_grade=pristine`。

Act1 boss gate 仍为 `NEEDS_WORK`：`reached=2/3`、`cleared=0/2`、`pristine_cleared=0/2`。下一步建议为 `improve_route_and_early_act1_survival`，同时继续保留 A0 终局胜利作为总目标。

## 训练结果

- shadow models：`trained rows=80`
- card model：`trained examples=15`
- neural combat value shadow：`trained examples=41`，`runtime_authority=false`
- learned memory：`applied=3`
- external prior：`trained examples=889`
- external blend：`trained deltas=151`

这说明项目已经接入“本地 MCP 日志训练 + 外部 A20 低权重 prior 候选 + 神经网络 combat value shadow”。但目前模型仍未接管 live `play_card`，只在训练、shadow、有限 card reward assist 边界中工作。

## 行为来源边界

- 启发式/搜索：当前 live route、shop、rest、potion、combat action 仍主要来自旧 policy、route risk、potion tempo、direct lethal 和 one-turn search。
- 本地学习：card model、shadow models、combat value shadow、learned memory 只从本地 clean/pristine 或允许质量的 MCP 日志产生训练信号。
- 外部数据：`mat1g3r_200_rotating_ironclad` 只参与 `external_prior` 与 `card_value_model_external_prior_blend` 候选训练，`runtime_authority=false`，不控制 live MCP，不进入 A0 gate、pristine 判定或 learned memory。

## 审计结果

`run_status` 刷新后静态知识缺口为 `missing=0`。本轮最初发现 `Orichalcum/奥利哈钢` 缺口 154 次，已补入 `data/static_knowledge/relics.json`，这是特征覆盖修复，不是新策略规则。

combat replay audit：`files=1 excluded=1 replayed=1 matches_label=0 matches_actual=1 other=0`。唯一被排除的标签来自 Guardian T7，当前策略重放匹配实际动作，因此保持为审计证据，不喂给训练。

## 对后续的影响

1. 不因 `_26` 失败继续堆硬编码启发式；优先让 route/future-loss、deck-quality 和 combat-value shadow 学习失败模式。
2. Guardian 长战是核心瓶颈：当前搜索会优先活过当回合，但输出窗口、Wound 稀释、Mode Shift 节奏和未来 5-10 回合损失评估不足。
3. 外部数据继续作为低权重参考，不能替代本地 A0 MCP 证据。
4. 模型可以逐步接管执行层，但下一步仍应先扩大 shadow/assist 的可解释证据，尤其是 combat value 对 Boss 长战的预测质量。

## 旁路 agent 审计结论

子 agent 对外部数据链路做了只读审计，结论如下：

- 外部数据已通过 `slay_ai/import_external_runs.py`、`slay_ai/train_external_priors.py` 和 `slay_ai/climb_cycle.py --external-prior-input` 接入训练阶段。
- 训练产物包括 `external_manifest.json`、`card_reward_priors.jsonl`、`card_prior_model.json`、`external_prior_training_summary.json`，以及 cycle 内的 `card_value_model_external_prior_blend.json` 候选。
- live runtime 没有加载 external prior/blend；`runner.py` 没有 external prior 参数，combat value 仍只写 shadow metadata，`policy_card_reward.py` 的模型权限仍限于 card reward tie-breaker。
- 阻止模型进一步接管执行层的最小缺口是：缺少可对齐的逐动作轨迹数据、缺少 `external_action_trace` importer、缺少离线对比到小流量 assist 的 promotion/eval 闭环、runtime authority 仍太窄。

因此下一步不是直接让外部 prior 接管，而是先在本地 clean/pristine 日志上比较 `card_value_model.json` 与 `card_value_model_external_prior_blend.json` 的 card reward 决策质量；战斗执行层接管要等动作轨迹和回滚审计流程齐备。
