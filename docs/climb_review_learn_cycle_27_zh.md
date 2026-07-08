# _27 爬塔、复盘、学习阶段记录

日期：2026-07-07  
目标：A0 终局胜利，暂不使用 A4 难度。

## 本轮结果

`cycle_20260707_ironclad_a0_win_27` 完成 3 局真实 live MCP 爬塔，3 局均为 `clean_trainable/completed_clean`，无 diagnostic、无 infra-blocked，但没有 A0 终局胜利。

- 第 1 局：F20 Act2 死亡，The Guardian 已清，失败归因 `combat_planning`。
- 第 2 局：F16 Hexaghost 死亡，失败归因 `deck_quality`，有 1 次可恢复 action race。
- 第 3 局：F25 Act2 双 Cultist 死亡，Slime Boss 已清，失败归因 `combat_planning`。

Act1 boss gate 本轮通过：reached=3/3，cleared=2/2，pristine_cleared=2/2。系统建议 `promote_to_next_validation_batch`，但全局目标仍是 A0 终局胜利，不能标记完成。

## 训练与模型状态

本轮训练已运行：

- card model：trained，examples=18。
- learned memory：applied=3。
- external prior：trained，examples=889。
- external blend：trained。
- shadow models：skipped，原因是本轮 source-quality 限制下没有可接受 shadow rows。
- combat value：skipped，本轮没有重新训练可接管的战斗价值模型。

当前 live 执行层仍不是神经网络接管。模型主要在 card reward assist、shadow metadata、external prior/blend 训练中工作，`runtime_authority=false`。

## 行为来源边界

启发式/搜索影响：

- live route、rest、shop、potion、combat action 主要来自旧 policy、route risk、potion tempo、direct lethal 和 one-turn search。
- 本轮能过两个 Act1 Boss，说明旧启发式仍有价值。
- 失败集中在 Act2 combat planning，说明 one-turn search 的短期目标不足以处理连续战斗和未来血量。

学习/模型影响：

- card reward assist 已开始记录 `heuristic_with_memory_model_score`、`learned_delta`、`model_delta`。
- 第 3 局 `Spot Weakness` 与 `Bludgeon` 选择中可以看到学习信号参与评分，但没有推翻启发式主导。
- combat value shadow 有预测记录，但没有运行时动作权限。

外部数据影响：

- 外部数据集仍使用 `data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 外部数据只进入 `external_prior` 和 `external_blend`，作为低权重候选训练来源。
- 外部数据不控制 live MCP，不进入 A0 gate，不作为本地 learned memory 的权威胜负来源。

## 静态知识更新

本轮初始静态知识缺口：missing=934，包括 relics=738、potions=175、cards=21。已补入：

- relics：`Toxic Egg`/`Toxic Egg 2`、`MealTicket`、`Toy Ornithopter`、`Juzu Bracelet`、`Sacred Bark`。
- potions：`Snecko Oil`/`SneckoOil`。
- cards：`Juggernaut`，以及 `Immolate/燔祭`、`Uppercut/上勾拳` 的本轮中文 alias。

补丁后重新扫描 `runs/ai_runs_climb_cycle_ironclad_a0_win_27`，静态知识缺口已降为 `missing=0`。run_status 仍显示部分 `combat_search:relic_unknown_count`，这是本轮运行时 manifest 中的旧特征快照，不会被事后知识补丁自动重写；下一轮 live 会使用更新后的静态知识。

## 对后续的影响

1. 不继续投入新的硬编码启发式作为主方向；重点转向 Act2 数据积累、复盘和模型评估。
2. 下一轮应围绕 Act2 普通战训练：Shelled Parasite、Snake Plant、Cultist 组合、Centurion+Healer。
3. 药水策略需要学习“什么时候保留”，不能只在压力来临时全部 emergency tempo 打光。
4. 允许模型逐步接管执行层，但必须先在离线/assist 中证明能改善 Act2 行为；下一步不是直接让 external prior 控制 live。
5. `max-step=3000` 没有阻止胜利，本轮全部在死亡前结束；终局胜利不可达的问题不来自 max-step。
