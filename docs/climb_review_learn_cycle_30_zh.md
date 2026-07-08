# 第 30 轮爬塔-复盘-学习记录

时间：2026-07-07  
目标：A0 终局胜利，角色 IRONCLAD  
结论：本轮没有 A0 终局胜利；Act1 Boss 门槛通过，下一阶段应把训练焦点推进到 Act2 生存、Boss/精英战斗规划和药水使用。

## 本轮状态

- 训练/爬塔不是后台自动进行；本轮由主会话显式启动、监控、复盘和补丁修正。
- 实盘 3 局：`20260707_150112_ironclad_a0`、`20260707_151331_ironclad_a0`、`20260707_152959_ironclad_a0`。
- 胜利：0/3。
- Act1 Boss 到达：3/3。
- Act1 Boss 通过：2/2 门槛达成，gate 输出为 `promote_to_next_validation_batch`。
- 验证等级：三局均为 `usable_with_recoveries`，原因包含 synthetic terminal recovery；没有 action recovery failure。
- 执行层：仍由现有 policy/search 控制 live MCP；模型处于 `assist` 权限，不直接点击 live MCP。

## 单局结果

- 第 1 局：到达 Guardian，死于 Act1 Boss。失败归因偏向 `potion_planning`；Boss 前药水规划和 Boss 内长线资源消耗没有做好。
- 第 2 局：击败 Guardian，死于 Act2 F23 Book of Stabbing。失败归因偏向 `combat_planning`；说明 Act1 已能通过，但 Act2 精英战斗的回合规划和承伤控制不足。
- 第 3 局：击败 Hexaghost，死于 Act2 F21 Spheric Guardian。失败归因偏向 `route_risk`；Act2 路线风险、恢复资源和战斗压力的组合评估仍不稳。

## 启发式与搜索影响

- one-turn combat search 仍是战斗执行的主要实时来源，尤其是出牌顺序、斩杀判断、短期格挡和伤害交换。
- 休息、商店、事件、奖励选择仍大量依赖原有启发式和规则打分；本轮没有继续扩写新的大块启发式策略。
- 启发式的作用仍然存在：它是执行层地基，也是模型尚未接管前的安全边界。
- 启发式的风险也仍然存在：一回合搜索看不到长线 Boss 资源、Act2 精英累计压力、药水保留价值和路线后续恢复窗口。

## 学习与模型影响

- `_29` 训练出的 route-risk 模型已被提升为默认模型，并在本轮 live 决策中以 `assist` 权限参与路线评分。
- 本轮路线日志中出现了 `model risk` 调整，说明路线风险模型已经真实影响 route evaluation，而不只是离线观察。
- 本轮摘要元数据已修正为 `route_risk_assist_and_card_reward_tiebreaker`，防止误写成只影响卡牌奖励。
- route-risk assist 带来的直接变化：相较 `_29` 的 Act1 Boss 到达 0/3，本轮达到 3/3，并清掉 2 个 Act1 Boss；这不是终局胜利，但说明路线风险学习信号开始改善早期路径。
- 需要警惕：该 route-risk 模型仍只有少量本地样本，出现过接近上限的风险惩罚；它适合 assist，不适合完全 pilot。
- 本轮 shadow 模型训练因 `source_quality=pristine` 严格门槛而跳过，原因是三局均为 `usable_with_recoveries`。
- 卡牌模型继续训练，`examples=15`。
- learned memory 更新，`applied=3`。
- combat value 模型跳过，`examples=0`；神经网络/价值模型还没有获得足够干净的可用标签。

## 外部数据影响

- 本轮接入外部数据集 `mat1g3r_200_rotating_ironclad`。
- 外部 prior 训练：`examples=889`。
- 外部 blend 训练：`deltas=151`。
- 外部数据当前只作为 card prior/blend 的候选信号，`runtime_authority=false`，`does_not_control_live_mcp=true`。
- 外部数据没有直接控制爬塔、没有直接点击 live MCP、没有直接替代本地 A0 实盘经验。
- 下一步使用原则：外部数据可帮助冷启动卡牌价值判断，但必须经过本地 A0 日志、复盘和 promotion gate 才能进入更高权限。

## 执行完整性

- 三局均存在 synthetic terminal recovery，但 recovery 成功，没有 unrecovered action。
- 本轮的两个 Act1 Boss clear 具有 prefix pristine clear 证据。
- 这意味着本轮可以用于复盘、卡牌学习和阶段 gate 判断；但默认 pristine shadow 训练仍不能吃这些样本。
- 如果要让更多样本进入 shadow 模型，必须显式放宽 source-quality，或先修复 terminal recovery 让日志成为 pristine。

## 静态知识修复

- 本轮后扫描到遗物知识缺口：`Black Blood`、`Letter Opener`、`Paper Frog` alias。
- 已补入 `data/static_knowledge/relics.json`。
- 已补 `Letter Opener` 的 `skills_per_trigger` 和 `aoe_damage` 特征抽取。
- 重新扫描第 30 轮日志后：`static_knowledge_gaps missing=0`。

## 对后续的影响

- 下一轮不应回到“多写启发式”的路线；应继续 A0 实盘、训练、复盘、promotion gate。
- Act1 Boss 门槛已通过，下一阶段重点是 Act2：路线风险、恢复资源、Book of Stabbing、Spheric Guardian、药水保存与释放时机。
- 模型接管范围可以逐步扩大，但优先从路线风险 assist 和卡牌 prior/blend 开始；战斗价值模型需要更干净的标签后再接管。
- max-step 不会让终局胜利永远无法到达，只要阈值高于正常通关所需步数；本轮 `--max-steps 3000` 不是失败主因。
