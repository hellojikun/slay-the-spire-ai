# A0 爬塔训练操作记忆

更新时间：2026-07-07

当前长期目标是先拿到本地 MCP 的 A0 终局胜利，不启用 A4 难度。项目阶段已经从“继续堆启发式策略”转为“真实爬塔 -> 复盘 -> 学习 -> 改进 -> 再爬塔”。启发式和搜索仍是执行底座，但不再作为主要新增投入方向；新的投入应优先服务于数据闭环、模型训练、复盘质量和模型逐步接管执行层。

## 每局后必须写中文复盘

每进行一局真实爬塔或训练循环后，必须在对应 cycle 的 `run_reviews/` 目录写中文复盘。复盘至少区分以下来源：

- 启发式/搜索影响：路线、营火、商店、药水、出牌顺序、direct lethal、one-turn search、执行保护等旧策略或搜索导致的行为。
- 学习/模型影响：learned memory、card value model、shadow advice、combat value shadow、外部 prior blend 是否影响选择；必须写明 `model_authority` 和 `runtime_authority`。
- 外部数据影响：写清 source_id、manifest、样本数、权重、是否进入 runtime；外部数据不能被写成本地 MCP 的真实胜负证据。
- 执行完整性：action race、recovery、synthetic terminal、MCP null、max-step、screen stall 等是否污染训练。
- 对后续影响：下一轮是继续收集、修执行层、修训练源质量、修知识库、还是扩大模型权限。

如果本局没有模型实际接管，也必须明确写“模型未接管执行层”，不能把 shadow 预测或训练产物描述成真实 live 控制。

## 当前模型接管边界

当前 live 执行层仍主要由既有 policy、route risk、potion tempo、combat search 和 MCP action wrapper 控制。模型处于 assist/shadow 阶段：

- `model_authority=assist` 当前允许 route-risk assist 参与路线评分，也允许 card reward tie-break；它会影响 policy 的评分/排序，但不等于模型直接点击 live MCP。
- `runtime_authority=false` 表示模型没有直接控制 live MCP 点击、路线、药水、战斗出牌或商店购买。
- `external_prior` 和 `external_blend` 只能作为离线 prior 或低权重 card reward 参考，不得进入 `clean_trainable`、`pristine`、A0 gate、learned memory 或 runtime authority。
- 神经网络/价值模型要接管战斗前，必须先有足够干净的本地 MCP combat/value 样本，并通过离线 ablation 证明优于现有搜索。

下一阶段可以让模型逐步接管，但应先从“路线风险 veto / card reward assist / combat value shadow 校准”开始，再考虑 pilot 权限；不能直接把外部 A20 数据或未审计 shadow 预测升为 live controller。

## max-step 解释

`max-step` 是防止异常卡死的安全边界，不是胜利条件。只要 run 在死亡或胜利前正常结束，`max-step=3000` 不会让终局胜利永远无法到达。若出现被 max-step 截断的 run，必须在复盘中标为训练污染或 diagnostic，不得当作完整失败局学习。

## 外部数据使用原则

已接入的外部 run history 当前只用于训练外部 card prior/blend：

