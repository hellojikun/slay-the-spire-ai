# 第 33 轮爬塔-复盘-学习记录

时间：2026-07-07  
目标：A0 终局胜利，角色 IRONCLAD  
结论：本轮没有取得 A0 终局胜利。正式落盘的完整样本只有第 1 局，Act1 Boss reached=1/1，cleared=0/1，gate 仍为 `NEEDS_WORK`。第 2 局推进到 F4，但因 live 进程中断没有生成 manifest，只能作为局部诊断日志，不能进入 A0 gate、clean evidence 或 promotion 证据。

## 本轮状态

- 运行命令使用 `model_authority=assist`、A0、Ironclad、`attempts-per-target=3`、`max-steps=3000`。
- 模型接管范围已扩展为 `route_risk_card_reward_and_potion_tempo_assist`。
- 正式 manifest：`20260707_174128_ironclad_a0.manifest.json`。
- 正式胜利：0/1。
- 正式 Act1 Boss 到达：1/1。
- 正式 Act1 Boss 通过：0/1。
- gate：`NEEDS_WORK`，缺口为 reached 还差 2，cleared 还差 2，pristine_cleared 还差 2。
- 第 2 局 `20260707_175258_ironclad_a0.jsonl`：推进到 F4，持有 `Dexterity Potion`，但没有 manifest，不作为正式训练闭环结论。

## 单局结果

- 第 1 局：到达 Slime Boss，Boss 入场 F16 step 164，HP 86/90，0 药水。牌组为 `Bash+ / True Grit+ / Wild Strike / Clothesline+ / Shockwave+` 加基础牌，遗物为 `Burning Blood / Dream Catcher / Pear`。分裂后进入多敌压力，T11 时 15/90 HP、incoming 32、block 9，随后死亡；终局由 MCP null 后的 synthetic terminal recovery 落盘。
- 第 2 局：Neow 选择删一张 `Strike_R`，早期拿到 `Thunderclap`、`Reckless Charge` 和 `Dexterity Potion`，推进到 F4 Looter 战斗。该局没有终局、没有 manifest、没有进入 campaign，只能用于执行层诊断和后续采样提醒。

## 启发式与搜索影响

- live 执行层仍主要由 policy、one-turn search、direct lethal、奖励/事件/营火启发式和 MCP action wrapper 控制。
- 第 1 局 Boss 前路线、休息、smith、卡牌奖励选择都来自启发式与路线评分；`Dream Catcher` 触发的卡牌奖励也仍由现有 reward policy 决定。
- Boss 战出牌多次来自 one-turn search，例如根据 `loss / damage / kills / attacks_removed` 选择防御或攻击序列。
- 失败核心不是“模型错误用药”，而是 Boss 入场 0 药水、缺少稳定 AoE/爆发，Slime Boss 分裂后多敌伤害压垮了单回合搜索。
- 第 2 局早期战斗也主要由 one-turn search 推进，拿药水和开卡牌奖励属于奖励页启发式。

## 学习与模型影响

- route-risk assist 已真实影响路线评分，日志中多次出现 `model risk -12.0`；这是学习模型对路线评分的 assist 影响，但 `runtime_authority=false`，模型没有直接点击 MCP。
- card reward 仍是启发式与记忆/模型分数混合，不是模型独裁。
- combat value 神经网络仍是 shadow：它只写入 `combat_value_shadow` 预测，`runtime_authority=false`。
- 本轮新增了 potion tempo model assist 的 live wiring：在高压、模型样本数足够、搜索不能解决危险、且有可用药水时，模型可以给药水使用动作提供 assist。
- 本轮 potion tempo assist 没有实际触发：第 1 局没有药水，`potion_tempo.jsonl` 为空；第 2 局虽然拿到 `Dexterity Potion`，但没有完整落盘，也没有看到 `use_potion` 行为。

## 训练产物

- 本地 usable 候选训练已完成，输入为第 1 局完整 live_shadow：
  - route-risk：15 examples，2 positive，44 weights。
  - potion-tempo：0 examples，0 weights。
  - pre-boss deck-quality：1 example，0 weights。
  - combat-search：35 examples，4 card priors，21 context buckets。
- 这些候选模型位于 `data/climb_cycles/cycle_20260707_ironclad_a0_win_33/training/usable_shadow_candidate_models/`，没有提升到默认 runtime 模型。
- 由于第 1 局终局带 `synthetic_after_mcp_null`，本地候选训练使用 `source_quality=usable`，不是 pristine promotion。

## 外部数据影响

- 外部数据源：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 来源：`MaT1g3R/Slay-the-Spire-data` 的 200 rotating Ironclad 样本，本地导入 50 个 Ironclad A20 run，card prior rows=889。
- 本轮重新训练了独立外部 card prior 候选：examples=889，deltas=151，`prior_scale=0.20`。
- 输出：`data/climb_cycles/cycle_20260707_ironclad_a0_win_33/training/external_prior_candidate_model/card_prior_model.json`。
- 权限：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。
- 禁止用途：`clean_trainable`、`pristine`、A0 gate、`learned_memory`、`runtime_authority`。外部 A20 数据只作为低权重先验和离线对照，不能写成本地 A0 MCP 胜负证据。

## 执行完整性

- 第 1 局终局时 MCP state 出现 null，系统通过 terminal recovery 合成 `GAME_OVER`，validation grade 为 `usable_with_recoveries`。
- 第 1 局无 failed actions，但有 synthetic terminal 标记，因此不能作为 pristine terminal。
- 第 2 局没有 manifest，说明 live cycle 在长局/中断场景下仍有落盘缺口；不能把 stdout 或半截 JSONL 当作完整训练闭环。
- `max-step=3000` 本轮不是失败原因；真正问题是 MCP null、终局恢复和部分日志未落盘。

## 静态知识与数据质量

- 初始 Cycle 33 gap：`static_knowledge_gaps logs=2 missing=291 relics=291/2`。
- 缺失实体集中在 `Dream Catcher` 和 `Pear`。
- 已补入 `data/static_knowledge/relics.json`：`Dream Catcher/捕梦网`、`Pear/梨子`。
- 修复后重新扫描：`static_knowledge_gaps logs=2 missing=0`。
- 注意：本轮已经生成的 live_shadow rows 不会自动改写；静态知识修复会从下一轮 shadow feature 生成开始生效。

## 对后续的影响

- 目标仍是 A0 终局胜利，不切 A4，不标记完成。
- 当前应继续真实爬塔积累本地 MCP 数据；重点是完整 manifest，而不是继续堆启发式例外。
- potion tempo assist 已接入，但需要真实药水正负样本才能成长；下一轮应重点观察拿药、保药、Boss 前资源和关键战斗用药。
- Slime Boss 需要更好的分裂前后规划样本：当前搜索偏单回合减伤，缺少分裂阈值、多敌后续风险和药水资源规划。
- 外部 prior 可以继续作为 card reward 的低权重候选训练输入，但不能提升为 live controller。
