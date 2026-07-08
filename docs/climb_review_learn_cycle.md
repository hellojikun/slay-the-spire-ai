# 爬塔-复盘-学习循环运行手册

当前阶段停止继续给单次失败堆启发式例外。阶段目标是：**先在 A0 达成终局胜利**，再考虑更高进阶。

新的默认节奏是：

1. 爬塔：批量收集真实 MCP 运行日志。
2. 复盘：用 manifest、diagnosis、gate、shadow advice 和 per-run 中文复盘判断失败归因。
3. 学习：只从合格数据刷新 learned memory、card value model 和 shadow models。
4. 下一批爬塔：带着复盘结论继续收集 A0 终局证据。

启发式仍是 live 控制器的基础。学习模型默认是 shadow 评分、诊断、tie-breaker 或高置信候选，不直接接管整局。

## 推荐主命令

日常优先用 `slay_ai.climb_cycle` 串起整轮：

```powershell
python -m slay_ai.climb_cycle `
  --name cycle_YYYYMMDD_ironclad_a0_win_01 `
  --characters IRONCLAD `
  --ascension 0 `
  --attempts-per-target 3 `
  --max-steps 3000 `
  --existing-save abandon `
  --log-dir runs\ai_runs_climb_cycle_ironclad_a0_win_01 `
  --output-dir data\climb_cycles `
  --knowledge-dir data\static_knowledge `
  --source-quality pristine `
  --card-min-count 1 `
  --card-max-delta 6 `
  --min-count 2 `
  --max-weight 2 `
  --model-authority assist `
  --use-all-hardware
```

`--model-authority assist` 只让模型在卡牌奖励近似平分、且候选卡模型信号为正时打破僵局。路线、药水、战斗、商店仍由现有策略和搜索控制，模型只做 shadow/复盘/训练证据。

## 工具职责

- `slay_ai.climb_cycle`：主入口。负责 live 爬塔、离线复盘、训练、中文 per-run 复盘和 cycle summary。
- `slay_ai.campaign`：live collection 子步骤。负责真实 MCP 批量跑局、写 JSONL、进度文件、每局 manifest、shadow rows/advice。
- `slay_ai.offline_batch`：复盘阶段离线入口。只读已有日志，生成 training manifest、shadow rows、shadow advice、run diagnosis、Act 1 boss gate、handoff 文件。
- `slay_ai.train_card_model`：卡牌奖励价值模型刷新入口，推荐通过 manifest 只读 `clean_trainable` 日志。
- `slay_ai.learn`：轻量 learned memory 重放入口，推荐同样走 manifest。
- `slay_ai.act1_boss_gate`：阶段验收入口。A0 当前先看 Act 1 boss reach/clear/pristine clear，再推进到完整终局胜利。

## live MCP 所有权边界

- 同一时间只有主线程或一个明确指定的 worker 可以控制 live MCP、游戏窗口或发送 `execute_actions`。
- 只读 worker 可以运行 `run_status`、`offline_batch`、`act1_boss_gate`、训练命令和日志分析，但不能启动/继续/放弃游戏，也不能向 MCP 发动作。
- `--use-all-hardware` 只表示离线训练可使用 CPU/GPU 资源；当前 MCP 控制仍按单游戏实例处理。
- 如果 live run 正在写 JSONL，监控侧优先用 `run_status`，不要抢 MCP 所有权。

## 训练污染边界

- manifest 是执行证据进入学习前的闸门。不要直接把整批 `runs\...` 喂给训练，除非明确是在做污染复盘。
- `clean_trainable`：默认可进入 `train_card_model --manifest` 和 `learn --manifest` 的日志。
- `validation_grade=pristine`：严格验证证据。默认用于 shadow 模型训练和 boss 稳定性声明。
- `validation_grade=usable_with_recoveries`：可保留为复盘证据或显式消融输入，但不要默认当作 pristine 训练源。
- `diagnostic_excluded` 和 `infra_blocked`：不能进入默认学习。它们用于修 runner、MCP、日志分类、command surface 或数据质量。
- 外部爬塔数据只能先进入单独 provenance 桶，例如 `external_run_history` 或 `external_reference`。它可以帮助 shadow pretraining、卡牌先验、静态知识和 ETL 测试，但不能计入本地 A0 胜利、`clean_trainable`、`pristine` 或 gate 通过。详见 `docs/external_run_datasets.md`。
- 外部数据的使用决策见 `docs/external_data_usage_plan.md`。当前只允许第一阶段：77M 小样本 scratch 先验和静态卡牌 reference；不训练 live 执行层。

## 模型逐步接管计划

1. `shadow`：所有模型只记录与复盘，不改变动作。
2. `assist`：只开放卡牌奖励 tie-breaker；必须写 `decision.metadata.model_authority`；候选卡必须有正向模型信号，不能因为“负得更少”覆盖启发式。
3. `pilot`：保留为后续扩大权限的开关；扩大前必须先有 A0 稳定终局证据和复盘确认。
4. 路线、药水、战斗、商店不在当前接管范围内；它们的模型结果先作为 shadow advice、诊断和训练标签。

每盘训练后必须写中文复盘，明确哪些行为受启发式影响、哪些行为受学习/模型影响，以及对后续爬塔有什么影响。复盘协议见 `docs/run_review_protocol.md`。

## 战斗执行层复盘结论

2026-07-07 的 A0 `_04` 批次显示：Slime Boss 后半段低血量时，执行层会把 Hemokinesis 等自伤攻击的即时伤害估得过高，即使存在无自伤击杀同一攻击目标的 Strike。该问题不是外部数据缺口，而是搜索/单卡评分没有充分保留余血。

已写入执行层边界：

- 自伤攻击可以用于终结战斗或避免死亡，但如果战斗不会结束，且自伤后只剩极低生命，应被显著降权。
- 在同样能移除当前攻击或达到同样 projected loss 时，搜索器优先保留生命值的路线。
- 这类修正属于复盘后的执行层安全评分，不是新增针对某张牌、某只怪的孤立启发式例外。

后续复盘如果再次看到低血量自伤、missed single-card search、missed direct kill，应先检查 `combat_search_labels`、`combat_label_replay_audit` 和 policy 回归测试，再决定是否训练 combat_search shadow model 或修执行层。

## _05_retry 后新增边界

2026-07-07 的 A0 `_05_retry` 批次显示两个新问题：

- 卡牌奖励 `assist` 曾在 Clash / Sever Soul / Inflame 三选一中选择 Sever Soul，只因为它的模型信号“负得较少”。这类行为现在不再视为有效学习接管；assist 只有在候选卡模型信号为正时才能覆盖启发式赢家。
- 高压战斗中，one-turn search 找到的多步降损线可能因为剩余序列识别过窄而丢失。现在高压且收益明显的搜索序列会先保留，下一次动作读取时仍由 fresh search 判定是否继续或中断。
- 路线复盘显示 F5 早死不是 F4 当下误选，而是低血、无即时药水、必须先打一场才能到休息时仍可能高估 `M`。readiness 现在会把这类“低缓冲战斗在恢复点之前”的路径标为风险，并让分叉处优先考虑安全事件。

这些都属于复盘后的执行层/接管边界修正，不扩大模型权限，也不把外部数据写入 live 执行层。

## _06 后阶段推进

2026-07-07 的 A0 `_06` 批次三盘均到达并清掉 Act 1 Boss，Act 1 boss gate 通过。当前目标仍是 A0 终局胜利，但主要瓶颈已经从 Act 1 Boss 生存转为 Act 2 路线风险和后半战斗规划。

新增路线边界：

- Act 2 中血量受伤状态（约 70% 以下）不能继续把普通战斗的静态分数固定压过安全问号。
- 如果 `M` 或 `E` 会导致短期 forced combat / forced elite，而同屏 `?` 能避开 forced combat 或更快接近休息/商店，应优先保留生存缓冲。
- `_06` 的 F21/F23 证据显示，HP 51/80 或 53/80 时硬进战斗链，会把后续路线推入低血精英或低血事件战；这类修正属于 route risk gate，不是扩大模型执行权限。

## _07 后阶段推进

2026-07-07 的 A0 `_07` 批次仍未达成终局胜利，但三盘都清掉 Act 1 Boss，训练链路继续写入本地学习数据：

- `clean_trainable=3`，其中 `validation_grade=pristine` 只有 1 盘，另外 2 盘因 recovered action race 只能作为 usable_with_recoveries。
- 终局：F21 route_risk、F33 Bronze Automaton combat_planning、F24 Sentry/SphericGuardian combat_planning。
- 本轮训练：shadow rows accepted 241；route risk 30 examples / 80 weights；potion tempo 158 examples / 49 weights；pre-boss deck quality 2 examples；combat search 51 examples / 8 card priors / 19 context buckets；card model 46 examples；learned memory 应用 3 盘。
- 外部数据未进入本轮 runtime；本轮行为仍来自启发式、one-turn search、本地 learned memory/card model 的 assist 边界。

关键复盘结论：

- `_06` 后新增的 Act 2 安全问号路线边界已经生效：第 2 盘在 F21 37/80 后选择 `?` 并到达 F23 篝火；第 3 盘 F20 后 57/88 也连续选择 `?` 到达 F23 篝火。
- 新瓶颈转为 Act 2 篝火过贪：第 2 盘 F23 37/80、F25 43/80、F32 51/97 均 smith；第 3 盘 F23 57/88 smith 后在 F24 长战死亡。
- 因此执行层新增边界：Act 2 后半段低缓冲篝火应优先 rest，而不是继续把升级价值压过生存缓冲。

新增休息边界：

- Act 2 `floor >= 23` 且 HP 低于约 70% 时休息。
- Act 2 Boss 前 `floor >= 31` 且 HP 低于约 75% 时休息。
- 这不是单怪/单楼层特例，而是 `_07` 两盘复盘共同指向的资源管理边界。下一轮如果仍死于 Act 2 后半段，应先检查休息后血量、路线强制战斗和 combat_search 标签，而不是继续增加卡牌奖励启发式。

神经网络接入状态：

