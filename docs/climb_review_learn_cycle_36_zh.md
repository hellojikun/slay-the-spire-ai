# 第 36 轮爬塔-复盘-学习记录

时间：2026-07-07
目标：Ironclad A0 终局胜利
结论：本轮没有取得 A0 终局胜利，但爬塔深度明显提升。三局均为 `clean_trainable/completed_clean`，Act1 Boss 3/3 清掉，第三局击败 Collector 并进入 Act3，最终死于 F37。当前瓶颈从 Act1 生存推进到 Act2 Boss/Act3 多怪战斗规划与 MCP 执行稳定性。

## 本轮状态

- 运行范围：Ironclad A0，3 次 live MCP 爬塔，`max-steps=3000`。
- 胜利：0/3。
- Act1 Boss：reached=3/3，cleared=3/3，pristine_cleared=1/2；gate 仍为 `NEEDS_WORK -> collect_pristine_act1_boss_clears`，原因是 2 局有 `recovered_action_race` 前缀污染。
- 失败归因：`route_risk` 1 局，`combat_planning` 2 局。
- 数据质量：3 局均为 `usable_with_recoveries`，可进入 usable lane 训练，但不能作为 pristine 证据。
- A0 目标：未完成，不能标记 goal complete。

## 单局摘要

- `20260707_203911_ironclad_a0`：F31 Act2 死亡，0/56 HP。Act1 击败 Hexaghost，Boss 后进入 Act2，但 F20 Bite 事件降低最大生命到 56，后续低血路线锁死。F31 战斗里搜索给出保命线后，后续启发式仍打出 Berserk/Whirlwind 等贪动作，最终死亡。主要归因 `route_risk`，近因是低血路线与战斗保命执行。
- `20260707_210655_ironclad_a0`：F33 Act2 Champ 死亡，0/90 HP。Act1 击败 Slime Boss，选 Coffee Dripper 后 F25/F29/F32 无法休息，只能 smith；Champ T10 面对 48 incoming 时无足够防御。主要归因 `combat_planning`，Coffee Dripper 的恢复窗口代价需要进入路线/遗物复盘。
- `20260707_214033_ironclad_a0`：F37 Act3 死亡，0/80 HP。Act1 以 11 HP 击败 Slime Boss，靠 Black Blood 回满；Act2 以 1 HP 极限击败 Collector 并进入 Act3，最终在 F37 多怪战斗中死亡。主要归因 `combat_planning`。这是目前最有价值的 Act2 Boss + Act3 样本。

## MCP 性能问题

本轮 MCP 不是断连型问题。跑后健康探针为 `healthy`，`list_tools=210ms`、`get_available_commands=684ms`、`get_screen_state=633ms`、`get_game_state_minimal=298ms`，协议、命令、状态读取都可用。

真正的问题是动作调用长尾和执行面 stale：

- 第 1 局：439 次 action，平均 1051ms，p95=1189ms，最大 11332ms，慢动作 >=2500ms 共 4 次。
- 第 2 局：536 次 action，平均 1109ms，p95=1322ms，最大 11056ms，慢动作 >=2500ms 共 4 次。
- 第 3 局：500 次 action，平均 1362ms，p95=1669ms，最大 11550ms，慢动作 >=2500ms 共 20 次。
- 第 3 局慢动作集中在 `play_card`、`end_turn`、`use_potion`，多次接近 11 秒，像 MCP/game action 调用卡到超时或内部等待边界，而不是持续 CPU 计算慢。
- 可恢复 action race 合计 6 次，未恢复 0 次。典型包括 `proceed` 不可用但 `choose` 可用、`choose` 不可用但 `proceed` 可用、`play_card` 不可用但 `end_turn/use_potion` 可用，以及一次 `Index 3 out of bounds in command "play 4 3"`。
- 第 1 局和第 3 局终局后出现 `synthetic_after_mcp_null`，terminal recovery succeeded，说明 GAME_OVER 后状态读取会返回 null 或不稳定；这不阻止 usable 训练，但会污染 pristine gate。

影响：MCP 性能问题不是本轮死亡主因，但它降低了 pristine 率，并让 Act1 Boss gate 继续卡在“还差 1 个 pristine clear”。下一轮优先做执行稳定性，而不是新增策略启发式。

## 启发式与搜索影响