- 当前来源：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`
- 类型：`external_run_history`
- 当前用途：scratch external card prior、低权重 blend、离线对照
- 禁止用途：本地 A0 胜负证明、pristine gate、learned memory、路线/战斗/药水 live 接管

外部数据必须帮助模型成长，而不是替代真实爬塔。所有外部数据影响都要写入中文复盘，尤其是 `runtime_authority=false` 和 `does_not_control_live_mcp=true`。

## _28 后新增记忆

`cycle_20260707_ironclad_a0_win_28` 完成 3 局 A0 live MCP 爬塔，未取得终局胜利。Act1 boss gate 通过，但主要失败集中在 Act2 路线风险与资源缓冲：两局归因 `route_risk`，一局归因 `combat_planning`。本轮说明 Act1 质量已能稳定推进，但 Act2 不应继续靠单回合搜索硬扛；下一步要重点训练/校准路线风险、低血量避战、Coffee Dripper 下营火解释、以及“已有更安全路线时不要被高分节点诱导进强制精英”。

本轮训练状态：

- card model：trained，26 examples。
- learned memory：updated，applied_completed_runs=3。
- external prior：trained，889 examples。
- external blend：trained。
- shadow models：skipped，原因是 `source_quality=pristine` 下无 accepted shadow rows。
- combat value model：skipped，原因是 `no_pristine_combat_value_examples`。

静态知识缺口报告仍显示 `missing=1735 cards=986/5 relics=749/3`，主要来自 Bite、Double Tap 等中英文/ID 别名和日志实体对齐；下一轮前应优先修补知识库别名或重新生成 gap 报告，避免模型特征里继续出现 unknown static snapshot。

## _29 后新增记忆

`cycle_20260707_ironclad_a0_win_29` 完成 3 局 Ironclad A0 live MCP 爬塔，未取得终局胜利。3 局均为 `clean_trainable`，Act1 Boss reached 为 0/3，gate 判定 `NEEDS_WORK`，主失败归因为 `route_risk:3`。本轮说明 `_28` 的 Act2/Coffee Dripper 问题修完后，新的瓶颈回到 Act1 早期路线：F5/F6 在低准备度、低血量、无 tempo potion、缺 premium block/weak/aoe 时仍会进入战斗或精英链。

本轮训练产物：shadow models trained rows=74；route-risk model trained examples=13；card model trained examples=4；combat search model trained；combat value 神经网络 trained examples=45 但 `runtime_authority=false`；external prior trained examples=889；external blend trained deltas=151 且 `runtime_authority=false`。

已完成项目升级：route-risk 模型已接入路线层 assist。`model_authority=shadow` 时模型不影响 live；`model_authority=assist` 时模型只对路线候选施加封顶惩罚，并在 `route_evaluation.options[*].model_assist` 写入 `risk_score`、`penalty`、`model_examples`、`model_training_source_quality`；`pilot` 只预留更高封顶，不代表外部数据或神经网络可直接控制 MCP。旧 `models/route_risk_model.json` 已备份为 `models/route_risk_model.pre_cycle29_assist_20260707_145929.json`，`_29` 的 pristine route-risk 模型已提升为默认模型，下一轮 A0 live 爬塔应观察模型是否真正压下早期 forced elite。

外部数据仍只能作为 card prior/blend 和离线对照，不能作为本地 A0 胜负证据，不能进入 pristine gate、learned memory 或 live runtime authority。后续每局复盘必须继续区分：启发式/搜索行为、route-risk assist 学习行为、外部 prior 行为、执行完整性。

## _30 后新增记忆

`cycle_20260707_ironclad_a0_win_30` 已完成 3 局 Ironclad A0 live MCP 爬塔，未取得终局胜利。Act1 Boss reached=3/3，cleared=2，prefix_pristine_cleared=2，gate 通过并给出 `promote_to_next_validation_batch`。这说明 `_29` 提升后的 route-risk assist 开始改善早期路线，但目标仍未完成，不能标记 A0 胜利。

本轮模型接管边界已扩大到 `route_risk_assist_and_card_reward_tiebreaker`：路线风险模型会在 `model_authority=assist` 下参与 route evaluation 并施加 `model risk` 惩罚，但仍不直接控制 live MCP 点击；live controller 仍是现有 policy/search/action wrapper。后续复盘必须把 route-risk assist 写成“学习模型影响”，不能再写成纯 shadow 观察，也不能夸大为完全 pilot。

本轮训练状态：shadow models 因 `source_quality=pristine` 严格门槛而 skipped rows=0；card model trained examples=15；learned memory updated applied=3；combat value skipped examples=0；external prior trained examples=889；external blend trained deltas=151。三局均为 `usable_with_recoveries`，可用于复盘、卡牌学习和 gate 观察，但默认不能进入 pristine shadow 训练。

外部数据 `data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json` 本轮只作为 card prior/blend 的离线先验，`runtime_authority=false`，`does_not_control_live_mcp=true`。它帮助模型冷启动卡牌价值，但不是本地 A0 胜负证据，也不能直接提升为 live controller。

本轮后静态知识缺口已修复：`Black Blood`、`Letter Opener`、`Paper Frog` alias 已进入 `data/static_knowledge/relics.json`；`Letter Opener` 的 `skills_per_trigger` 与 `aoe_damage` 已进入遗物特征抽取；重新扫描第 30 轮日志得到 `static_knowledge_gaps missing=0`。

下一阶段重点：继续 A0，不切 A4；不要回到大规模新增启发式。Act1 gate 已过，下一轮应集中验证 Act2 路线风险、恢复窗口、Book of Stabbing、Spheric Guardian、Boss/精英长线战斗规划和药水释放时机。若要让 shadow 模型吃更多样本，必须显式选择放宽 source-quality，或先修复 terminal recovery 让日志达到 pristine。

## _31 后新增记忆

`cycle_20260707_ironclad_a0_win_31` 已完成 3 局 Ironclad A0 live MCP 爬塔，未取得终局胜利。Act1 Boss reached=1/3，cleared=0/2，gate 退回 `NEEDS_WORK -> improve_route_and_early_act1_survival`。这说明 `_30` 的 Act1 gate pass 不是稳定能力，目标仍是 A0 终局胜利，不能标记完成。

本轮主要失败不是执行污染：1 次 action race 已恢复，3 个 synthetic terminal 影响 pristine/gate 计数，但死亡机制集中在路线/准备度。两局 clean 都死在 Act1 Boss 前，第三局到 Guardian 但 Boss 入场无药、带 Parasite，T8 死亡。

route-risk assist 暴露新问题：默认模型只有 13 个 pristine examples，本轮几乎所有路线点都被打到 `risk≈0.90-0.99`、`penalty≈-30`，导致候选之间失去区分度。已修正为 examples < 30 时小样本 assist 使用更低惩罚封顶；仍保持 `runtime_authority=false`、`direct_mcp_control=false`，不直接点击 MCP。

路线层已新增 Act1 后段 forced hallway chain readiness penalty：当 F8+ 的 M/E 路线会进入 forced combat chain，且卡组缺前载、AoE 或即时 tempo potion 时，会记录 `act1_late_forced_hallway_frontload_gap`、`act1_late_forced_hallway_aoe_gap`、`act1_late_forced_hallway_no_tempo_potion` 并降低该路线评分。下一轮需要验证这是否减少第31轮那种 F11/F12/F14 多敌拖死。

本轮静态知识缺口已修复：`Parasite` 的中文/乱码别名 `瀵勭敓` 已加入 `data/static_knowledge/cards.json`；第31轮重新扫描为 `static_knowledge_gaps logs=3 missing=0`。

本轮默认 pristine shadow 训练仍 skipped rows=0；但额外训练了 `source_quality=usable` 的 scratch 候选模型，不提升 live：route-risk candidate 18 examples/49 weights，potion-tempo candidate 46 examples/20 weights，combat-search candidate 53 examples/3 card priors，pre-boss deck-quality 0 examples。后续只有经过离线对比和 gate 验证后才能 promotion。

## _32 后新增记忆

`cycle_20260707_ironclad_a0_win_32` 已执行 Ironclad A0 live MCP 爬塔，未取得 A0 终局胜利，目标不能标记完成。正式落盘 manifest 只有 2 局：Act1 Boss reached=2/2，cleared=0/2，gate 仍为 `NEEDS_WORK`，主要失败归因为 `potion_planning` 与 `deck_quality`。第三局 stdout 显示已击败 Act1 Boss 并推进到 F21，但因为进程强制停止、没有 manifest，只能作为 diagnostic，不得计入 clean_trainable、A0 gate、learned memory 或 promotion 证据。

本轮验证了第31轮后的 route-risk 小样本限幅：live route 日志多次显示 `model risk -12.0`，不再是全图 `-30` 饱和。它很可能改善 Act1 Boss 到达率，但仍只是 `model_authority=assist`，`runtime_authority=false`，不直接点击 MCP，也不能称为模型完全接管执行层。

本轮 usable scratch 候选模型已训练但未提升 live：route-risk 30 examples/55 weights，potion-tempo 37 examples/34 weights，pre-boss deck-quality 2 examples/0 weights，combat-search 71 examples/5 card priors。当前大多数模型仍是可审计统计模型；神经网络只适合作 combat value shadow，样本规模不足以直接接管 live 战斗。

外部数据继续只作为低权重 prior/blend：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`，accepted runs=50，card prior rows=889，Ironclad A20。它不是本地 A0 MCP 证据，不能进入 gate 或 runtime authority。

