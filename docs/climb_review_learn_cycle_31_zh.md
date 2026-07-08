# 第 31 轮爬塔-复盘-学习记录

时间：2026-07-07  
目标：A0 终局胜利，角色 IRONCLAD  
结论：本轮没有 A0 终局胜利。第 30 轮通过的 Act1 gate 在本轮退回 `NEEDS_WORK`，主要问题是 Act1 路线/准备度把牌组送进多敌与精英压力链，导致 Boss 前死亡或带着不足资源进 Guardian。

## 本轮状态

- 实盘 3 局：`20260707_160545_ironclad_a0`、`20260707_161645_ironclad_a0`、`20260707_162319_ironclad_a0`。
- 胜利：0/3。
- Act1 Boss 到达：1/3。
- Act1 Boss 通过：0/2。
- gate：`NEEDS_WORK -> improve_route_and_early_act1_survival`。
- 训练：默认 pristine shadow skipped；card model trained examples=2；learned memory applied=2；external prior examples=889；external blend trained。
- 执行完整性：1 次 action race 已恢复；3 个 synthetic terminal，其中 2 个是 `synthetic_after_mcp_null`，1 个是 `synthetic_after_main_menu`。这影响 pristine/gate 计数，但不是主要失败机制。

## 单局结果

- 第 1 局：F12 死于 Act1 后段多敌战。失败归因 `route_risk`。前期从 Neow 事件拿钱损失 24 HP，之后 starter-heavy 卡组进入多敌/精英前路线，战斗拖长，未到 Act1 Boss。
- 第 2 局：F6 低血进入 Nob，死亡。失败归因 `route_risk`。F4 已被多敌战打到 36/88，商店后仍被单通道送入 E，说明路线层在“已经很低分但无替代路线”时缺少更早的灾难规避。
- 第 3 局：到达 Guardian，未击败。诊断归因 `diagnostic_incomplete`，Boss 入场 65/88、无药、带 Parasite，Guardian T8 承受 32 incoming 死亡。该局说明就算到 Boss，卡组的前载/防御/药水窗口也不足。

## 启发式与搜索影响

- combat search 在大多数回合做到了局部一回合最优，没有明显 missed lethal。
- 但一回合搜索仍短视：F11/F12/F14 多敌战中多次选择当回合降损或小击杀，无法评估“战斗继续拖长会让后续路线和 Boss 入场资源崩掉”。
- 路线启发式会给危险路线打很低分，例如第 2 局 F6 进入 E 的分数已到 `-181`；问题不是分数不知道危险，而是更早没有强力避开让路线变成单通道灾难。

## 学习与模型影响

- route-risk assist 继续参与 live route evaluation，但不直接控制 live MCP。
- 本轮三篇单局复盘都已列出 route-risk assist 的 `model risk`、`authority=assist`、`direct_mcp_control=False`。
- 重要发现：默认 route-risk 模型只有 13 个 pristine examples，本轮几乎所有路线点都被打到 `risk≈0.90-0.99`、`penalty≈-30`，导致候选之间失去区分度。
- 已修正：小样本 route-risk assist 现在在 examples < 30 时使用更低上限，避免全图饱和；同时保留 `runtime_authority=false` 和 `direct_mcp_control=false`。
- 已新增路线准备度信号：Act1 后段 forced hallway chain 若缺前载、AoE、即时 tempo potion，会进入 readiness flags，并对 M/E 路径施加额外 penalty。

## 外部数据影响

- 外部数据 `mat1g3r_200_rotating_ironclad` 本轮仍只作为 card prior/blend。
- 外部 prior：trained examples=889。
- 外部 blend：trained。
- 外部数据仍为 `runtime_authority=false`，不直接控制 MCP，不作为本地 A0 胜负证据，不进入 pristine gate。

## 本轮学习产物

- 默认训练使用 `source_quality=pristine`，shadow rows=0，因此不会把本轮可恢复日志直接提升为默认 shadow 模型。
- 额外训练了 `source_quality=usable` 的 scratch 候选模型，未提升 live：
  - route-risk candidate：18 examples，49 weights。
  - potion-tempo candidate：46 examples，20 weights。
  - combat-search candidate：53 examples，3 card priors。
  - pre-boss deck-quality：0 examples。
- 这个候选模型只用于离线分析和后续 promotion 对比，不替换 `models/route_risk_model.json`。

## 静态知识修复

- 本轮扫描发现 `Parasite` 的中文/乱码别名 `瀵勭敓` 缺失。
- 已加入 `data/static_knowledge/cards.json`。
- 重新扫描第 31 轮日志：`static_knowledge_gaps logs=3 missing=0`。

## 对后续的影响

- 下一轮仍然 A0，不切 A4。
- 不能把第 30 轮 Act1 gate 通过视为稳定能力；第 31 轮证明 Act1 仍不稳。
- 下一轮重点验证：
  - route-risk assist 小样本封顶是否减少全图 -30 饱和。
  - Act1 后段 forced hallway penalty 是否让路线更早选择 R/$/?。
  - 卡牌/奖励是否更重视 Cleave/Thunderclap/Flame Barrier 这类 Act1 多敌、Boss、精英准备工具。
  - combat search 是否仍在多敌长战中拖慢。