- 项目已有 `slay_ai.train_combat_value_model`，会从 combat_search labels 训练 PyTorch MLP shadow combat value model。
- `slay_ai.climb_cycle` 现在会在训练阶段自动尝试写出 `training.models/combat_value_model.pt` 和 `combat_value_training_summary.json`；只有 `source_quality=pristine` 且有 combat value 样本时才训练，否则写 `skipped`。
- `_07` 手动训练验证通过：54 条 pristine 样本、149 维输入、CUDA 后端、PyTorch MLP；该模型仍标记 `runtime_authority=false`。
- 当前它只作为 shadow/离线评分模型使用，不接管 `play_card`。模型要进入 live 执行层，必须先经过本地 A0 clean/pristine 日志评估，并在中文复盘中写明影响动作、权重和回滚条件。

## _08 后阶段推进

2026-07-07 的 A0 `_08` 批次仍未达成终局胜利，三盘都到达 Act 1 Boss 后死亡；因此当前爬塔目标继续停留在 A0 终局胜利，不进入 A4。

- 运行结果：`clean_trainable=3`，`validation_grade=pristine` 1 盘，`usable_with_recoveries` 2 盘。
- Boss 结果：F16 Hexaghost 死亡 2 盘，F16 The Guardian 死亡 1 盘；Act 1 boss gate 显示 `reached=3/3`、`cleared=0/2`、`pristine_cleared=0/2`。
- 主要归因：`potion_planning:3`，但复盘细看同时包含 Boss 长战输出不足、单回合搜索对直接击杀/单卡减伤的执行优先级不足。
- 本轮训练：shadow rows accepted 142；route risk 15 examples；potion tempo 94 examples；pre-boss deck quality 1 example；combat search 32 examples；card model 23 examples；learned memory applied 3 runs。
- 神经网络训练已经接入 `climb_cycle` 自动流程：`combat_value_model` 本轮自动训练成功，backend=`pytorch_mlp`，examples=32，validation_examples=6，device=`cuda`，model path=`data/climb_cycles/cycle_20260707_ironclad_a0_win_08/training/models/combat_value_model.pt`，`runtime_authority=false`。

行为来源复盘：

- 启发式影响：F8/F15 休息来自现有 Act 1 风险边界；战斗中的用药、one-turn search、fallback 仍是 live 执行层主体。`_08` 第三盘 F8 “Act 1 risk is high without potions; rest.” 是明确启发式结果，不是神经网络结果。
- 学习结果：本轮训练产物进入 cycle scratch 和 learned memory；card/shadow/combat_value 都只用于离线复盘、后续候选评分和下一轮证据积累，尚未直接控制 `play_card`。
- 外部数据：本轮没有把外部日志注入 runtime，也没有把外部数据计入 A0 gate；外部数据仍限定为 schema/ETL、静态知识、先验参考和 scratch pretraining，详见 `docs/external_run_datasets.md` 与 `docs/external_data_usage_plan.md`。

执行层修正：

- `combat_search` 标签审计显示 `_08` 有 5 条排除标签：`missed_single_card_search=3`、`missed_direct_kill=2`。
- 已补执行层边界：直接斩杀由单卡斩杀逻辑优先，不再被一般 one-turn search 序列抢走；高压回合允许单卡搜索结果带 metadata 执行，以便减少“搜索知道但执行不采纳”的断点。
- 回放审计 `combat_label_replay_after_policy_fix.json` 显示 5 条排除标签中当前 policy 仅 `matches_label=1`、`matches_actual=1`、`other=3`，说明这只是第一步修复；后续仍需围绕 Act 1 Boss combat、药水规划和 search-label 闭环继续改进。

下一轮验收目标：

- 仍以 A0 为目标，优先收集 Act 1 Boss clear 证据：至少补足 2 次 Act 1 Boss cleared，其中 pristine cleared 也要补足 2 次。
- 如果仍死在 Boss，先查 `combat_label_audit`、`combat_label_replay_audit`、Boss 入口药水数量、Boss 长战输出曲线，再决定是否扩大 combat value 模型在执行层的权限。

## _09 后阶段推进

2026-07-07 的 A0 `_09` 批次仍未达成终局胜利；三盘全部到达 Act 1 Boss，但 `cleared=0/2`、`pristine_cleared=0/2`，因此阶段目标仍然是 A0 终局胜利，不能标记完成。

- 运行质量：3 盘均为 `clean_trainable/completed_clean`，且全部 `validation_grade=pristine`，没有 action recovery。
- 失败位置：Slime Boss 2 盘，The Guardian 1 盘；run_status 归因为 `potion_planning=2`、`deck_quality=1`。
- 训练结果：shadow trained rows=251；card model trained examples=10；combat value PyTorch MLP trained examples=110，validation_examples=22，device=`cuda`，val_loss=0.8186，`runtime_authority=false`。
- 标签审计：combat_search labels 110 条，排除 1 条 `missed_single_card_search`；replay 显示当前 policy 在该例上先用虚弱药水，所以不匹配 Ghostly Armor 单卡标签。

关键复盘结论：

- 第 1、2 盘 Slime Boss 后半段都死于分裂后的多怪高压，表现为低血时继续 fallback 攻击、搜索线未完整转换为足够防御。
- 第 2 盘满血进 Slime Boss，但 T1 过早打出 Fiend Fire 清空手牌，T3/T6 又在高压下 fallback 攻击，说明强输出牌的使用时机仍需执行层保护。
- 第 3 盘 67/80 进 Guardian，T2 成功用攻击触发 Mode Shift 移除 32 incoming，这是有效学习/搜索证据；但 T8 在 Sharp Hide + incoming pressure 下连续 Strike，反伤和 block 消耗把血量从 47 拉到 26，最终死亡。

本轮执行层修正：

- 已修复 `policy_combat` 的空手等待边界：满能量、空手、疑似新回合发牌未完成时等待；只有能量已花、0 能量、或当前 block 覆盖 incoming 时才 end turn。
- 已新增 Guardian/Thorns 反伤压力降权：当反伤存在且本回合仍有 incoming pressure 时，非击杀、非移除攻击、非带足够格挡的攻击显著降权，避免 Strike 消耗防御缓冲。
- 这些修正属于执行层安全评分，不是扩大模型 live 权限；combat value 继续保持 shadow。

下一轮重点：

- 继续 A0 `_10`，优先验证 Act 1 Boss clear 是否恢复。
- 若仍死 Slime Boss，下一刀优先检查分裂后多怪低血 fallback 攻击、Fiend Fire/True Grit 等改变手牌的牌是否应退出 one-turn search/pending 序列。
- 若仍死 Guardian，检查 Sharp Hide 回归是否减少反伤导致的低血崩盘。

## _10 后阶段推进

2026-07-07 的 A0 `_10` 批次仍未达成终局胜利，但阶段推进明显：三盘均为 `clean_trainable/completed_clean`，Act 1 Boss 到达 2/3、到达后清除 2/2，第三盘通过 Act 2 Boss 并推进到 Act 3 F38 后死亡。A0 目标继续保持，不进入 A4。

- 运行质量：3 盘均为 `usable_with_recoveries`，终局均由 `synthetic_after_mcp_null` 落盘；terminal recovery 3 次尝试、2 次成功、1 次失败，工程侧仍需修复终局恢复可信度。
- 失败位置：第 1 盘 F6 三哨卫，归因 `route_risk`；第 2 盘 F22 Chosen+Byrd，归因 `combat_planning`；第 3 盘 F38 Giant Head，归因 `route_risk`，但核心战斗证据指向 Act 3 高压单怪处理。
- Act 1 Boss：两次 Guardian 均清除，说明 `_09` 后的空手等待边界与 Guardian/Thorns 反伤压力降权恢复了 Act 1 Boss clear；但第 3 盘 Guardian 仍以 38/80 出 Boss，健康度不足。
- 训练结果：本轮 `source_quality=pristine` 下 shadow models 因无 pristine shadow rows 被跳过，card model 训练成功 examples=29，combat value model 因 `no_pristine_combat_value_examples` 被跳过，learned memory 已应用 3 盘。
- 标签审计：combat_search labels=158，排除 2 条，原因均为 `missed_single_card_search`；当前 policy 回放 2 条中 `matches_actual=1`、`other=1`，说明仍存在药水/单卡搜索仲裁与高压单卡防御优先级问题。

行为来源复盘：

- 启发式/搜索影响：live 执行仍由现有 policy、one-turn search、route risk、rest/shop/potion 规则控制。第 3 盘早期删除 Strike、购买 Flame Barrier、F23/F32 低 Act2 buffer 选择 rest、F24 购买防御牌和药水、Guardian 和 Chosen 回合的防御序列，都来自启发式和搜索。
- 学习结果影响：本轮 card model 与 learned memory 已更新，但没有 `runtime_authority=true` 的执行层接管；模型只在 `assist` 边界内作为卡牌奖励辅助与后续复盘/训练证据。第三盘选择 Demon Form、Snecko Eye、Dark Blood、Feed 等行为不能直接归因于神经网络接管。
- 外部数据影响：外部爬塔数据仍只作为 `docs/external_run_datasets.md` 与 `docs/external_data_usage_plan.md` 中定义的 reference/scratch/pretraining 候选，没有计入本地 A0 gate、没有写入 clean/pristine live 训练，也没有接管 runtime。

关键复盘结论：

- Act 1 Boss clear 已恢复，但 gate 仍缺 1 次 Act 1 Boss reach；第一盘 F6 早死说明 early route/deck readiness 还不能忽略。
- Act 2 的主要伤害来源是蛇花、Chosen 组合与持续战斗链。第三盘过了 Chosen+Byrd，说明上一盘 F22 死亡不是固定必败，而是低血、Hex/Dazed 污染、遗物/牌组和当回合行动顺序叠加。
- Act 3 暴露新瓶颈：Nemesis/Giant Head 这类高压单怪下，能力牌和自伤牌时机过乐观；`avoided_lethal=True` 的搜索奖励会掩盖“本回合勉强活着但下回合极易死亡”的低血未来风险。
- Collector 战与 Giant Head 战都出现搜索评估和真实结算偏差：多目标/召唤物回合里，搜索声称 avoided lethal 或大幅降损，但实际仍把 HP 压到危险线。下一刀优先修搜索评分的低血未来风险和高压能力牌时机，而不是扩大模型 live 权限。

下一轮重点：

