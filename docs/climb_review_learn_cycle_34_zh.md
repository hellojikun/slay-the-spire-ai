# 第 34 轮爬塔-复盘-学习记录

时间：2026-07-07  
目标：Ironclad A0 终局胜利  
结论：本轮没有取得 A0 终局胜利。3 局均为 `clean_trainable/completed_clean`，Act1 Boss reached=3/3，cleared=2/2，Act1 boss gate 已通过并建议 `promote_to_next_validation_batch`。真正瓶颈转移到 Act2 Boss，尤其是 Collector 的召唤物、大招窗口和资源释放。

## 本轮状态

- 运行范围：Ironclad A0，3 次 live MCP 尝试，`max-steps=3000`。
- 模型权限：`model_authority=assist`。
- 接管范围：`route_risk_card_reward_and_potion_tempo_assist`。
- 胜利：0/3。
- clean trainable：3/3。
- validation：第 1 局 pristine；第 2、3 局为 `usable_with_recoveries`，原因是 MCP null 后 synthetic terminal recovery。
- Act1 boss gate：PASS，`reached=3/3`、`cleared=2/2`、`pristine_cleared=2/2`。
- 训练状态：shadow、card、combat value、external prior、external blend 均训练完成，但只写入本轮 scratch/cycle 产物，没有提升为默认 live runtime controller。

## 单局结果

- `20260707_180529_ironclad_a0`：到达 Hexaghost，Boss 入场 HP 73/88，持有 Duplication Potion，T2 使用 Attack Potion 并选择临时 Whirlwind，最终 F16 死亡，Boss 余 36 HP。失败归因偏 `deck_quality`：Boss 前输出、AoE 与药水规划不足，Duplication Potion 未能转化为关键收益。
- `20260707_181835_ironclad_a0`：击败 Guardian，Act2 深入到 Collector，Boss 入场 HP 72/80。卡组核心包括 Clothesline+、Impervious+、Flame Barrier+、Whirlwind+，Boss relic 选择 Sozu。Act2 Slavers 依靠 Whirlwind+ 通过，但 Collector T5/T7 无法处理大招和召唤物，最终 synthetic game over。失败归因偏 `combat_planning`。
- `20260707_184956_ironclad_a0`：击败 Guardian，Act2 再次进入 Collector，Boss 入场 HP 76/80。路线中买 Cleave、拿 Second Wind、Shrug It Off+、Battle Trance+，Boss relic 选择 Philosopher's Stone。Collector T2 连用防御/生命类药水仍只挡到 18/35，T5 面对 incoming 52 只挡 6，T8 HP 11、incoming 33、block 13 后死亡。失败归因偏 `combat_planning`。

## 启发式与搜索影响

- live 执行层仍主要由现有 policy、one-turn search、direct lethal、事件/商店/营火/药水启发式驱动。
- 休息点行为是显式规则：低血或 Act2 buffer 低时 rest，安全时 smith，例如第 3 局 F23/F25/F32 休息。
- 药水使用大多是启发式触发，不是模型接管：第 3 局 Guardian 开局使用 Strength/Speed 类药水，Snake Plant 前使用 Fear Potion 和 Skill Potion，Collector T2 连用防御/生命药水。
- one-turn search 产生了大量局部可训练动作，尤其是 Shrug It Off、Defend、Cleave、Whirlwind、Second Wind 的回合组合。
- 失败点也来自搜索局限：Collector 大招窗口缺少多回合规划，T5/T8 仍按局部序列出牌，无法提前杀 TorchHead 或为大招储备 block。
- `max-steps=3000` 本轮不是失败原因。三局都在终局/死亡附近自然结束，瓶颈是策略和 Boss 战规划，不是步数上限阻止终局胜利。

## 学习与模型影响

