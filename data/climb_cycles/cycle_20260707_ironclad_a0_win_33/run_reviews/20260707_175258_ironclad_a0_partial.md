# 局部复盘：20260707_175258_ironclad_a0

结论：本局不是完整训练局。日志推进到 F4 Looter 战斗，持有 `Dexterity Potion`，但没有生成 manifest，也没有进入 campaign/gate，因此只能作为局部诊断材料，不能进入 clean_trainable、A0 gate、learned memory 或模型 promotion 证据。

## 关键进程

- F0 Neow 选择从牌组移除一张牌，删除 `Strike_R`。
- F1 早期拿到 `Thunderclap`。
- F3 奖励页拿到 `Dexterity Potion`，后续开卡牌奖励并拿到 `Reckless Charge`。
- 最后可见进度约为 F4 Looter 战斗 T4，HP 67/80。

## 启发式与搜索影响

- 删除 `Strike_R`、拿药水、开卡牌奖励和选卡都来自既有启发式/奖励评分。
- 前几层战斗主要由 one-turn search 处理，日志多次记录 `loss -> projected_loss`、`kills`、`attacks_removed`。
- 本局没有足够证据判断后续路线或 Boss 资源规划。

## 学习与模型影响

- route-risk assist 继续作为路线评分项出现，属于 assist，不是 live MCP 直接控制。
- combat value 仍是 shadow。
- 虽然本局拿到了 `Dexterity Potion`，但没有看到 potion tempo assist 触发，也没有完整 `potion_tempo` 训练闭环。

## 外部 prior 影响

- 外部 prior 没有控制本局执行。它只应进入本轮独立外部 card prior 候选训练记录。

## 执行完整性

- 本局没有 manifest，是本轮最大的执行层/落盘缺口。
- 后续需要改进长局 heartbeat 或 partial manifest 策略，避免已经产生的局部样本在统计上不可见。

## 后续影响

- 这局说明“拿到药水后的后续决策”开始出现，但还没有被完整记录为 potion tempo 样本。
- 下一轮应优先保证完整落盘，再扩大 potion assist 权限。