- live 出牌仍主要由现有 policy、one-turn search、direct lethal、药水应急规则、路线评分和执行保护驱动。
- 第 1 局 F31 的失败不是模型直接误控，而是 one-turn search 后续启发式覆盖了保命意图：安全线倾向防御，后续却继续打风险动作。
- 第 2 局 Coffee Dripper 后的营火选择、低血不能 rest，是遗物约束与路线恢复窗口的问题，不是神经网络行为。
- 第 3 局 Collector 与 F37 Act3 多怪战斗中，search 能找到一些极限保命线，但在高 incoming、多敌和召唤物压力下仍缺少长线资源规划。

这些行为可用于学习，但不能因为单局失败直接新增大量启发式例外。下一步应先做标签审计、combat value 校准和执行层稳定。

## 学习与模型影响

- `model_authority=assist`：route-risk assist 参与路线评分，card reward/potion tempo 可作为 assist；它们不直接控制 MCP 点击。
- `runtime_authority=false`：本轮没有模型直接接管 `play_card`、路线点击、药水点击或商店购买。
- Cycle36 原始 live 训练里，旧代码仍让 combat value 因非 pristine 而 skipped。
- 已完成代码升级和离线重放：`cycle_20260707_ironclad_a0_win_36_usable_retrain` 使用 `source_quality=usable`，并让 combat value MLP 走 `quality_policy=weighted`。
- 重放训练结果：shadow rows=863；route-risk=92 examples；potion-tempo=542 examples；pre-boss deck-quality=5 examples；combat-search=224 accepted / 226 labels；card model=29 examples / 47 deltas；learned memory applied=3。
- 神经网络 combat value：trained，226 examples，weighted_examples=79.1，train=181，validation=45，device=CUDA，`runtime_authority=false`。它现在用于 shadow/value 校准，仍不能直接控制战斗出牌。

## 外部数据影响

- 外部数据源：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 数据集：`mat1g3r_200_rotating_ironclad`，50 个外部 Ironclad A20 run，889 条 card reward prior rows。
- 本轮用途：训练 scratch external card prior 和 external prior blend；external prior examples=889，blend deltas=151。
- 边界：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。外部数据不进入 A0 gate、pristine、clean_trainable、learned memory，也不能解释本地 live 输赢。

## 后续影响

- 继续 A0，不切 A4。
- 下一轮应优先解决 MCP 执行稳定性：减少 11 秒 action 长尾、降低 stale command surface、减少 terminal `synthetic_after_mcp_null`，目标是拿到至少 1 个额外 pristine Act1 Boss clear。
- 模型成长路线：继续使用 usable lane 聚合训练统计模型；combat value 可以 weighted shadow 训练，但不直接 pilot；重点审计 Act2 Boss/Act3 多怪战斗的标签质量。
- 战术学习重点：低血保命线、Coffee Dripper 路线代价、Collector 召唤物与 execute 窗口、F37 多怪高压下的防御/输出取舍、终局状态读取恢复。

## 追加复盘：feature-clean retrain

Cycle36 复盘后又完成一次数据质量修复与离线重训：`cycle_20260707_ironclad_a0_win_36_feature_clean_retrain`。这次改动不属于启发式策略投入，也不是模型直接接管执行层，而是修复 combat-search 训练样本中的假未知遗物特征。

具体问题：live MCP 的 `state.relics` 可能只有本地显示名或乱码，而 `state.relic_items` 保留真实英文 id。旧 combat-search label 生成只读 `state.relics`，导致静态知识库已经补齐后，训练审计仍出现 `combat_search:relic_unknown_count=63`。修复后 combat-search 与 route/potion/pre-boss 一样使用 `_state_relic_items(state)`，优先使用可识别 id。

验证结果：Cycle36 日志静态知识缺口为 `missing=0`；修复后 shadow feature audit 为 `categories_with_issues=0`、`unknown_static_features={}`，combat-search 226 行无未知静态特征；66 个相关测试通过。重训结果仍是 `source_quality=usable`、`model_authority=assist`、`runtime_authority=false`，外部 prior 仍只做低权重 card prior/blend，不控制 live MCP。

对后续影响：下一轮 live 训练前的数据入口更干净，模型不会继续把显示名乱码学成“未知遗物风险”。如果后续复盘再看到 `feature_unknown`，优先检查 snapshot 字段和静态知识覆盖，再考虑策略问题。
