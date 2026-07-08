# 单局复盘协议

项目进入“真实爬塔 -> 复盘 -> 学习 -> 下一批爬塔”阶段后，每一盘完成并经过离线训练/学习后，都必须写一篇中文 per-run 复盘文档，避免只在对话里记住经验。

## 必写位置

- 默认目录：`data/climb_cycles/<cycle_name>/run_reviews/`
- 文件名：`<run_stem>.md`
- 如果是补跑复盘，仍然写到对应 cycle 的 `run_reviews/` 目录，并在文档顶部注明 source log、manifest、training summary。

## 必写结构

每篇复盘必须包含：

1. 本局概况
   - 角色、进阶、最终楼层、胜负、分类：`clean_trainable / diagnostic_excluded / infra_blocked`
   - validation grade、action recovery、terminal recovery、Act 1 boss reach/clear/pristine 状态。

2. 启发式与搜索的影响
   - 哪些行为主要来自现有启发式策略、执行保护或 one-turn search。
   - 典型来源包括：路线选择、休息/升级、商店购买、药水触发、战斗 play_card 顺序、direct lethal、search sequence commitment。
   - 必须引用具体楼层/step，例如 `F16 Slime Boss split 后，search 多次选择防御或斩杀线`。

3. 学习与模型的影响
   - 哪些行为受到 learned memory、card value model、shadow advice 或训练后模型影响。
   - 必须写明 `model_authority` 档位：`shadow / assist / pilot / none`。
   - 如果 `runtime_authority=true`，必须列出具体 step、surface、启发式首选、模型信号首选、最终选择。
   - 如果当前 run 没有可用模型，必须明确写 `model_missing / shadow_only / no runtime model authority`，不能假装模型控制了行为。
   - 卡牌选择需要说明是否可能受到 `StrategyMemory.card_score()` 中 learned/model delta 影响。

4. 训练影响
   - 本局哪些数据会进入 `learn`、`train_card_model`、shadow model，哪些会被排除。
   - 明确 source-quality：`pristine`、`usable_with_recoveries`、`diagnostic`。
   - 如果有 missed lethal、missed direct kill、action race、max_steps 等污染，必须写出训练影响。
   - 如果训练或模型先验使用了外部数据，必须写明来源、provenance 桶、权重和是否进入 runtime；外部数据不能写成本局 MCP 学习结果。

5. 对后续爬塔的影响
   - 这盘对下一批爬塔的影响：继续收数据、提高 max_steps、修执行层、修 combat planning、调整训练源质量，或考虑扩大/收紧模型执行权限。
   - 不允许因为单局异常直接新增启发式例外；需要先通过 manifest/gate/diagnosis 证明是稳定问题。

## 模型接管执行层边界

- 默认 `model_authority=shadow`：模型只记录、训练、产生 shadow advice，不实际改动作。
- `model_authority=assist`：当前只允许卡牌奖励在“启发式分差很小、学习/模型信号明显更强且候选卡模型信号为正”时打破僵局；“负得更少”不能算作学习结果接管。
- `model_authority=pilot`：作为后续扩大权限的入口；当前实现范围仍与 `assist` 一样，先不接管路线、药水、战斗和商店。
- `search.combat_value_shadow`：one-turn search 可以记录神经网络 combat value 的 shadow 预测，包括 `predicted_value` 与 `runtime_authority=false`。这属于“模型观察/复盘证据”，不属于战斗执行层接管。
- 每一次 runtime 接管都必须进入 JSONL 的 `decision.metadata.model_authority`，并在本局复盘中出现。
- 如果模型接管导致明显坏选择，下一轮先降回 `shadow` 或收紧阈值，再继续收集 A0 终局证据。

## 外部数据说明

网上数据集和日志源记录在 `docs/external_run_datasets.md`，具体使用决策记录在 `docs/external_data_usage_plan.md`。外部 run history 可以做 shadow 先验和离线消融，但不能替代本项目真实 MCP 爬塔日志，也不能计入 A0 终局胜利或 pristine gate。

## _18 后补充要求

- 每篇复盘都必须显式区分 `decision.metadata.model_authority` 的卡牌奖励元数据和 `search.combat_value_shadow` 的战斗搜索元数据；前者可能影响卡牌奖励 assist，后者目前只观察搜索线价值。
- 如果本轮训练 summary 中 neural combat value 是 `skipped`，即使 live JSONL 有 shadow prediction，也必须写成“模型观察已记录、未用于本轮训练、未接管执行层”。
- `usable_with_recoveries` 局可以进入 learned memory/card model 和中文复盘，但默认不得进入 `source_quality=pristine` 的 shadow/combat value 训练。