- 继续 A0 `_11`，优先补足 1 次 Act 1 Boss reach，同时观察 Act 1 Boss clear 是否稳定保持。
- 代码侧优先修复三类边界：高压单怪下能力牌/自伤牌降权；`avoided_lethal` 在低剩余 HP 时的未来风险惩罚；Act2/Act3 连续普通战斗路线在低血时的风险加权。
- 训练侧继续保留模型 shadow/assist，不提升到 live combat authority；只有当 A0 终局胜利和复盘证据稳定后，才考虑让 combat value model 参与执行层 tie-breaker。

_10 复盘后的执行层修复：

- `combat_search` 新增低剩余血量缓冲惩罚：当搜索线虽然把 `projected_loss` 从致死压到非致死，但剩余 HP 低于动态安全缓冲时，不再让 `avoided_lethal=True` 的固定奖励掩盖未来死亡风险。
- `policy` 新增高压能力牌降权：Demon Form、Feel No Pain、Metallicize 等 POWER 在当回合压力较高且不能立刻覆盖伤害时会被降权；Metallicize 保留较轻惩罚，因为它有少量回合末防御价值。
- `policy` 新增高压自伤牌降权：Hemokinesis 等即时 HP cost 牌在压力下会把自伤和剩余 incoming 一起计入风险；除非击杀结束战斗，否则低血线会显著降权。
- 回归测试覆盖：`test_high_pressure_power_defers_to_available_block`、`test_high_pressure_slow_power_does_not_beat_fallback_attack`，并保留 Guardian 高压单卡搜索、combat search、policy_combat、climb_cycle、card model 相关回归。

## _11 后阶段推进

2026-07-07 的 A0 `_11` 批次仍未达成终局胜利，但 Act 1 boss gate 已通过：3 盘全部到达 Act 1 Boss，2 盘清除 Boss，且 2 盘均为 prefix pristine clear。阶段目标仍是 A0 终局胜利，不进入 A4。

- 运行质量：3 盘均为 `clean_trainable/completed_clean`；2 盘 `validation_grade=pristine`，1 盘 `usable_with_recoveries`，有 1 次 recoverable action race。
- Boss 结果：第 1 盘清除 Hexaghost 后死于 Act2 F24 Gremlin Leader；第 2 盘死于 Act1 F16 Hexaghost；第 3 盘清除 Slime Boss 后死于 Act2 F23 三奴隶。
- gate 状态：`act1_boss_gate PASS`，`reached=3/3`、`cleared=2/2`、`pristine_cleared=2/2`；下一阶段应从 Act1 gate 迁移到 Act2 survival / validation batch。
- 训练结果：shadow models 训练成功 rows=259；card model 训练成功 examples=24；combat value PyTorch MLP 训练成功 examples=87、validation_examples=17、device=`cuda`、runtime_authority=false；learned memory 应用 3 盘。
- 标签审计：combat_search labels=118，排除 2 条 `missed_single_card_search`；当前 policy replay 2 条中 `matches_actual=1`、`other=1`。下一轮仍需复查被排除标签，但不应因此暂停爬塔。

行为来源复盘：

- 启发式/搜索影响：F6/F15 低风险休息、F10/F11/F18/F21 多次 Shrug/Flame Barrier/Impervious 防御序列、Slime Boss T3 分裂移除 35 incoming、F23 三奴隶 T3 Impervious+Shrug 完全挡住 32 incoming，均来自现有启发式和 one-turn search。
- 复盘修复影响：`_10` 后新增的低剩余血量缓冲惩罚与高压能力牌/自伤牌降权，让 `_11` 出现更多高压防御正例；尤其第 3 盘 Slime Boss 分裂后不再立刻崩盘。
- 学习结果影响：本轮训练链路首次在当前阶段同时产出 shadow/card/combat value 三类模型，但 combat value 仍为 shadow，`runtime_authority=false`；live 出牌仍不能归因于神经网络接管。
- 外部数据影响：外部数据仍没有进入 runtime 或本地 gate；它只作为参考数据集和后续 scratch/pretraining 方案记录，不能替代本地 A0 终局证据。

关键复盘结论：

- Act1 已从主要瓶颈降级为稳定性问题：Slime Boss 与 Hexaghost 都有 clear 样本，但 Hexaghost 长战仍会被 Combust/低伤害曲线拖死。
- 当前主瓶颈转为 Act2：Gremlin Leader、三奴隶，以及事件导致 max HP 下降后继续进精英。路线层需要识别 Bites/最大生命降低后的 elite 风险，不能只看当前 HP 是否较高。
- 战斗层的新问题是中压持续掉血：高压防御搜索有效，但普通战斗中单卡评分仍会在 10-15 incoming 时偏输出，导致进入 Act2 精英前血线被磨低。
- 搜索日志中的 pending sequence 文案会展示完整原始 sequence，而实际 pending 只执行剩余牌；这不是必然执行 bug，但复盘时需要结合 action metadata 和 hand state 判断。

下一轮重点：

- 继续 A0 `_12` 前，优先考虑路线/事件风险修正：Act2 中段若 max HP 因 Bites/事件下降，或下一节点为 elite，应提高休息/问号/商店优先级，降低直接进 elite 的评分。
- 补充 Bloodletting/Combust 类 HP cost 或每回合自伤风险的执行层边界；`Maxwell` 建议将 Bloodletting 纳入 `self_damage_hp_cost_cards`，Combust 则需要按持续自伤和 Boss 长战单独评估。
- 保持 combat value model shadow，不提升 live authority；下一次是否扩大模型接管，只能在 A0 终局胜利和复盘稳定后讨论。

_11 复盘后的路线层修复：

- `policy_route` 新增 Act2 低最大生命精英路径惩罚：当事件或 Bites 把 `max_hp` 压到约 60 以下时，即使当前 HP 比例接近满血，直接精英或近端 forced elite 都会被标记为 `act2_low_max_hp_elite_path` 并显著降权。
- 如果存在安全问号/恢复路径，低 max HP 的 immediate elite 会被进一步标记为 `act2_low_max_hp_immediate_elite`，优先选择问号、休息、商店等恢复缓冲。
- 该修复来自 `_11` 第 3 盘 F22 Bites 后 59/59 继续进 F23 三奴隶死亡；它属于 route risk gate，不扩大模型 live 权限。
- 回归测试覆盖：`test_map_act2_low_max_hp_prefers_question_over_elite`，并保留 Act2 injured safer-question 与 route/readiness 相关回归。

## _12 后阶段推进

2026-07-07 的 A0 `_12` 批次仍未达成终局胜利；三盘均为 `clean_trainable/completed_clean`，其中 1 盘 `pristine`、2 盘 `usable_with_recoveries`。本轮训练链路已经继续接入：shadow models trained rows=180，card model trained examples=17，combat value PyTorch MLP trained examples=49，`runtime_authority=false`，learned memory applied=3。A0 目标继续保持，不进入 A4。

- 运行结果：第 1 盘 F16 Hexaghost 死亡，归因 `deck_quality`；第 2 盘 F16 Hexaghost 死亡，归因 `deck_quality`；第 3 盘清除 Hexaghost 后推进到 Act2 F23 三奴隶死亡，归因 `combat_planning`。
- Act1 boss gate：`reached=3/3`，`cleared=1/2`，`pristine_cleared=1/2`，状态为 `NEEDS_WORK`，下一批 gate 仍建议 `improve_act1_boss_combat`。
- 标签审计：combat_search labels=113，排除 1 条 `missed_single_card_search`；当前 policy replay 对该条 `matches_label=0`、`matches_actual=0`、`other=1`，下一轮应先复查这个排除标签，不因为单条异常暂停真实爬塔。
- live MCP 质量：本轮有 recovered action race 3 次、unavailable action 1 次，两个终局由 `synthetic_after_mcp_null` 记录并恢复成功；这些属于执行/界面同步证据，不计为模型学习行为。

行为来源复盘：

- 启发式/搜索影响：本轮所有 live 出牌、路线、药水、商店和休息仍由现有 policy、route risk、potion tempo、one-turn search 控制。第 1 盘 F16 的高压防御序列、第 2 盘低血后走宝箱/问号/商店/休息、第 3 盘 F6 低风险休息、F12 boss 前休息、F21 低血走商店和 F22 问号，均来自启发式或搜索层。
- 复盘修复影响：`_10` 后的低生命缓冲惩罚让多次高压回合更重视保血；`_11` 后的 Act2 低最大生命值精英风险修复没有在本轮直接触发 Bites 场景，但低血路线确实更倾向恢复节点。第 3 盘 F23 三奴隶更像地图约束下的 forced elite/forced combat，而不是路线层主动冒险。
- 学习/模型影响：card/shadow/combat value 均训练成功，但没有 `runtime_authority=true`；卡牌奖励中的 Shrug It Off、Uppercut、Pommel Strike、Immolate、Thunderclap、Hemokinesis 等会进入后续学习证据。第 3 盘选择高输出包并清除 Hexaghost，是卡牌价值和本地记忆可学习的正样本；第 1、2 盘 Hexaghost 长战死亡，是输出曲线不足和长战自损风险的负样本。
- 外部数据影响：外部爬塔日志仍只作为 `docs/external_run_datasets.md` 与 `docs/external_data_usage_plan.md` 中定义的 reference/scratch/pretraining 来源；本轮没有把外部数据写入 live runtime、没有计入 A0 gate、没有替代本地 `clean_trainable/pristine` 证据。

关键复盘结论：

- Act1 boss 仍不稳定。第 1 盘与第 2 盘均死于 Hexaghost，核心不是路径单点错误，而是长战输出不足、Combust/Hemokinesis 等自损或慢速牌的风险折价不足，以及 one-turn search 仍偏向“本回合活下来”而非“保留下回合可赢缓冲”。
- 第 3 盘证明进攻质量提升可以清掉 Hexaghost：Bash+、Uppercut、Pommel Strike、Perfected Strike、Hemokinesis、Immolate 共同缩短 boss 战，但以 22/80 进入 Act2，说明输出胜利仍伴随过薄生命缓冲。
- Act2 新瓶颈是三奴隶这类高爆发精英。第 3 盘 36/80 进入 F23 三奴隶，T1 只能挡 5，随后死亡；这里需要 combat planning 学会在 Act2 低血时更重视即刻爆发防御、药水可用性和慢速商店牌风险。
- Dark Embrace 购买应进入负面复盘：低血 Act2 买慢速引擎牌不一定错误，但在三奴隶前缺乏立刻防御/爆发收益，应由后续 card/shop 模型学习折价。

