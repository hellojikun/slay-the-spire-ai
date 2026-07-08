# cycle_20260707_ironclad_a0_win_29 中文复盘

更新时间：2026-07-07

## 当前结论

本轮正在训练和爬塔，但没有取得 A0 终局胜利。`cycle_20260707_ironclad_a0_win_29` 完成 3 局 Ironclad A0 live MCP 爬塔，3 局均为 `clean_trainable`，无 infra blocker；Act1 Boss reached 为 0/3，gate 判定 `NEEDS_WORK`，主失败归因为 `route_risk:3`。

本轮不是启发式胜利问题，而是路线风险学习问题：战斗层 one-turn search 多次止损，但路线层仍把 Act1 早期高收益精英/连续战斗路径估得过高，导致 F6/F7 前死亡。

## 单局摘要

- `20260707_143541_ironclad_a0`：死于 Act1 F7。关键路线证据在 step 100/F6，选择 `M`，`route_score=2.0`，`readiness_penalty=-40.0`，风险旗标包含 `critical_hp`、`act1_low_buffer_no_recovery`、`hallway_no_immediate_tempo_potion`、`hallway_lacks_premium_block`。
- `20260707_144156_ironclad_a0`：死于 Act1 F7。关键路线证据在 step 124/F6，选择 `M`，`route_score=12.0`，`readiness_penalty=-30.0`，低血量且仍被迫进入战斗链。
- `20260707_144849_ironclad_a0`：死于 Act1 F6 Sentries。关键路线证据在 step 75/F5，选择 `E`，`forced_elite_within_3=true`，`readiness_penalty=-82.0`，风险旗标包含 `elite_not_ready`、`elite_low_hp_no_tempo_potion`、`forced_elite_aoe_gap`、`forced_elite_weak_gap`、`forced_elite_no_tempo_potion`。

每局自动复盘已写入：

- `data/climb_cycles/cycle_20260707_ironclad_a0_win_29/run_reviews/20260707_143541_ironclad_a0.md`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_29/run_reviews/20260707_144156_ironclad_a0.md`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_29/run_reviews/20260707_144849_ironclad_a0.md`

## 启发式/搜索影响

启发式仍负责 live 控制的主体：路线基础分、营火/商店/奖励选择、药水规则、direct lethal 和 one-turn combat search。`_29` 中大量战斗动作来自 one-turn search，说明战斗搜索在延缓死亡，但没有解决“进入错误风险带”的问题。

受启发式影响最大的行为是：F5/F6 早期路线仍允许负向 readiness penalty 后的战斗/精英路径胜出。尤其第三局 F5 选择精英时已经有 `readiness_penalty=-82.0`，但最终 `score=10.0` 仍足够执行。这说明路线基础收益和局部路径结构仍能压过风险证据。

## 学习/模型影响

本轮运行时 `model_authority=assist`，但 cycle summary 仍显示旧边界为 `card_reward_tiebreaker_only`；也就是说 `_29` 实际爬塔时，route-risk 模型还没有真正接管路线评分。训练结束后产出：

- shadow models：`trained`，accepted rows=74。
- route-risk model：`trained`，examples=13，目标为 `died_within_3_floors`。
- card model：`trained`，examples=4。
- combat search model：`trained`。
- combat value 神经网络：`trained`，examples=45，CUDA 训练，`runtime_authority=false`。
- learned memory：updated，applied_completed_runs=3。

本次升级已经把 route-risk 模型接入路线 assist：`policy_route` 会对每个地图候选构造训练同形特征，并在 `model_authority=assist/pilot` 时写入 `model_adjustment` 和 `model_assist`。assist 模式只给封顶惩罚，不直接点击 MCP；旧 `models/route_risk_model.json` 已备份，`_29` 的 pristine route-risk 模型已提升为默认模型。

## 外部数据影响

外部数据源仍为：

- `data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`
- source_id：`mat1g3r_200_rotating_ironclad`
- external prior examples=889
- external blend deltas=151

外部数据当前只作为 card prior/blend 的离线输入，`runtime_authority=false`，`does_not_control_live_mcp=true`。它不能作为本地 A0 胜负证据，不能进入 pristine gate，也不能直接控制 live 路线、战斗或药水。后续可继续用于卡牌奖励先验，但路线模型接管应优先依赖本地 clean/pristine MCP 数据。

## 执行完整性

3 局均为 `clean_trainable`。第一局存在一次 `synthetic_after_mcp_null` 终局恢复，恢复成功，验证等级为 `usable_with_recoveries`；第二、三局为 `pristine`。没有 action recovery 或 failed action。训练默认采用 pristine source-quality，因此第一局不会污染 pristine shadow 训练，但仍可用于复盘和 learned memory 的完成局统计。

`max-step=3000` 没有截断终局；本轮死亡均在 max-step 前发生，所以不是步数上限导致无法胜利。

## 已完成改进

- 修复 Coffee Dripper/No Rest 遗物下，路线层把营火误当成可恢复点的问题。
- 修复 Coffee Dripper 低血量只能 smith 时的中文/元数据解释。
- 修补 `_28` 暴露的静态知识别名缺口：Bite、Double Tap、Gremlin Horn、White Beast Statue、HornCleat；重新审计后 `_28` static gaps 为 0。
- 接入 route-risk 模型到路线 assist，保留 `shadow` 无控制、`assist` 封顶惩罚、`pilot` 更高上限的边界。
- 备份并提升 `_29` route-risk 模型到 `models/route_risk_model.json`，下一轮 A0 live 会真实使用学习信号。

## 下一轮影响

下一轮目标仍是 A0 终局胜利，不切 A4。立即验收目标不是“模型分数更漂亮”，而是至少先恢复 Act1 Boss reached。下一轮应重点观察：

- F5/F6 低准备度时，route-risk assist 是否能压下 immediate/forced elite。
- `route_evaluation.options[*].model_assist` 中风险分是否与实际死亡风险一致。
- 如果模型误伤安全路线，先调低 assist 阈值/上限或回到 shadow，而不是继续堆新启发式。
- 如果 Act1 Boss reached 恢复，再让模型逐步扩大到 card reward blend 和 combat value shadow 校准。