本轮静态知识 gap 已修复并归零：补入 `Violence/暴力`、`HandOfGreed/贪婪之手`、`Magnetism/磁力`、`Ghostly Armor/幽灵铠甲`、`HeartOfIron/铁之心`、`Lizard Tail/蜥蜴尾巴`；重扫 Cycle 32 得到 `static_knowledge_gaps logs=3 missing=0`。后续复盘必须继续记录启发式/搜索、学习模型、外部 prior、执行完整性的边界，尤其不能把未落盘第三局当作正式训练样本。

## _33 后新增记忆

`cycle_20260707_ironclad_a0_win_33` 未取得 A0 终局胜利，目标不能标记完成。正式落盘完整样本只有 1 局：到达 Slime Boss，入口 86/90 HP、0 药水，最终分裂后多敌压力下死亡；Act1 Boss reached=1/1、cleared=0/1，gate 仍为 `NEEDS_WORK`。第 2 局推进到 F4 并拿到 `Dexterity Potion`，但没有 manifest，只能作为局部诊断，不能进入 clean_trainable、A0 gate、learned memory 或 promotion 证据。

本轮项目已把模型接管范围扩展到 `route_risk_card_reward_and_potion_tempo_assist`。route-risk assist 在 live 中继续影响路线评分，常见 `model risk -12.0`；card reward 仍是启发式与模型/记忆分数混合；combat value 神经网络仍是 shadow。新接入的 potion tempo assist 本轮没有实际触发：第 1 局 Boss 入场 0 药水，第 2 局不完整且未见 `use_potion`。后续不能把“potion assist 已接入代码”误写成“potion assist 已在 live 成功用药”。