下一轮重点：

- 继续 A0 `_13`，不提升到 A4，不把 combat value 直接接管 live 出牌。
- 修复方向优先级：Hexaghost 长战输出/自损折价、Act2 低血三奴隶即时防御、慢速 power/shop 购买在低血 Act2 的折价。
- 训练方向：combat value 继续 shadow；允许它先做候选动作离线排序和失败样本解释，等 A0 终局胜利证据稳定后再讨论 live tie-breaker。

_12 复盘后的商店层修复：

- `policy_shop` 新增 Act2 低血慢速 POWER 购物折价：当 Act2+ 且 HP 比例不高于 55% 时，Dark Embrace、Demon Form、Barricade 以及慢速 engine power 不再只按静态卡牌价值购买，会额外扣除低血生存风险。
- 该修复来自 `_12` 第 3 盘 F21：36/80 低血时购买 Dark Embrace，随后 F23 三奴隶首回合无法形成即时防御/击杀，最终死亡。结论是低血 Act2 商店应优先即时防御、药水或删 Strike，而不是慢速引擎牌。
- 回归测试覆盖：`test_shop_screen_low_hp_act2_purges_over_slow_power`，并保留商店高级卡购买、删 Strike、Boss 准备药水、普通离店等回归。

## _13 后阶段推进

2026-07-07 的 A0 `_13` 批次仍未达成终局胜利；三盘均为 `clean_trainable/completed_clean`，但全为 `usable_with_recoveries`，因此 `source_quality=pristine` 下 shadow models 与 combat value model 均跳过，card model 训练成功 examples=17，learned memory applied=3。A0 目标继续保持，不进入 A4。

- 运行结果：第 1 盘清除 Slime Boss 和 Collector，推进到 Act3 F37 三大颚虫后死亡，归因 `combat_planning`；第 2 盘死于 Guardian，归因 `potion_planning`；第 3 盘死于 Guardian，归因 `deck_quality`。
- Act1 boss gate：`reached=3/3`，`cleared=1/2`，`pristine_cleared=0/2`，状态仍为 `NEEDS_WORK`，下一批继续 `improve_act1_boss_combat`。
- 标签审计：combat_search labels=152，排除 3 条 `missed_single_card_search`；当前 policy replay 3 条中 `matches_actual=1`、`other=2`。这些标签均来自 `usable_with_recoveries`，不能直接当 pristine combat value 训练源。
- live MCP 质量：本轮有 unavailable action、invalid command、hand-select preflight 等 recovered action race，Act1 Boss clear 因 prefix recovered action race 不算 pristine。执行器 metadata 仍需复盘，但没有 infra_blocked。

行为来源复盘：

- 启发式/搜索影响：所有 live 出牌、路线、药水、商店和休息仍由 policy、route risk、potion tempo、one-turn search 控制。第 1 盘 F13 复制药水+Cleave、F23 Book of Stabbing 的 Disarm/Flame Barrier/Bludgeon 序列、F33 Collector 的 Swift/Gambler's Brew 使用、第 2 盘低血休息、第 3 盘连续休息和商店买 Spot Weakness，均为现有控制层行为。
- 复盘修复影响：`_12` 后低血 Act2 慢速 POWER 商店折价方向得到部分验证。第 1 盘 F19 满血买药水、不买慢速牌；F28 三奴隶满血时能力药水给 Dark Embrace，这属于成型 Corruption/FNP 套件的正向场景，和 `_12` 低血买 Dark Embrace 的负面场景不同。
- 学习/模型影响：本轮 `model_authority=assist` 仍仅作用于卡牌奖励边界，未发生 combat value live 接管。第 1 盘 Corruption/FNP/Snecko Eye/Bludgeon/Flame Barrier 套件推进到 Act3，是卡牌组合正样本；第 3 盘 F1 过早拿 Feel No Pain、缺 exhaust payoff，导致 F4 史莱姆战拖到 T15，是慢速 engine 早期负样本。
- 外部数据影响：外部数据仍未进入 runtime、gate 或 pristine 训练，只作为 reference/scratch/pretraining 计划保留。

关键复盘结论：

- 第 1 盘证明当前系统可以从 Act1 劣势牌组恢复到 Act3，并能清除 Collector；主要新瓶颈是 Act3 多目标高压，例如 F37 三大颚虫，Reaper/防御/击杀顺序没有形成稳定生存线。
- 第 2、3 盘说明 Act1 boss gate 仍不稳。第 2 盘 boss relic swap 后早期血线被普通战和 Nob 链打穿，33/90 进 Guardian；第 3 盘虽然 80/80 进 Guardian，但 FNP 未成型、输出/防御曲线拖长，最终死于 T16。
- Feel No Pain / Dark Embrace 不能用单一好坏判断。成型 Corruption/FNP/Snecko 套件中它们很强；但 Act1 floor 1-5、没有 exhaust enabler 或 Corruption 时，过早拿 Feel No Pain 会拖普通战节奏，导致后续低血。
- Guardian 长战仍暴露“本回合避免死亡”奖励掩盖未来缓冲的问题；第 3 盘多次靠防御线活过当前回合，但输出不足导致血线从 80/80 慢慢被磨到 0。

下一轮重点：

- 继续 A0 `_14`，不提升模型 live authority。
- 代码优先级：加强 card reward 对早期 unsupported exhaust payoff 的折价，尤其 Act1 前 5 层没有 exhaust enabler / Corruption 时的 Feel No Pain；保留成型套件中 FNP/Dark Embrace 的价值，不做全局禁用。
- 复盘优先级：检查 `_13` 三条 `missed_single_card_search`，特别是 Slime Boss 分裂后 Bludgeon 标签和 Guardian Clothesline 标签，区分真正搜索漏选与药水/手牌/执行顺序导致的 replay mismatch。

_13 复盘后的卡牌奖励层修复：

- `policy_card_reward` 新增早期 unsupported exhaust payoff 折价：Act1 早期、牌组没有 True Grit / Burning Pact / Corruption 等 exhaust enabler 时，Feel No Pain / Dark Embrace 这类 payoff 在已有 unsupported 惩罚基础上继续降权。
- 该修复来自 `_13` 第 3 盘：F1 拿 Feel No Pain 后缺少 exhaust payoff 支撑，F4 酸液史莱姆战拖到 T15 并大量掉血，最终虽然 80/80 进 Guardian，仍因 deck quality 和长战曲线不足死亡。
- 修复边界：不全局禁用 Feel No Pain。若牌组已有 True Grit 等 enabler，Feel No Pain 保留高价值；第 1 盘 Corruption/FNP/Snecko Eye 套件推进到 Act3 证明成型场景必须保留。
- 回归测试覆盖：`test_early_unsupported_feel_no_pain_loses_to_frontload` 与 `test_feel_no_pain_keeps_value_with_exhaust_enabler`，并保留 card reward、policy、combat search、shop/potion/combat policy 和 climb cycle 回归。
## _14 后阶段推进

2026-07-07 的 A0 `_14` 批次没有形成可训练胜局，也没有正常战败收束：第 1 盘在 Act2 F33 第一勇士 T9 后 live MCP 断开，manifest 分类为 `infra_blocked/mcp_unreachable`，失败归因为 `mcp_execution`。本盘 74/80 进入 Slime Boss，71/80 清除 Act1 Boss，prefix pristine clear 成立；随后推进到 Act2 Boss，但在 Champ 阶段转换和 54 incoming 爆发前后暴露了 Boss 阶段计划不足。A0 目标继续保持，不进入 A4。

- 运行质量：`clean_trainable=0`，`infra_blocked=1`；run_status 显示 F33 Champ，HP 27/80，incoming 54，recovered action race 2 次，最终 `mcp_unreachable`。
- 训练结果：由于本轮是 infra-blocked，shadow/card/combat value/learned memory 均跳过，combat label audit rows=0，replay audit replayed=0。这一盘只能作为执行层/MCP 稳定性与 Boss 复盘证据，不能进入默认训练。
- 启发式/搜索影响：`_13` 后的早期 unsupported Feel No Pain 折价在本盘得到正向验证：早期选择 Anger，商店购买 Shrug It Off 和 Perfected Strike，没有再把 Act1 前几层资源押给无燃料 Feel No Pain。Slime Boss T3 的 Shrug + Perfected Strike + Anger 搜索线把 35 incoming 压到 0 并完成分裂，是 one-turn search 的正例。
- 复盘修复影响：`_12` 后的 Act2 低血慢速 POWER 商店折价也得到验证：F24 商店买 Hemokinesis 并删 Strike，没有低血买 Dark Embrace/Demon Form/Barricade 这类慢引擎。
- 学习/模型影响：`model_authority=assist` 仍只处于卡牌奖励边界；本盘没有 combat value runtime authority，也没有新的训练产物。不能把任何 live 出牌归因给神经网络接管。
- 外部数据影响：本轮没有把外部数据注入 runtime 或 gate。代码层已升级 `climb_cycle --external-prior-input`，允许在 cycle 训练阶段额外训练 scratch external card prior，并在 summary/复盘中记录 provenance、examples、model_path 和 `runtime_authority=false`。

关键复盘结论：

- 当前不应继续给早期选牌写更多启发式例外。Act1 通过质量已改善，本轮主要瓶颈转向 Act2/Act3 多敌与 Boss 阶段长期价值。
- Gremlin Leader T6 面对 45 incoming 时选择高格挡但不杀小怪，吃 21 点后才靠 T10 搜索救命，说明 one-turn search 能救急，但还不能充分惩罚“把召唤战拖长”的未来风险。
- Champ 战前半段输出过快，T7 低血压力下仍出现 Hemokinesis fallback，T9 面对 54 incoming 只有 13 block；这需要 Boss 阶段特征和长期 combat value，而不是单条硬编码规则。
- 本轮最大工程阻塞是 live MCP 在关键战斗中断开。下一轮爬塔前优先确认 MCPTheSpire 进程/端口稳定，并继续把 recovered action race 与策略失败分开记录。