- route-risk assist 真实影响路线评分，日志多次出现 `model risk -12.0`。它是学习模型对 route score 的 assist，不是直接点击 MCP。
- card reward 使用本地 reward policy 加学习/记忆分数；本轮产生 13 个 card model examples。
- potion tempo model 已接入 assist 训练管线，本轮训练得到 127 examples/29 feature weights；但可见 live 用药理由主要仍是 `Dangerous incoming damage`、`Emergency tempo`、`Long boss/elite fight` 等规则文本。
- combat value 神经网络已训练：PyTorch MLP，36 examples，7 validation examples，device=`cuda`，但 `runtime_authority=false`，当前只作为 shadow/value signal，不能称为神经网络接管执行层。
- 本轮 shadow models 使用 `source_quality=pristine` 训练，accepted rows=179：route-risk 15 examples，potion-tempo 127 examples，pre-boss deck-quality 1 example，combat-search 36 examples。
- learned memory 已更新，读取 3 个完成日志，applied_completed_runs=3。

## 外部数据影响

- 外部数据源：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 数据集：`mat1g3r_200_rotating_ironclad`，本地导入 50 个 Ironclad A20 run，card prior rows=889。
- 外部 prior 本轮训练：examples=889，deltas=151，`source_quality=external_prior`。
- 外部 blend 本轮训练：base_deltas=29，external_prior_deltas=151，external_only_deltas=122，`external_prior_blend_scale=0.25`，`external_prior_max_contribution=0.75`。
- 权限边界：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。
- 禁止用途：不能作为本地 A0 胜负证据，不能进入 `clean_trainable`、`pristine`、gate、learned memory 或 runtime authority。
- 作用定位：外部数据只给卡牌奖励提供低权重先验和离线对照，帮助冷启动，不替代真实 A0 MCP 爬塔数据。

## 训练产物

- Cycle summary：`data/climb_cycles/cycle_20260707_ironclad_a0_win_34/climb_cycle_cycle_20260707_ironclad_a0_win_34.json`。
- 本轮 scratch 模型目录：`data/climb_cycles/cycle_20260707_ironclad_a0_win_34/training/models/`。
- route-risk：15 examples，38 feature weights。
- potion-tempo：127 examples，29 feature weights。
- pre-boss deck-quality：1 example，0 feature weights。
- combat-search：36 examples，4 card priors，24 context buckets。
- card model：13 examples，29 deltas。
- combat value NN：36 examples，validation=7，`runtime_authority=false`。
- external prior：889 examples，151 deltas。
- external blend：151 deltas，未提升默认 runtime。

## 数据质量升级

- 本轮初始 static knowledge gap：`missing=1038 cards=51/4 relics=884/3 monsters=103/1`。
- 已补入卡牌别名：`Impervious/岿然不动`、`Flex/活动肌肉`、`Decay/腐朽`。
- 已补入怪物：`TorchHead/火炬头`。
- 已补入遗物：`Self Forming Clay/自成型黏土`、`Philosopher's Stone/贤者之石`、`Molten Egg 2/熔火之蛋`。
- 重扫结果：`static_knowledge_gaps logs=3 missing=0`。
- 影响：下一轮 shadow feature 的 `relic_unknown_count`、`enemy_unknown_count` 会降低，训练输入更干净；这不是新增启发式策略。

## 执行完整性

- 第 1 局终局为 pristine。
- 第 2、3 局均在死亡后 MCP state null，通过 synthetic terminal recovery 写出 manifest，validation 为 `usable_with_recoveries`。
- 没有 failed action 或 unrecovered action；执行层主要问题不是点击失败，而是战斗规划不足。
- per-run 复盘已写入 `data/climb_cycles/cycle_20260707_ironclad_a0_win_34/run_reviews/`，本文件作为本轮中文总复盘和长期记忆依据。

## 对后续的影响

- 目标仍是 A0 终局胜利，不切 A4，不标记完成。
- Act1 gate 已过，下一阶段应转向 Act2 validation batch，而不是继续围绕 Act1 补启发式。
- 模型逐步接管的低风险切入点：路线风险继续 assist；card reward 用 external blend 做离线对照；potion tempo 从 shadow/assist 观察开始；combat value NN 先用于重排候选或提示风险，不直接发 MCP 动作。
- Collector 是当前最清晰瓶颈：需要学习召唤物击杀优先级、大招窗口前的 block/药水储备、Whirlwind/Cleave/Second Wind 在多敌 Boss 中的价值边界。
- 下轮优先收集 Act2 Boss 样本，尤其是是否能在 HP 70+、有药水、卡组含 AoE/block 时稳定处理 Collector。