本轮训练产物：usable scratch 候选模型已训练但未提升 runtime，route-risk 15 examples/44 weights，combat-search 35 examples/4 card priors，pre-boss deck-quality 1 example/0 weights，potion-tempo 0 examples/0 weights。外部 prior 候选重新训练：`mat1g3r_200_rotating_ironclad`，50 个 Ironclad A20 run，889 条 card prior rows，151 deltas，输出到 `data/climb_cycles/cycle_20260707_ironclad_a0_win_33/training/external_prior_candidate_model/card_prior_model.json`；权限仍为 `runtime_authority=false`、`does_not_control_live_mcp=true`，禁止进入 gate、pristine、learned memory 和 runtime authority。

静态知识缺口已修复：Cycle 33 初扫为 `missing=291 relics=291/2`，缺 `Dream Catcher` 与 `Pear`；已补入 `data/static_knowledge/relics.json` 的 `Dream Catcher/捕梦网` 和 `Pear/梨子`，重扫为 `static_knowledge_gaps logs=2 missing=0`。注意：已生成的本轮 live_shadow rows 不会自动改写，修复从下一轮特征生成开始生效。

下一轮重点：继续 A0，不切 A4；优先保证完整 manifest 和 live MCP 终局/partial 落盘；围绕 Boss 前药水资源、Slime Boss 分裂前后规划、potion tempo 正负样本继续真实爬塔。不要回到大规模新增启发式，除非是修复执行层或数据质量的必要最小改动。

## _34 后新增记忆

`cycle_20260707_ironclad_a0_win_34` 已完成 3 局 Ironclad A0 live MCP 爬塔，未取得 A0 终局胜利，目标不能标记完成。三局均为 `clean_trainable/completed_clean`：第 1 局死于 F16 Hexaghost，失败归因 `deck_quality`；第 2、3 局均击败 Guardian 并死于 F33 Collector，失败归因 `combat_planning`。Act1 Boss gate 已通过：`reached=3/3`、`cleared=2/2`、`pristine_cleared=2/2`，下一阶段应推进 Act2 validation batch，而不是继续围绕 Act1 增加启发式。