_14 后外部数据训练接入边界：

- 新增 `climb_cycle --external-prior-input <external_manifest_or_rows>`；输入必须是 `external_prior_manifest` 或 `card_reward_priors.jsonl`，来源等级为 `external_prior`。
- 默认输出到 `cycle_dir/training/external_models/<source_id>/card_prior_model.json`，并写 `external_prior_training_summary.json`。
- 训练 summary 新增 `external_priors`，状态行新增 `external_prior=<status> examples=<n>`。
- 该模型明确写入 `runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`，禁止用途仍包括 `clean_trainable`、`pristine`、`gate`、`learned_memory`、`runtime_authority`。
- 后续如使用外部 prior，中文单局复盘必须写明外部来源、样本数、是否改变卡牌奖励选择；若没有 `runtime_authority=true`，不得把 live 行为称为外部数据学习结果。

## _15 后阶段推进

2026-07-07 的 A0 `_15` 批次没有达成终局胜利，但三盘均为 `clean_trainable/completed_clean`，均清除 Act 1 Boss。Act 1 boss gate 已通过：`reached=3/3`、`cleared=3/2`、`pristine_cleared=3/2`、`infra=0`。三盘都死在 Act 2，失败归因均为 `combat_planning`，说明当前瓶颈已从一层 Boss 稳定性转向二层连续战斗、普通战消耗、精英前低血缓冲和高压下慢速能力牌。

- 第 1 盘：The Guardian 后进入 Act2，F23 Book of Stabbing 死亡。关键负例是 25/85、18 incoming 时使用 Power Potion 选择 Demon Form；随后 1/85 仍面对未挡伤害时继续打 Inflame。这不是神经网络接管，而是临时卡牌选择和单卡评分仍把长期能力牌估值过高。
- 第 2 盘：The Guardian 后 40/87 进入 Act2，死于 F22 Sentry + Spheric Guardian。长回合防御能拖住部分压力，但普通战链条把血量磨穿。
- 第 3 盘：Hexaghost 后 33/80 进入 Act2，死于 F20 triple Cultists。早期双删 Strike 后输出/防御缓冲不足，二层多敌人爆发仍是主风险。
- 本轮训练：card model `trained examples=41`，learned memory `applied=3`；shadow models 与 neural combat value 因 `source_quality=pristine` 且样本为 `usable_with_recoveries` 跳过；external prior 未配置，`external_prior=skipped examples=0`。
- 标签审计：combat labels 154 条，排除 3 条；当前策略回放 2 条已匹配 label，1 条匹配实际动作。它们都来自 Guardian 单卡搜索，不是本轮 Act2 死亡主因。

行为来源复盘：

- 启发式/搜索影响：live 出牌、路线、药水、商店和休息仍由 policy、route risk、potion tempo 与 one-turn search 控制。三盘过一层 Boss 的防御/输出序列、低血休息、路线选择和药水使用属于这些执行层规则与搜索的结果。
- 学习/模型影响：`model_authority=assist` 仍只允许卡牌奖励 tie-breaker；本轮没有 combat value runtime authority，也没有神经网络直接控制 `play_card`。本轮新增的学习结果只进入 card value model 与 learned memory，作为后续卡牌奖励和复盘证据。
- 外部数据影响：本轮没有把外部数据注入 runtime、gate、learned memory 或 pristine 训练；外部数据仍只允许作为 `external_prior` scratch prior、静态 reference 或后续离线 ablation。

_15 复盘后的执行层修复：

- `policy_card_reward` 现在识别 `CARD_REWARD + room_phase=COMBAT` 的临时战斗卡选择，例如 Power Potion 三选一。临时卡选择不再写 `learn_card_pick`，避免把 Demon Form 这类一次性药水选项污染长期卡牌奖励学习。
- 临时战斗卡选择在低血/高压/致命压力下，会折扣 Barricade、Corruption、Dark Embrace、Demon Form、Inflame 这类慢速能力牌，并给 Metallicize 这类即时防御倾向的能力牌加权。该修复直接来自 `_15` 第 1 盘 Book of Stabbing。
- `policy` 的高压能力牌惩罚扩大到 lethal pressure：当当前未挡伤害已经足以致死时，非即时能力牌即使压力数值小于旧的 14 点阈值，也会被显著降权；Inflame 被纳入该边界。
- `policy_route` 新增 Act2 受伤恢复缓冲边界：当 HP 低于约 65%，当前选项是 M/E，旁边有可用商店或火堆，并且战斗线通向近端 forced elite 或恢复点太远时，M/E 会被标记为 `act2_injured_recovery_available` 并降权。该修复来自 `_15` 第 1 盘 F20 46/80、400 金仍走战斗线并被迫进入 Book of Stabbing 的证据。
- 回归覆盖：`test_temporary_power_potion_choice_avoids_slow_power_under_pressure`、`test_lethal_pressure_does_not_play_inflame_after_block`、`test_map_act2_injured_prefers_shop_over_monster_before_forced_elite_chain`。

下一轮重点：

- 继续 A0，不进入 A4；目标从 Act1 gate 转为完整终局胜利。
- 下一轮要重点观察二层普通战和 Book of Stabbing/三奴隶/三邪教徒等多段爆发场景：如果低血下仍选择慢速能力牌或普通攻击消耗防御缓冲，先看 combat search 与单卡评分；如果路线在低血时仍强迫进入精英/连续 M，再看 route risk。
- 神经网络 combat value 继续 shadow；只有当本地 A0 clean/pristine 证据稳定且中文复盘能解释其影响动作时，才讨论扩大 live 权限。

## _16 后阶段推进

2026-07-07 的 A0 `_16` 批次仍未达成终局胜利，但三盘均为 `clean_trainable/completed_clean`。Act 1 boss gate 通过：`reached=3/3`、`cleared=2/2`、`pristine_cleared=2/2`、`infra=0`，下一阶段可以继续以 A0 终局胜利为目标，不进入 A4。三盘失败归因均为 `combat_planning`，说明路线修复已经缓解了一部分低血硬走危险路线的问题，但执行层仍在二层普通战、多敌人爆发、长战血线与药水/防御时机上损失过大。

- 第 1 盘：F16 Hexaghost T14 死亡，Boss 剩 59/250；属于 Act1 Boss 长战输出/防御曲线不足。F14 已经先走商店、F15 休息到 57/80，路线选择不是主因。
- 第 2 盘：F16 The Guardian 已清，player_hp=16，随后 F18 Act2 死亡并由 MCP null 后 synthetic terminal 恢复为 `usable_with_recoveries`。标签审计只有 1 条 `missed_single_card_search` 排除；replay 显示当前策略匹配实际动作，不匹配被排除标签，因此该条不作为训练污染扩大。
- 第 3 盘：F16 Hexaghost 已清，player_hp=15；F22 Act2 对 Chosen + Cultist 死亡。F20 50/80 后路线选择商店，说明 `_15` 后的 Act2 受伤恢复路线边界生效；但 F21 商店购买 Dark Embrace、F22 T1 在 50/80 对双敌先打 Dark Embrace，随后血线被多敌人攻击压穿，属于 combat planning 和慢速 engine 时机问题。
- 本轮训练：shadow models `trained rows=451`；card model `trained examples=27`；neural combat value PyTorch MLP `trained examples=82`、`validation_examples=16`、`device=cuda`、`val_loss=0.5894`、`runtime_authority=false`；learned memory `applied=3`；external prior 未配置，`external_prior=skipped examples=0`。
- 本轮标签审计：combat labels 116 条，排除 1 条 `missed_single_card_search`。replay audit 对这 1 条给出 `current_policy_matches_actual=1`、`current_policy_matches_label=0`，所以它是已解释排除项，不应强迫模型学习。

行为来源复盘：

- 启发式/搜索影响：三盘 live 出牌仍由现有 policy、potion policy、route risk 和 one-turn search 控制。F14 Power Potion 临时选择 Dark Embrace、F16 Boss 战多次 one-turn search、F21 商店购买 Dark Embrace、F22 对 Chosen + Cultist 的出牌顺序都不是神经网络接管，而是启发式/搜索/商店评分的结果。
- 学习/模型影响：`model_authority=assist` 仍只在卡牌奖励边界允许有限 tie-breaker；本轮没有 `runtime_authority=true` 的战斗动作接管。card model、shadow models、combat value MLP 都是训练产物或后续复盘证据，不能把 live `play_card` 归因于神经网络。
- 外部数据影响：本轮没有配置 `--external-prior-input`，外部数据没有进入 runtime、gate、learned memory 或 pristine 训练；外部数据仍只作为 source catalog、scratch prior 和离线 ablation 候选。

_16 复盘后的项目升级：

- `train_combat_value_model` 新增 `CombatValueScorer`，可懒加载 `combat_value_model.pt` 并对 combat search row 做推理。
- `policy` 在 one-turn search 决策 metadata 中记录 `search.combat_value_shadow`：包含 `status`、`path`、`predicted_value`、`runtime_authority=false`。这让下一轮真实爬塔日志能看到神经网络对搜索线的 shadow 价值判断，但不会改变动作。
- 该接入是“模型训练与 shadow 推理接入”，不是“模型接管执行层”。真正扩大权限前，必须先用 A0 clean/pristine live 日志证明 shadow 预测能解释或改善 combat planning。
- `_16` 训练出的 `card_value_model.json`、`combat_search_model.json`、`combat_value_model.pt`、`deck_quality_model.json`、`potion_tempo_model.json`、`route_risk_model.json` 已提升到默认 `models/`，用于下一轮验证批。该提升没有重复 replay learned memory；combat value 的默认用途仍是 shadow metadata。
- 回归覆盖：`test_combat_search_records_combat_value_shadow_score_without_authority`，并与 `tests.test_policy`、`tests.test_train_combat_value_model`、`tests.test_climb_cycle`、combat label audit/replay tests 一起通过。

下一轮重点：

- 继续 A0 真实爬塔，观察每条 one-turn search 是否写入 `combat_value_shadow`，并在单局复盘中比较 predicted value、实际血线与死亡原因。
- 不再扩大启发式策略投入；如果继续死在 F18-F23 多敌人战，优先把这些局面转成 combat value / future-loss 训练目标，而不是继续补单点规则。
- 暂不把 `_16` scratch 模型自动提升到全局 runtime 权限；只有在下一验证批中 shadow 预测稳定、复盘能解释其收益后，才考虑从 shadow 升到更窄的 pilot/assist combat tie-breaker。

