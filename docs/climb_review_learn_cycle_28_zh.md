# _28 A0 爬塔、复盘、学习记录

日期：2026-07-07  
目标：A0 终局胜利；本轮未达成；A4 不启用。

## 本轮结论

`cycle_20260707_ironclad_a0_win_28` 完成 3 局真实 live MCP A0 爬塔，3 局均为 `clean_trainable/completed_clean`，没有 infra-blocked。最终没有 A0 终局胜利，最好进度为第 3 局 Act2 F27。

Act1 boss gate 已通过：3/3 到达并击败 Act1 Boss，2/2 pristine clear 达标，系统建议 `promote_to_next_validation_batch`。但全局目标仍是 A0 终局胜利，不能标记完成。主要失败点从 Act1 过关转移到 Act2 路线风险、连续战斗资源缓冲和关键回合战斗规划。

## 三局摘要

- 第 1 局 `20260707_133005_ironclad_a0`：The Guardian 已清，Act2 F24 死于 Book of Stabbing，归因 `combat_planning`。
- 第 2 局 `20260707_134631_ironclad_a0`：The Guardian 已清，Act2 F21 死于 Shelled Parasite/Fungi，归因 `route_risk`。
- 第 3 局 `20260707_140626_ironclad_a0`：Slime Boss 已清，Act2 F27 死于 Chosen + Cultist，归因 `route_risk`，有一次 MCP null 后 synthetic terminal，但 terminal recovery 成功，仍为 `usable_with_recoveries`。

## 启发式/搜索影响

本轮 live 执行仍由既有 policy、route risk、rest/shop/potion policy、direct lethal 和 one-turn combat search 主导。启发式没有被废弃，它是当前稳定跑完真实 MCP 局的底座；但不再继续把主要投入放在新增硬编码例外上。

典型影响：

- 三局 Act1 Boss 都能通过，说明 Act1 路线、基础战斗和奖励选择启发式仍有效。
- 第 1 局 Book of Stabbing 前的战斗搜索在单回合上多次寻找防御或斩杀线，但没有解决“Coffee Dripper + 强制精英 + 无补给”的长期资源问题。
- 第 2 局 F2 选择商店路线而避开强制精英，说明路线风险启发式已经能避免一部分早期坏路线；但 Act2 低血量时仍没有足够避战空间。
- 第 3 局 F19 路线选择暴露问题：`M` 选项分数更高但后续有强制精英风险，而 `?` 选项更接近休息/商店。当前启发式被即时分数诱导，低估了 Act2 后续资源崩盘。
- 第 3 局 F25 Coffee Dripper 下 12/56 HP 仍执行 smith，日志理由类似“HP is safe”，这是营火/遗物解释错误，不是模型学习结果。

## 学习/模型影响

当前模型仍处于 assist/shadow 阶段，`runtime_authority=false`。模型没有直接接管路线、战斗出牌、药水、商店或营火。

本轮训练状态：

- card model：`trained`，26 examples，生成 cycle scratch `card_value_model.json`。
- learned memory：`updated`，3 局 completed runs 均已应用。
- shadow models：`skipped`，原因是 `source_quality=pristine` 下 accepted rows=0；虽然 training manifest 有 464 行 shadow feature rows，但本轮没有训练出可接管的 shadow route/potion/deck/combat-search 模型。
- combat value model：`skipped`，原因是 `no_pristine_combat_value_examples`。
- live 中的 model authority 为 `assist`，实际作用范围仍主要在 card reward tiebreaker 或复盘元数据，不是执行层接管。

因此，本轮所有死亡都不能归因于“神经网络模型接管失败”。更准确的说法是：学习系统已经积累了卡牌奖励、记忆和外部先验产物，但执行层仍由旧策略和搜索控制。

## 外部数据影响

本轮显式传入外部数据：

- manifest：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`
- source_id：`mat1g3r_200_rotating_ironclad`
- source category：`external_run_history`
- external prior：trained，889 examples，151 deltas，`prior_scale=0.20`
- external blend：trained，`external_prior_blend_scale=0.25`，`external_prior_max_contribution=0.75`
- 权限：`runtime_authority=false`，`runtime_default_enabled=false`，`does_not_control_live_mcp=true`

外部数据进入了训练阶段的 scratch external prior/blend，不控制本轮 live MCP。禁止用途仍包括 `clean_trainable`、`pristine`、`gate`、`learned_memory`、`runtime_authority`。这意味着外部数据可以帮助模型形成低权重卡牌先验，但不能把 A20 rotating sample 当成 A0 live 策略真理。

## 执行完整性

3 局都可训练且无 infra-blocked。Act1 boss gate 中记录：

- validation grade：3 局均 `usable_with_recoveries`
- recovered action runs：2
- action recovery total：4，unrecovered=0
- terminal synthetic：1，failed=0
- failure attribution：`combat_planning=1`，`route_risk=2`

第 3 局有 `synthetic_terminal`、`synthetic_after_mcp_null`、`synthetic_terminal_state`，但终局恢复成功，分类仍是 clean trainable。它可以用于复盘和 memory/card 学习，但进入高洁净度 shadow/combat value 训练时仍要遵守 `source_quality=pristine` 限制。

`max-step=3000` 没有阻止胜利。本轮三局都在死亡前结束，不是被 step 上限截断。

## 静态知识与数据缺口

本轮静态知识 gap 报告显示：

- `static_knowledge_gaps logs=3 missing=1735 cards=986/5 relics=749/3`
- 高频缺口包括 Bite/噬咬、Double Tap/双发，以及部分 relic 命名对齐。

这说明日志实体与 `data/static_knowledge/` 的英文 ID、中文名、别名仍未完全对齐。它不会直接解释本轮死亡，但会影响 shadow 特征的 unknown static 统计和后续模型输入质量。下一轮前应优先补别名或重新审计 gap，避免把可识别实体误当未知。

## 对后续影响

下一轮继续 A0，不切 A4。重点不是继续新增单点启发式，而是把 `_28` 暴露的问题转成可训练/可验证的模型目标：

1. Act2 route-risk veto：低血量、无药水、Coffee Dripper、未来强制精英、已有安全路线时，要让模型/评分学会否决高即时分但高尾部风险路线。
2. Coffee Dripper 营火解释：不能把“有营火”误当成“可恢复”，smith 判断必须显式考虑不能休息。
3. 战斗价值模型：优先积累 pristine combat value examples，再训练神经网络或价值函数，不要把 usable-with-recoveries 样本直接升格为 pristine。
4. 外部 prior：继续只做低权重 card prior/blend，暂不接管 route/combat/potion。
5. 模型接管：下一步可从 route-risk shadow/veto 和 card reward assist 开始扩大，仍不应直接让模型全权点击 live MCP。