本轮没有任何模型 `runtime_authority=true`。route-risk assist 继续通过 `model risk -12.0` 影响路线评分，但 `direct_mcp_control=false`；combat value 神经网络只是 shadow/value signal；药水使用文本主要来自 `Dangerous incoming damage`、`Emergency tempo`、`Long boss/elite fight` 等规则触发。不能把这些药水使用写成模型已直接接管 live MCP。

本轮训练已自动执行但没有提升默认 runtime 模型：shadow rows=179 accepted，route-risk 15 examples，potion-tempo 127 examples，pre-boss deck-quality 1 example，combat-search 36 examples；card model 13 examples/29 deltas；combat value PyTorch MLP 36 examples、7 validation examples、CUDA 训练，`runtime_authority=false`；learned memory 读取 3 个 completed runs 并更新。

外部数据继续只作为低权重 card prior/blend：`mat1g3r_200_rotating_ironclad`，50 个 Ironclad A20 run，889 card prior rows，151 deltas。外部 prior 和 external blend 均 `runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`，禁止进入 A0 gate、pristine、clean_trainable、learned memory 或 runtime authority。

本轮数据质量升级已完成：初扫 `static_knowledge_gaps missing=1038`，补入 `Impervious/岿然不动`、`Flex/活动肌肉`、`Decay/腐朽`、`TorchHead/火炬头`、`Self Forming Clay/自成型黏土`、`Philosopher's Stone/贤者之石`、`Molten Egg 2/熔火之蛋` 后，重扫 Cycle 34 为 `missing=0`。这是特征清洗，不是新增启发式策略。

下一轮重点：继续 A0，收集 Act2/Collector/Act3 validation evidence；优先学习 Collector 召唤物击杀优先级、大招窗口前 block/药水储备、Philosopher's Stone/Sozu 对 Act2 多敌压力的代价，以及 Battle Trance/Second Wind 在无能量或无可耗牌时的边界。模型逐步接管应从 route/card/potion assist 和 combat value 候选重排开始，不直接让神经网络控制 `play_card`。
## _35 后新增记忆

`cycle_20260707_ironclad_a0_win_35` 已完成 3 局 Ironclad A0 live MCP 爬塔，未取得 A0 终局胜利，目标不能标记完成。三局均为 `clean_trainable/completed_clean`，但 Act1 Boss gate 回到 `NEEDS_WORK`：reached=3/3，cleared=1/2，pristine_cleared=1/2。失败分别集中在 Guardian 前输出/卡组质量、Coffee Dripper 后 Act2 低血路线锁死、Hexaghost 前无药水且输出不足。

本轮确认：`map risk -xxx` 是正常惩罚展示，不是符号反转 bug。路线分数是 `base + lookahead_adjustment + model_adjustment`，负数会降低路线分；F29 的 `map risk -129.0` 是因为前序路线已经锁线，只剩一个危险 M 节点可选。后续不要做 route-risk 符号修复，应改进更早的路线选择与小样本模型区分度。

本轮关键训练升级：原始 Cycle35 使用 `source_quality=pristine`，因此 417 条 shadow rows 因 `usable_with_recoveries` 被过滤，shadow models skipped。随后已用 `cycle_20260707_ironclad_a0_win_35_usable_training` 重放三局并显式使用 `--source-quality usable`，训练成功：accepted_rows=415，route-risk 58 examples，potion-tempo 199 examples，pre-boss deck-quality 3 examples，combat-search 155 examples。combat value MLP 仍要求 pristine，因此在 usable lane 中按设计 skipped。后续 A0 live cycle 应使用 `--source-quality usable` 作为 shadow 统计模型训练 lane，但 gate/pristine 统计必须保持独立。

本轮静态知识已清洗到 `missing=0`：补入/修复 `Strike_R/打击`、`Defend_R/防御`、`Bash/痛击`、`Clothesline/金刚臂`、`Juggernaut/势不可当`、`Flex/活动肌肉`、`Reckless Charge/无谋冲锋`、`Burn/灼伤`、`Dazed/晕眩`、`Slimed/黏液`、`Decay/腐朽`、`Oddly Smooth Stone/意外光滑的石头`、`Mummified Hand/干瘪之手` 以及若干历史乱码 alias。相关测试 `tests.test_static_knowledge` 与 `tests.test_static_knowledge_gaps` 已通过。