## _17 中断复盘

2026-07-07 的 A0 `_17` 批次启动后只形成 1 条日志：`runs/ai_runs_climb_cycle_ironclad_a0_win_17/20260707_081023_ironclad_a0.jsonl`。该局推进到 Act3 F46，已清除 Slime Boss、Collector、Act2 精英 Gremlin Leader 和 Act3 精英 Reptomancer，但在 F46 事件处 MCP 服务断连，最终为 `infra_blocked/mcp_unreachable`。这不是策略失败，也不是终局胜利；本批 `shadow/card/combat_value/learned_memory` 全部 skipped。

- 本局的价值：首次确认 live JSONL 写入 `search.combat_value_shadow`，共 54 条 shadow prediction，均为 `runtime_authority=false`。神经网络已经进入真实爬塔观察层，但没有接管动作。
- 不能用于默认训练：manifest 将本局标为 `infra_blocked`，`combat_label_audit` rows=0；这局只能人工复盘，不能计入 clean/pristine、gate 或 learned memory。
- 强正样本：F23 Gremlin Leader 高压战、F40 Reptomancer avoided-lethal 搜索、F42 多敌人战的控损线，适合下一批 clean 日志中继续验证 combat value。
- 强负例/观察点：F33 Collector 26 incoming 下打 Demon Form、F28 低血普通战先打 Dark Embrace、F45 Darklings 低血靠 Reaper/Whirlwind 勉强求生。它们目前只能作为人工观察，因为本局 infra-blocked。
- 下一步：先恢复 MCP 执行层，再开 `_18` A0。不要因为 `_17` 的 infra-blocked 结果修改策略；继续让 `combat_value_shadow` 收集 clean/pristine 对照证据。

## _18 后阶段推进

2026-07-07 的 A0 `_18` 批次已完成 3 局，全部为 `clean_trainable/completed_clean`，无 infra-blocked。三局均到达 Act1 Boss，只有第 2 局清除 Slime Boss 后死于 F33 Champ；Act1 boss gate 仍未通过：`reached=3/3`、`cleared=1/2`、`pristine_cleared=0/2`，下一步仍是 `improve_act1_boss_combat`，目标保持 A0 终局胜利，不进入 A4。

- 第 1 局：Boss relic swap 后以 Perfected Strike/攻击包到达 F16 Hexaghost，最终死亡，失败归因 `deck_quality`。Boss 前路线和商店不是主问题，长战防御/输出曲线不足。
- 第 2 局：清除 F16 Slime Boss，62/80 进入 Act2，连续经过 Slavers、Chosen/Byrd、Snake Plant、Centurion/Mystic、Shell Parasite/Fungi，最终 F33 Champ 死亡，失败归因 `combat_planning`。这是 Act2 未来血线和 Boss 前资源评估的重点样本。
- 第 3 局：Shockwave 起手，拿到 Second Wind、Offering、Flame Barrier、Fiend Fire，63/80 进入 F16 Slime Boss，但分裂后长战被持续攻击压到 0，失败归因 `combat_planning`。它是 Act1 Boss future-loss 的重点负样本。
- 本轮训练：card model `trained examples=35`，learned memory `applied=3`；shadow models `skipped rows=0`，neural combat value `skipped examples=0`，原因是默认 `source_quality=pristine` 不接收本轮 `usable_with_recoveries` 证据；external prior 仍 `skipped examples=0`。
- 本轮 card model 保留在 cycle scratch，不提升到默认 `models/`。原因是 Act1 boss gate 未通过，且多个强牌在失败局中会被相关性负向打分；现阶段只把它作为复盘和下一轮对照材料，避免把失败相关性误当成因果。
- 本轮 live shadow：三局共记录 143 条 `search.combat_value_shadow`，分别为 13、81、49 条，全部 `runtime_authority=false`。这证明神经网络已经进入真实爬塔观察层，但本轮没有接管战斗动作。
- 数据质量：shadow feature rows=587，combat labels=147，label exclusions=4，排除原因均为 `missed_single_card_search`；这些排除项应继续走审计/replay，而不是强行进入训练。

行为来源复盘：

- 启发式/搜索影响：路线、商店、药水、休息、卡牌奖励基础评分、one-turn search 和 avoided-lethal search 仍是 live 行为主体。第 2 局 Act2 多次 avoided-lethal 能保命但不能解决未来血线；第 3 局 Slime Boss 分裂后多次短期保命仍缺少胜势规划。
- 学习/模型影响：`model_authority=assist` 只影响卡牌奖励边界；`search.combat_value_shadow` 只写预测元数据。三局没有任何 `runtime_authority=true`，因此不得把过 Slime Boss或死亡原因归为神经网络接管。
- 外部数据影响：本轮没有 `--external-prior-input`，外部数据没有进入 runtime、gate、learned_memory、pristine 或 neural combat value 训练；外部来源继续只作 `external_reference`、scratch prior 或离线 ablation。

下一轮重点：

- 继续 A0 live MCP 爬塔，保持 `model_authority=assist`，但不扩大 combat runtime 权限。
- 优先把 `_18` 的 Act1 Boss/Act2 高压战样本转成 future-loss/combat-value 训练目标；在 clean/pristine 证据足够前，不新增启发式例外。
- 复盘要继续逐局写明：哪些动作来自旧 policy/search，哪些只是模型 shadow 观察，哪些才是真正学习结果。

## _19 中断复盘

2026-07-07 的 A0 `_19` 批次在第 1 局 F16 The Guardian T14 中断：玩家 48/85，Boss 20/240，MCPTheSpire 端口 8080 拒绝连接。该局分类为 `infra_blocked/mcp_unreachable`，训练全部 skipped：shadow rows=0、card examples=0、combat value examples=0、learned memory applied=0、external prior examples=0。

- 不能计入 gate：本局 Act1 Boss 已 reached，但未能形成 clean/pristine clear；gate next action 转为 `fix_execution_layer`。
- 不能训练：该日志不进入 learned_memory、card model、shadow model、combat value model，也不能用来证明策略失败。
- 有人工观察价值：它在 Guardian 战前缀较强，Uppercut + Shockwave + Whirlwind + Shrug It Off + Flame Barrier，且有 15 条 `search.combat_value_shadow`，全部 `runtime_authority=false`。
- 执行层处理：已重启 ModTheSpire/MCPTheSpire，新的 Java PID 为 420712；`mcp_watchdog --attempts 5 --delay 2 --recover-terminal --json` 返回 `healthy`、MAIN_MENU。

下一步：继续 A0 `_20` live 爬塔前，先承认 `_19` 是基础设施中断，不做策略/模型权重更新。若 MCP 再连续中断，应优先由 engineering_agent 处理端口/状态序列化稳定性，而不是继续消耗训练批次。

## _20 诊断复盘

2026-07-07 的 A0 `_20` 批次由主代理手动中止在第 1 局 F24 SHOP_SCREEN。该局已清除 Act1 Hexaghost，41/80 进入 Act2，并在 F23 休息、F24 商店恢复到较可继续的状态；但商店界面从 step 311 起重复尝试购买同一瓶敏捷药水，直到 step 323 仍未推进。为避免空转到 max-step，主代理中止了进程。

- 分类：`diagnostic_excluded/no_terminal_outcome`，不是训练局，也不是终局胜利。
- Gate 价值：Act1 Boss reached/cleared/pristine 证据存在，offline gate 接受该 boss clear 片段，但整局不能作为 clean terminal outcome。
- 训练状态：未运行 climb_cycle training；该日志不应进入 learned_memory、card model、shadow model 或 combat value 训练。
- 模型观察：37 条 `search.combat_value_shadow`，13 次 card reward `model_authority`，全部 `runtime_authority=false`。
- 行为来源：Hexaghost 清除、Act2 route recovery、F18 Byrds search 线来自旧 policy/search；F24 shop loop 是执行层状态同步/购买可用性问题。
- 外部数据：未使用 external prior，外部数据没有影响本局。

下一步：暂停继续扩跑，先修 `SHOP_SCREEN` 重复购买同一药水的问题。修复方向应是执行层而不是策略层：购买后重新读取商店状态、确认 potion slot/gold/item 是否变化、对同一 shop item 增加本房间 attempted guard，或在重复同一购买 N 次后 leave shop。修复后再开 `_21` A0 live。

_20 复盘后的执行层修复：

- `runner` 新增 repeated shop purchase guard：同一楼层、同一 `choice_index`、同一 `Shop buy ...` reason 连续重复 3 次后，强制执行 `cancel` 离店，避免 stale SHOP_SCREEN/inventory snapshot 空转到 max-step。
- 该修复只作用于商店执行层，不改变路线、战斗、卡牌奖励或模型权重；它不会把 `_20` 的 diagnostic log 转成训练样本。
- 回归覆盖：`test_repeated_shop_purchase_guard_leaves_stale_shop_loop`，并与 `tests.test_policy_shop`、combat value shadow runtime-authority 测试、`tests.test_train_combat_value_model` 一起通过。

## _21 诊断与修复复盘

2026-07-07 的 A0 `_21` 批次在第 3 局 F17 GRID 处由主代理中止。批次共有 3 条日志：2 条 `clean_trainable/completed_clean`，1 条 `diagnostic_excluded/no_terminal_outcome`；Act1 Boss reached=3，cleared=1，pristine_cleared=1，仍未达成 A0 终局胜利，也没有通过本阶段 gate。继续目标保持 A0，不进入 A4。

- 第 1 局：F16 Hexaghost 死亡，`validation_grade=usable_with_recoveries`，recovered action 1，失败归因 `combat_planning`。该局可作为 clean_trainable 失败样本，但不能作为 pristine boss clear。
- 第 2 局：F16 The Guardian 死亡，`validation_grade=pristine`，recovered action 0，失败归因 `potion_planning`。该局是 `_21` 中最干净的 Boss 失败样本，combat label 有 1 条 `missed_single_card_search` 排除项，应继续 replay audit。
- 第 3 局：F16 The Guardian 已清，22/80 进入 F17；Astrolabe/星盘 GRID 选择后卡在多选确认循环，连续 64 次尝试不可用的 `confirm`，MCP 只暴露 `choose`。该局分类为 diagnostic，不训练。
- 本轮离线样本：shadow feature rows=178，route_risk=30，potion_tempo=92，pre_boss_deck_quality=2，combat_search_labels=54；其中 combat search source_quality 为 pristine 22、usable_with_recoveries 32。
- 本轮 live 记录 90 条 `search.combat_value_shadow`，全部 `runtime_authority=false`。这证明神经网络仍在真实爬塔观察层，而不是执行层控制器。

