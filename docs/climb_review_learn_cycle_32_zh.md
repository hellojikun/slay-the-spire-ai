# 第 32 轮爬塔-复盘-学习记录

时间：2026-07-07  
目标：A0 终局胜利，角色 IRONCLAD  
结论：本轮没有取得 A0 终局胜利。正式落盘样本为 2 局，Act1 Boss reached=2/2，cleared=0/2，gate 仍为 `NEEDS_WORK`。第三局 stdout 显示已清 Act1 并推进到 F21，但因强制停止未生成 manifest，只能作为诊断观察，不能进入 clean training 或 gate 证据。

## 本轮状态

- 运行命令使用 `model_authority=assist`、A0、Ironclad、`attempts-per-target=3`、`max-steps=3000`。
- 正式 manifest：`20260707_164601_ironclad_a0`、`20260707_165954_ironclad_a0`。
- 正式胜利：0/2。
- 正式 Act1 Boss 到达：2/2。
- 正式 Act1 Boss 通过：0/2。
- gate：`NEEDS_WORK`，缺口为 reached 还差 1，cleared 还差 2，pristine_cleared 还差 2。
- 第三局 `20260707_171219_ironclad_a0`：stdout 到 F21，但无 manifest，诊断使用，不参与训练 promotion。

## 单局结果

- 第 1 局：到达 Hexaghost，Boss 入场 72 HP、0 药，未击败。失败归因 `potion_planning`。说明路线能把角色送到 Boss，但 Boss 前药水与爆发窗口不足。
- 第 2 局：到达 Guardian，Boss 入场 62 HP、1 药，未击败。失败归因 `deck_quality`。说明 Boss 前卡组质量仍不足，尤其是稳定防御/输出结构不够。
- 第 3 局：stdout 显示击败 Act1 Boss 后进入 Act2，到 F21。因为进程被强停且无 manifest，不作为清 Boss 证据；但它是有价值的方向性信号。

## 启发式与搜索影响

- live 出牌仍由现有 policy、one-turn search、direct lethal、药水启发式和 MCP action wrapper 控制。
- 第三局 Boss 战里 Whirlwind 与药水 tempo 表现突出，但这是搜索/启发式执行产生的行为，不是神经网络直接决策。
- 一回合搜索能处理局部高 incoming，但仍无法长期规划 Boss 战资源、药水保留和 Act2 多敌战。
- 本轮不应继续把主要精力投入新增启发式例外；要把这些行为转成训练样本、模型校准和复盘约束。

## 学习与模型影响

- route-risk assist 已真实影响路线评分，但权限仍是 `assist`，不是 pilot。
- 第 31 轮发现的小样本饱和问题在本轮得到验证：路线日志多次出现 `model risk -12.0`，不再是全图 `-30`。
- 正式样本的 Act1 reach 从第 31 轮的 1/3 改善为 2/2；第三局 stdout 还显示 Act1 clear。样本仍小，但小样本 cap 很可能改善了路线区分度。
- 本轮训练的 usable scratch 候选模型未提升 live：
  - route-risk：30 examples，55 weights。
  - potion-tempo：37 examples，34 weights。
  - pre-boss deck-quality：2 examples，0 weights。
  - combat-search：71 examples，5 card priors，33 context buckets。
- 当前大多数模型仍是可审计统计模型。神经网络只有 `models/combat_value_model.pt` 一类 combat value shadow；样本规模和执行风险不足以让 NN 接管 live MCP。

## 外部数据影响

- 当前落地外部数据集：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 来源：`MaT1g3R/Slay-the-Spire-data` 的 200 rotating Ironclad 样本。
- 本地可用数据：accepted runs=50，card prior rows=889，角色 Ironclad，ascension 全为 A20。
- 使用方式：低权重 card prior/blend，`external_prior_scale=0.20`，`external_prior_blend_scale=0.25`。
- 限制：外部数据不是 A0、本地 MCP、逐动作 live 轨迹；`runtime_authority=false`，不能进入 A0 gate、pristine evidence、learned memory 或 live controller。

## 执行完整性

- 两个正式 manifest 都是 `usable_with_recoveries`，并带 `synthetic_after_mcp_null`。
- 第三局长时间无输出后被强制停止；进程退出码 1，没有生成 manifest。
- `max-step=3000` 本身不会让胜利永远不可达；本轮真正问题是 MCP/流程无响应导致未落盘。
- 后续应考虑 partial manifest 或 heartbeat，避免清 Act1 的长局因为强停丢失训练价值。

## 静态知识与数据质量

- 初始 Cycle 32 gap：`missing=591 cards=409/5 potions=51/1 relics=131/1`。
- 已修复实体：`Violence/暴力`、`HandOfGreed/贪婪之手`、`Magnetism/磁力`、`Ghostly Armor/幽灵铠甲`、`HeartOfIron/铁之心`、`Lizard Tail/蜥蜴尾巴`。
- 修复后重新扫描：`static_knowledge_gaps logs=3 missing=0`。
- 已新增静态知识测试覆盖这些实体，防止后续回归。

## 对后续的影响

- 目标仍是 A0 终局胜利，不切 A4，不标记完成。
- route-risk assist 保持 assist，不升 pilot；先继续收集完整 manifest。
- 下一步最值得让模型逐步接管的是 card reward tie-break 和 potion-tempo high-confidence assist，而不是直接让 NN 出牌。
- Boss 清除是当前正式 gate 的关键缺口：需要围绕 Boss 前药水、卡组质量、前载/防御/输出结构做训练和离线对比。
- 第三局的 Act1 clear 苗头值得继续验证，但必须用完整落盘的下一轮证明。