外部数据仍只作为低权重 card prior/blend 和离线对照：`mat1g3r_200_rotating_ironclad` 为外部 Ironclad A20 run history，当前 50 runs、889 card prior rows，`runtime_authority=false`、`does_not_control_live_mcp=true`。它不能进入 A0 gate、`pristine`、`clean_trainable`、learned memory 或 runtime authority。

## _36 进行中：性能优化与成长加速边界
2026-07-07 晚间 Cycle36 live MCP 爬塔运行中，机器出现高内存与高 CPU。已执行低风险性能优化：保护 live 爬塔控制器 `python.exe` 与 Slay the Spire `java.exe`，将其优先级提升到 `AboveNormal`；将 Listary、QQMusic、Weixin、Steam WebHelper 等后台进程降到 `Idle`；不杀游戏、不杀爬塔控制器、不关闭 live MCP。优化后 CPU 从约 88% 降到约 51%-56%，内存占用从约 78% 降到约 65%-66%。后续若再次高负载，优先调整后台进程优先级或关闭非必要应用，不能中断正在进行的 live A0 run。

成长加速方向已经从“继续新增启发式”转为“扩大本地 MCP usable 数据的训练效率”。`source_quality=usable` 可以作为统计 shadow 模型的训练 lane，并在 Cycle35 usable promote 中验证成功；从本次代码更新开始，`climb_cycle` 中的 combat value 神经网络在 `source_quality=usable/diagnostic/all` 时不再直接 skipped，而是使用 `quality_policy=weighted` 训练 shadow 候选。该神经网络仍必须写明 `runtime_authority=false`，不能直接控制 `play_card`、路线、药水或 MCP 点击；usable/diagnostic 样本也不能进入 A0 victory、pristine gate 或外部数据证据。

Cycle36 结束后应立即执行聚合学习：用 Cycle34-36 的本地 MCP 日志做 `--skip-live --source-quality usable` 聚合训练，继续传入 `mat1g3r_200_rotating_ironclad` 外部 prior 作为低权重 card prior/blend；再跑 combat-search label replay/audit，重点审计 Act2 低血路线、Coffee Dripper 无法休息、Champ/Collector execute、Burn 终局伤害和 one-turn search 后续启发式是否覆盖了保命线。每盘仍必须写中文复盘，明确区分启发式/搜索、学习模型、外部 prior 和执行完整性。

## _36 后新增记忆：特征清洗与下一轮 live 前置条件

`cycle_20260707_ironclad_a0_win_36_feature_clean_retrain` 已完成离线重训并提升默认模型，仍未取得 A0 终局胜利，目标不能标记完成。本次不是新增启发式策略，而是修复训练特征污染：`combat_search` 原先直接读取 `state.relics`，在 live MCP 中文显示名乱码时会把已知遗物误记为 `relic_unknown_count`；现在统一改为 `_state_relic_items(state)`，优先使用带真实 id 的 `relic_items`。

验证结果：Cycle36 日志 `static_knowledge_gaps missing=0`；修复后 shadow feature audit 为 `categories_with_issues=0`、`unknown_static_features={}`，`combat_search` 226 行均无未知静态特征；相关 66 个测试通过。后续每轮 live 后，如果 gate 或审计出现 `feature_unknown`，应先判定是知识库缺口还是 snapshot 字段选择错误，优先修数据入口和静态知识，不要把假未知特征当作策略失败学习。

本次重训仍使用 `source_quality=usable`、`model_authority=assist`、`runtime_authority=false`。本地 MCP usable 数据训练 shadow/card/combat value；外部 `mat1g3r_200_rotating_ironclad` 仍只作为低权重 card prior/blend，不能进入 A0 gate、pristine、clean_trainable、learned memory 或 live MCP 控制。下一轮应继续 A0 live，优先争取额外 pristine Act1 Boss clear，并继续收集 Act2/Act3 真实样本。