行为来源复盘：

- 启发式/搜索影响：三局 live 行为仍由 policy、route risk、potion tempo、shop/rest/card reward 评分和 one-turn search 控制。Boss 战死亡、Guardian clear、Boss relic 选择与 GRID 操作都不是神经网络接管。
- 学习/模型影响：`model_authority=assist` 仍只允许卡牌奖励边界的有限辅助；combat value MLP 只写 `search.combat_value_shadow`。本轮没有 `runtime_authority=true` 的战斗动作。
- 外部数据影响：本轮未配置 `--external-prior-input`，外部数据没有进入 runtime、gate、learned_memory、pristine 或 neural combat value 训练。网上数据集只作为文档化 reference/scratch prior 方向。

_21 复盘后的执行层修复：

- `policy_rest` 的 GRID 决策改为按 `num_cards` 判断多选是否完成；多选未满时继续选择，而不是只因 `selected_cards` 非空就确认。
- GRID 选择现在会跳过已选 uuid 和同名 card key，避免重复选择同一张或同类已选卡。
- 星盘这类非事件多选变牌被视为“选择低价值卡去变掉”，不会再优先把 Offering+ 这类高价值牌送去变牌。
- 单选已满足的 bottle/类似场景仍会确认，保持旧边界。
- 现场只读校验：当前 F17 GRID 修复后的下一步决策为 `choose`，不再是 `confirm`。
- 回归覆盖：`tests.test_policy_rest` 以及事件 GRID、重复商店购买 guard、combat value shadow 相关局部测试通过。

下一轮重点：

- 开 `_22` A0 live 前可直接使用 `--existing-save abandon` 清掉 `_21` 诊断局，避免污染训练。
- 继续收集本地 MCP clean/pristine 终局样本；不因 `_21` 继续添加战斗启发式例外。
- 如果 `_22` 仍死在 Act1 Boss，优先处理 potion_planning、combat_search_priority 和 combat_value/future-loss 训练目标；如果进入 Act2 后失败，再回到 Act2 普通战和资源曲线复盘。

## _22 后阶段推进

2026-07-07 的 A0 `_22` 批次完成 3 局，全部为 `clean_trainable/completed_clean`，无 `diagnostic_excluded`，无 `infra_blocked`。A0 终局胜利仍未达成。Act1 Boss gate 状态为 `NEEDS_WORK`：`reached=3/3`、`cleared=2/2`、`pristine_cleared=1/2`，下一步是 `collect_pristine_act1_boss_clears`，还需要 1 个 pristine Act1 Boss clear。

- 第 1 局：F16 Hexaghost T14 死亡，Boss 剩 15/250；`validation_grade=pristine`，失败归因 `deck_quality`。这是高价值 pristine Boss 负样本。
- 第 2 局：F16 Guardian cleared，24/80 进入 Act2，最终 F25 死亡；`validation_grade=usable_with_recoveries`，失败归因 `combat_planning`。Act1 clear prefix pristine，可作为 gate clear 证据，但整局因 synthetic terminal 不能进默认 pristine shadow 训练。
- 第 3 局：F16 Guardian cleared，24/80 进入 Act2，最终 F20 死亡；`validation_grade=usable_with_recoveries`，失败归因 `route_risk`。一次 recovered action race 污染 Act1 prefix，因此不能补 pristine clear。
- 本轮训练：shadow models `trained rows=173`；card model `trained examples=32`；neural combat value PyTorch MLP `trained examples=25`、`validation_examples=5`、`device=cuda`、`val_loss=1.2711`、`runtime_authority=false`；learned memory `applied=3`；external prior skipped。
- 标签审计：combat labels 115，排除 3 条，均为 `missed_single_card_search`；没有 `missed_direct_kill`。这些排除项用于 replay/audit，不强行喂给训练。

行为来源复盘：

- 启发式/搜索影响：路线、Boss 前休息/升级、战斗出牌、药水使用、direct lethal 与 one-turn search 仍是 live 行为主体。F15/F13 的休息来自 Act1 风险边界；F23 56/80 仍 smith、F19 35/80 仍走战斗线属于 route/rest 风险样本。
- 学习/模型影响：`model_authority=assist` 有一次明确卡牌奖励接管：第 2 局 F7 `Limit Break` 通过 `model_authority_tiebreaker` 压过启发式第一候选 `Dropkick`。除此之外，combat value 仍只写 `search.combat_value_shadow`，没有 `play_card` runtime 接管。
- 外部数据影响：本轮没有 `--external-prior-input`，`external_prior=skipped examples=0`；外部数据没有进入 runtime、gate、learned_memory、pristine 或 neural combat value 训练。

_22 后的执行层/学习结论：

- `_21` 的 GRID/Astrolabe 修复在 `_22` 的升级、删牌、Boss relic、CHEST/GRID 流程中未再复现循环，执行层更稳定。
- 第 3 局的 potion recoverable action race 说明 pristine gate 仍会被单次可恢复执行错误污染；下一轮主目标不是扩策略，而是继续收集无 recovery 的 Act1 Boss clear。
- Act2 风险成为下一层主要瓶颈：第 2 局 F23 56/80 smith 后进入危险战斗链，第 3 局 F19 35/80 仍走战斗线到 F20 Snake Plant。后续应优先评估 Act2 rest/route risk，而不是扩大 combat 神经网络权限。

下一轮重点：

- 继续 `_23` A0 live，目标窄化为补足 1 个 pristine Act1 Boss clear，同时保持 A0 终局胜利为总目标。
- 保持 combat value `runtime_authority=false`。只有 card reward assist 继续在受限 tie-breaker 内工作，并且每次实际接管都必须写入复盘。
- 外部数据仍只做 reference/scratch prior，不进入 gate 或执行层。

## _23 后阶段推进

2026-07-07 的 A0 `_23` 批次完成 3 局真实 MCP 爬塔，全部为 `clean_trainable/completed_clean`，无 diagnostic、无 infra-blocked。A0 终局胜利仍未达成。Act1 Boss gate 状态仍为 `NEEDS_WORK`：`reached=2/3`、`cleared=2/2`、`pristine_cleared=2/2`，说明 Act1 Boss clear 质量已经够，但本批第 3 局 F10 早死导致 reached 数不足，下一步变成 `improve_route_and_early_act1_survival`。

- 第 1 局：F16 Slime Boss 已清，19 HP 进入 Act2，最终 F30 死于 Shelled Parasite，`validation_grade=pristine`，失败归因 `route_risk`。这是 pristine Act1 clear 后进入 Act2 长链风险的强样本。
- 第 2 局：F16 Slime Boss 已清，55 HP 进入 Act2，最终 F22 死亡，`validation_grade=pristine`，失败归因 `combat_planning`。这是本批最干净的 Act1 正样本和 Act2 战斗负样本。
- 第 3 局：F6 走 Sentries 后降到 19 HP，F8 休息到 43，F10 死于 Gremlin Gang；`validation_grade=usable_with_recoveries`，失败归因 `route_risk`，没有到达 Act1 Boss。它把下一阶段重点从 Boss 门槛转回早期路线/精英风险。
- 本轮训练：shadow models `trained rows=251`；card model `trained examples=29`；neural combat value PyTorch MLP `trained examples=100`、`validation_examples=20`、`device=cuda`、`val_loss=0.7697`、`runtime_authority=false`；learned memory `applied=3`；external prior 在本批 cycle 中仍 `skipped examples=0`。
- 标签审计：combat labels 147，排除 4 条，均为 `missed_single_card_search`；没有把这些 replay mismatch 强行喂给训练。

行为来源复盘：

- 启发式/搜索影响：三局 live 行为仍主要来自 route policy、rest/shop/card reward 评分、direct lethal、one-turn search 和 execution guards。第 1 局 F30 Act2 长链、第 2 局 F22 战斗计划、第 3 局 F6 Sentries 路线都是现有控制器和搜索结果，不是神经网络接管。
- 学习/模型影响：`model_authority=assist` 仍只在卡牌奖励边界生效；combat value MLP 继续作为 `search.combat_value_shadow` 和训练产物，`play_card` 没有 `runtime_authority=true`。本轮最有价值的学习信号是“两个 pristine Act1 clear + 一个 F10 route-risk 早死”的对照，而不是新启发式规则。
- 外部数据影响：`_23` 批次没有配置 `--external-prior-input`，因此外部数据没有影响本批 live 动作、gate、learned memory、pristine 训练或神经网络 combat value。

_23 后外部数据训练接入：

- 已导入 GitHub `MaT1g3R/Slay-the-Spire-data` 的 `runs/200-rotating-sample/IRONCLAD` 原始 `.run` 小样本，source_id 为 `mat1g3r_200_rotating_ironclad`。
- 导入结果：50 局 Ironclad run accepted、0 rejected、889 条 `external_prior` card reward rows；样本 ascension 为 A20，导入权重 `source_weight=0.05`。
- 训练结果：`data/external_models/mat1g3r_200_rotating_ironclad/card_prior_model.json`，889 examples、151 deltas、`prior_scale=0.20`、`max_delta=2.0`。
- 权限边界：该模型是 scratch external card prior，`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`，不进入 `clean_trainable`、`pristine`、A0 gate 或 `learned_memory`。
- 由于外部样本是 A20，而当前目标是 A0 终局胜利，下一轮只能把它作为 cycle 训练阶段的隔离对照或低权重 card prior 候选，不能让它直接改变路线、药水、商店或战斗执行层。
- 已额外运行 `cycle_20260707_ironclad_a0_win_23_external_replay`：对 `_23` 三条本地 MCP 日志做 `--skip-live` 回放训练，并传入 `--external-prior-input data\external\sts_runs\mat1g3r_200_rotating_ironclad\external_manifest.json`。该 replay cycle 输出 `external_prior=trained examples=889`，证明外部 prior 已接入训练阶段；由于是回放训练，它没有改变任何 `_23` live 动作，也不构成 A0 胜利证据。

下一轮重点：

- 继续 A0 live，目标从“补 pristine Act1 clear”转为“避免 F6-F10 早期路线/精英风险，同时推进到终局”。
- 下一次 `climb_cycle` 可以显式加入 `--external-prior-input data\external\sts_runs\mat1g3r_200_rotating_ironclad\external_manifest.json`，但只用于训练阶段 summary 和 scratch external model，不给 live MCP 接管权限。
- 复盘继续逐局写明：哪些动作来自旧启发式/搜索，哪些来自本地学习模型，哪些只是外部 prior 的离线背景；如果外部 prior 没影响 live，就必须明确写“未影响本局动作”。

## _24 后阶段推进

2026-07-07 的 A0 `_24` 批次完成 3 局真实 MCP 爬塔，全部为 `clean_trainable/completed_clean`，无 diagnostic、无 infra-blocked。A0 终局胜利仍未达成。Act1 Boss gate 仍为 `NEEDS_WORK`：`reached=2/3`、`cleared=1/2`、`pristine_cleared=1/2`，top failure 为 `route_risk:2`，另有 1 局 Hexaghost `deck_quality` 失败。下一步仍是 `improve_route_and_early_act1_survival`，但第 3 局已经证明强前缀可稳定过 Guardian 并进入 Act2。

- 第 1 局：F16 Hexaghost 死亡，Boss 剩 46/250，`validation_grade=pristine`，失败归因 `deck_quality`。本局满血高上限进 Boss，但 T14 死亡，说明 Feed/Seeing Red/Ghostly Armor/Clothesline/Carnage 线仍不足以支撑 Hexaghost 长战输出与防御。
- 第 2 局：F8 Sentries 死亡，`validation_grade=pristine`，失败归因 `route_risk`。F6 Lagavulin 后 36/80，F7 休息到 60/80，F8 被迫进 Sentries，说明早期连续精英链风险仍会压垮牌组。
- 第 3 局：F16 Guardian 已清，47/88 后 Tiny House 回到 93/93 进 Act2，最终 F28 Centurion/Mystic 死亡，`validation_grade=pristine`，失败归因 `route_risk`。这是本轮最强样本：Act1 双精英 + Guardian clear 成功，但 Act2 连续普通战和低血商店无恢复把资源链打穿。
- 本轮训练：shadow models `trained rows=548`；card model `trained examples=23`；neural combat value PyTorch MLP `trained examples=135`、`validation_examples=27`、`device=cuda`、`val_loss=0.9059`、`runtime_authority=false`；learned memory `applied=3`；external prior `trained examples=889`。
- 外部 prior 来源仍是 `mat1g3r_200_rotating_ironclad`，cycle 内模型位于 `data/climb_cycles/cycle_20260707_ironclad_a0_win_24/training/external_models/mat1g3r_200_rotating_ironclad/`，`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。
- 标签审计：combat labels 135，排除 2 条：`missed_direct_kill=1`、`missed_single_card_search=1`。回放审计结果为 2 条均可解释：`matches_label=1`、`matches_actual=1`、`other=0`，因此先记录为训练排除项，不改 live 策略。

行为来源复盘：

- 启发式/搜索影响：三局 live 动作仍来自 route policy、card/rest/shop/potion policy、direct lethal、one-turn search 和执行保护。第 2 局 F6-F8 连续精英链、第 3 局 F27 低血商店后 F28 被迫进战斗，都是路线/资源图上的旧控制器结果。
- 学习/模型影响：`model_authority=assist` 仍只限卡牌奖励边界；combat value 继续作为 shadow training/prediction，`play_card` 没有 `runtime_authority=true`。本轮本地学习最有价值的信号是：强 Act1 前缀能过 Guardian，但 Act2 连续普通战的 future-loss 仍评估不足。
- 外部数据影响：本轮显式传入 `--external-prior-input`，外部 prior 已参与训练阶段 summary 和 scratch external card prior；但没有写入 default runtime，没有进入 A0 gate、`pristine` 判定、`learned_memory` 或战斗执行层。三局 live 选择不能归因于外部 A20 样本。

下一轮重点：

- 不继续扩展启发式规则；优先把 `_24` 的 F6-F8 精英链、F24-F28 Act2 资源链和 Hexaghost 长战样本转成 route/combat value/future-loss 学习证据。
- 下一轮仍可带 `--external-prior-input`，但只用于 cycle 训练阶段；若复盘中出现外部 prior 与本地 MCP 证据冲突，优先本地 pristine MCP 证据。
- 运行前可先审计 static knowledge gaps，尤其 `Bloodletting`、`Bag of Preparation`、`Tiny House`、`Darkstone Periapt` 等未知实体；这会改善 route/potion/combat feature 覆盖，但不应被当作新的启发式策略。

## _24 后训练管线升级

2026-07-07 已把外部数据接入从独立 prior 推进到训练阶段的外部融合候选。代码行为：

- `climb_cycle` 先用本地 MCP clean/pristine manifest 训练 `card_model`。
- 再用 `external_prior_manifest` 训练隔离的 `external_card_prior`。
- 如果两者都成功，额外写出 `training/models/card_value_model_external_prior_blend.json`。
- 该融合候选默认使用 `--external-prior-blend-scale 0.25`、`--external-prior-blend-max-delta 0.75`，并在 metadata 中记录来源、权重、`requires_audited_promotion=true`。

已验证 `cycle_20260707_ironclad_a0_win_24_external_blend_replay`：对 `_24` 的 3 条真实 MCP 日志做 replay 训练，输出 `external_prior=trained examples=889 external_blend=trained`。这不是新爬塔，不改变 `_24` 的 live 行为；它只证明训练阶段能产生“本地学习 + 外部 A20 低权重 prior”的候选模型。

边界必须继续写进每篇复盘：

- 启发式/搜索：仍负责 live route、shop、rest、potion、combat action。
- 本地学习：`card_model`、shadow models、combat value shadow、learned memory 只来自本地 MCP clean/pristine 或允许质量的日志。
- 外部数据：只参与 `external_prior` 和 `card_external_prior_blend` 训练候选；`runtime_authority=false`，不控制 live MCP，不进入 A0 gate、`pristine` 或 `learned_memory`。

同日已修复 `_24` 暴露的静态知识缺口：`Bloodletting`、`Sever Soul`、`Infernal Blade`、`Searing Blow`、`Bag of Preparation`、`Tiny House`、`Darkstone Periapt`。复扫 `_24` 三条日志的 static knowledge gaps 后结果为 `missing=0`。这是特征覆盖改进，不是新增启发式策略。

## _25 后阶段推进

2026-07-07 的 A0 `_25` 批次完成 3 局真实 MCP 爬塔，A0 终局胜利仍未达成。三局都到达 Act1 Boss，但都没有 cleared：Slime Boss、Hexaghost、The Guardian 各失败一次。Gate 状态为 `NEEDS_WORK`，下一步从 `_24` 的早期路线转为 `improve_act1_boss_combat`；top failure 为 `potion_planning:2`，另有 `deck_quality:1`。

- 第 1 局：F16 Slime Boss，`validation_grade=pristine`，失败归因 `potion_planning`。满血进 Boss，分裂后 T15 死亡，说明无药水/分裂后多目标损血仍评估不足。
- 第 2 局：F16 Hexaghost，`validation_grade=usable_with_recoveries`，失败归因 `deck_quality`。MCP null 后 synthetic terminal recovery 成功；本局有一次真实 card-reward assist 接管：F14 选择 `Brutality`，启发式第一候选为 `Armaments`。
- 第 3 局：F16 Guardian，`validation_grade=usable_with_recoveries`，失败归因 `potion_planning`。F14 Nob 后 43/80，F15 rest 到 67/80 进 Boss，Guardian T19 前后死亡；本局最接近 Boss clear。
- 本轮训练：shadow models `trained rows=93`；card model `trained examples=11`；neural combat value `trained examples=37`、`runtime_authority=false`；learned memory `applied=3`；external prior `trained examples=889`；external blend `trained deltas=151`。
- 标签审计：combat labels 127，排除 2 条，均为 `missed_single_card_search`；replay 结果 `matches_label=1`、`matches_actual=0`、`other=1`。其中 Guardian T14 回放为 `confirm`，需要作为 combat search/GRID 上下文审计，而不是直接训练。

行为来源复盘：

- 启发式/搜索影响：三局 live route、shop、rest、potion、combat action 仍由旧控制器、direct lethal、one-turn search 和 execution guard 控制。F15 rest、Boss 中 search、分裂/Mode Shift 回合均不是外部数据接管。
- 学习/模型影响：只有第 2 局 F14 的 `Brutality` 是真实 `model_authority=assist` 卡牌奖励接管；它需要单独审计，因为自伤 power 可能加重 Boss 长战失败。combat value MLP 仍是 shadow，不控制 `play_card`。
- 外部数据影响：`mat1g3r_200_rotating_ironclad` A20 prior 参与赛后 external prior 与 external blend 训练；`runtime_authority=false`，不进入 gate、`pristine`、`learned_memory` 或 live MCP。本轮三局动作不能归因于外部 prior。

_25 后已补静态知识缺口：`Doubt/疑虑`、`Brutality/残暴`、`Cleave/顺劈斩`、`War Paint/战纹涂料`、`Pantograph/缩放仪`、`Mercury Hourglass/水银沙漏`。复扫 `_25` 三条日志后 `static_knowledge_gaps logs=3 missing=0`。这是特征覆盖改进，不是策略规则。

下一轮重点：

- 先围绕 `improve_act1_boss_combat`，聚焦 Boss 长战、药水保留/使用、Slime Boss 分裂后多目标损血、Guardian Mode Shift 后续回合 future-loss。
- 审计 `Brutality` 这类自伤慢引擎在 Act1 Boss 前后的 card reward assist；如连续负样本，应收紧 assist 阈值或转回 shadow。
- 不扩展外部数据权限。external blend 只做离线候选，下一轮复盘继续区分本地学习、启发式/搜索、外部 prior。
