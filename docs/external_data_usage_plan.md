# 外部数据使用决策

更新时间：2026-07-07。当前目标仍是 **A0 本地 MCP 终局胜利**。外部数据只帮助模型成长，不替代真实爬塔。

## 最终决策

外部数据分三条泳道使用：

1. `external_reference`：静态事实与语义先验。
   - 代表来源：Hugging Face `slaythespire-codex`、卡牌/遗物元数据。
   - 用途：补静态知识、卡牌相似度、卡牌/遗物标签、文本 embedding。
   - 不产生胜负标签，不进入 A0 gate。

2. `external_run_history`：局级/楼层级历史结果。
   - 代表来源：77M metrics dump、MaT1g3R run history、Run History Plus 本机历史。
   - 用途：卡牌奖励先验、遗物/路线/楼层死亡率、boss 前 deck quality、长期胜率统计。
   - 不训练逐动作执行，不计入 `clean_trainable` / `pristine`。

3. `external_action_trace`：逐动作轨迹或采集工具。
   - 代表来源：runlogger；未来也可以接入我们自己采集的 runlogger 日志。
   - 用途：未来执行层 imitation / value 模型的候选数据格式。
   - 当前只做 schema 对齐和离线评估；在本地 MCP 执行层稳定前，不接管 live。

## 数据源到用途

| 来源 | 第一用途 | 第二用途 | 禁止用途 |
| --- | --- | --- | --- |
| 77M metrics dump | Ironclad A0/A1 样本的卡牌奖励先验、楼层死亡风险、boss 前 deck quality 统计 | 训练 scratch 外部 card prior / route prior，与本地 clean 日志做 ablation | 不训练 `play_card`；不计入 A0 胜利；不覆盖本地 MCP 标签 |
| MaT1g3R/Slay-the-Spire-data | importer/schema 小样本验证 | 检查 run history 字段兼容性和分析报表格式 | 不作为通用策略标签 |
| rrenaud/slay_analysis | fight/deck 特征工程参考 | 参考 per-fight deck info 生成思路 | 不直接当 Ironclad A0 训练集 |
| SlayTheSpireFightPredictor | 战斗损血预测目标参考 | 未来设计 combat/deck value shadow model | 不直接复制为 MCP 控制器 |
| colinking/runlogger | 逐动作日志格式参考 | 未来补充 `external_action_trace` 数据 | 当前不进入 live 执行层训练 |
| Run History Plus | 本机 run history 补充记录 | 辅助复盘和长期样本收集 | 不替代 MCP JSONL |
| Hugging Face slaythespire-codex | 静态卡牌知识、embedding、相似卡先验 | 补齐卡牌标签和语义检索 | 不提供胜负/路线/战斗标签；STS2 不混入 STS1 |

## 第一阶段最小实现

第一阶段只做 77M 小样本和静态卡牌 reference，不碰 live 控制器。2026-07-07 已接入外部 run history 的最小代码路径：

- `python -m slay_ai.import_external_runs`：把外部 JSON/JSONL/`.run`/`.gz` run history 转成隔离的 `external_manifest.json` 与 `card_reward_priors.jsonl`。
- `python -m slay_ai.train_external_priors`：从外部 prior rows 训练 scratch `card_prior_model.json`，默认写到 `data/external_models/<source>/`，不覆盖 `models/card_value_model.json`。
- `python -m slay_ai.climb_cycle --external-prior-input ...`：在正常爬塔-复盘-学习 cycle 的训练阶段额外训练 scratch external prior，并把 provenance 写入 cycle summary；不计入本地 `clean_trainable/pristine`，不改变 A0 gate，不给 live MCP runtime authority。
- 外部 prior CLI 默认拒绝写入 `models/card_value_model.json` 或同名 runtime 路径；只有经过离线评估和人工确认的 promotion 才允许显式使用 `--allow-runtime-output`。

新增或扩展的产物建议：

```text
data/external/sts_runs/77m_sample/
  raw/                         # 下载/抽样后的原始 .json/.json.gz
  external_manifest.json       # provenance、过滤条件、样本量、字段质量
  card_reward_priors.jsonl     # card_id, character, ascension_bucket, pick_rate, win_lift
  route_floor_risk.jsonl       # 后续扩展：floor, act, node_type/path stats, death_rate
  deck_quality_priors.jsonl    # 后续扩展：boss, deck tags, card/relic counts, outcome stats

data/external/reference/slaythespire_codex/
  external_reference_manifest.json
  card_reference.jsonl
  card_embeddings.index.json
```

第一阶段训练只写 scratch 模型：

```text
data/external_models/77m_sample/card_prior_model.json
data/external_models/77m_sample/route_prior_model.json
data/external_models/77m_sample/deck_quality_prior_model.json
```

这些模型默认不被 live policy 读取。当前已实现 `card_prior_model.json`；route/deck 外部 prior 仍是后续扩展。只有通过本地 clean MCP 日志评估后，才允许把其中一小部分合并成 `models/card_value_model.json` 的低权重先验，并且 summary 必须写明外部来源和权重。

## 进入本地模型的条件

外部先验要影响 `assist`，必须同时满足：

- 来源 manifest 有 provenance：source、url、downloaded_at、sample_filter、character、ascension、patch/date 范围、row_count。
- 和本地 MCP clean/pristine 日志做过离线对比，至少不降低：
  - card reward 复盘解释质量；
  - Act 1 boss reach/clear 相关离线指标；
  - 本地 run review 中的坏选择数量。
- 只进入小权重 prior，不覆盖本地 MCP learned delta。
- cycle summary 和中文复盘写明“外部先验参与”，不能写成“本局真实学习结果”。

## 训练入口边界

现有入口保持默认安全：

- `slay_ai.learn --manifest`：只吃本地 `clean_trainable`，不吃外部数据。
- `slay_ai.train_card_model --manifest`：只吃本地 `clean_trainable`，不直接吃外部 run history。
- `slay_ai.train_shadow_models --source-quality pristine`：只吃本地 pristine shadow rows。
- `source_validation_grade=external_prior` 和 `--source-quality external` 已作为隔离档位接入；默认入口仍会跳过它，只有显式外部命令和 scratch 输出目录能读取。
- 外部数据只通过专用命令进入 scratch 产物：

```powershell
python -m slay_ai.import_external_runs `
  data\external\sts_runs\77m_sample\raw `
  --source 77m_metrics_dump `
  --source-uri https://www.reddit.com/r/slaythespire/comments/jt5y1w/77_million_runs_an_sts_metrics_dump/ `
  --character IRONCLAD `
  --ascension-max 1 `
  --output-dir data\external\sts_runs\77m_sample

python -m slay_ai.train_external_priors `
  data\external\sts_runs\77m_sample\external_manifest.json `
  --model-dir data\external_models\77m_sample

python -m slay_ai.climb_cycle `
  --name cycle_YYYYMMDD_ironclad_a0_win_ext_prior `
  --characters IRONCLAD `
  --ascension 0 `
  --attempts-per-target 3 `
  --external-prior-input data\external\sts_runs\77m_sample\external_manifest.json `
  --source-quality pristine `
  --model-authority assist
```

不要手工把外部样本伪装成本地 JSONL。`learn --manifest`、`train_card_model --manifest` 和 A0 gate 仍只承认本地 MCP manifest。

## 建议 manifest/schema

外部 manifest 不进入现有三分类，应使用独立类型：

```json
{
  "version": 1,
  "manifest_type": "external_prior_manifest",
  "source_category": "external_run_history",
  "source_id": "77m_metrics_dump_sample",
  "source_uri": "https://www.reddit.com/r/slaythespire/comments/jt5y1w/77_million_runs_an_sts_metrics_dump/",
  "filters": {
    "game": "STS1",
    "character": "IRONCLAD",
    "ascension_max": 1
  },
  "categories": {
    "external_run_history": [],
    "external_reference": [],
    "rejected_external": []
  },
  "permitted_uses": ["shadow_prior", "card_prior", "offline_ablation"],
  "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"]
}
```

每条外部 row 必须带：

```json
{
  "source_category": "external_run_history",
  "source_reason": "external_prior",
  "source_validation_grade": "external_prior",
  "source_validation_flags": ["external", "not_mcp", "not_pristine"],
  "source_dataset": "77m_metrics_dump_sample",
  "source_weight": 0.2,
  "transform_version": 1
}
```

卡牌先验不要伪造 `decision.learn_card_pick`。没有真实 reward options 时，只训练 picked-card outcome prior，不生成 opportunity penalty；有完整 reward options 时，才允许做候选间比较。

## 对执行层学习的判断

当前不使用 77M、MaT1g3R 或 Run History Plus 训练执行层，因为它们通常缺少逐动作状态/action 序列。执行层学习需要至少包含：

- 每步手牌、能量、怪物 HP/intent、遗物/药水、可用命令；
- 实际 action：play_card/end_turn/use_potion/choose 等；
- action 后状态；
- 终局结果与中间损血。

runlogger 更接近这个目标，但它首先是采集工具。它的正确用法是未来建立 `external_action_trace` importer，然后与我们 MCP JSONL 对齐 schema；不是现在直接训练 live controller。

## 当前优先级

1. 先使用 77M 小样本 importer、card prior scratch 训练，以及 `climb_cycle --external-prior-input` 的 cycle 内训练摘要。
2. 接入 Hugging Face `slaythespire-codex` 静态卡牌 reference，补标签/embedding，不产生胜负标签。
3. 用本地 MCP clean/pristine 日志评估外部 card prior 是否改善卡牌奖励复盘。
4. 如果改善稳定，只允许进入 `model_authority=assist` 的卡牌奖励 tie-breaker，且权重低于本地 MCP learned delta。
5. 等本地执行层稳定、A0 终局胜利可重复后，再研究 runlogger 逐动作数据。

## 复盘写法

如果某轮使用了外部数据，中文复盘必须增加：

- 外部数据来源与 provenance。
- 它影响的是 `external_reference`、`external_run_history` 还是 `external_action_trace`。
- 本局实际动作是否由外部先验改变。
- 如果改变了卡牌奖励选择，列出启发式首选、本地 learned 首选、外部 prior 首选、最终选择。
- 对后续影响：继续保留、降低权重、回滚，或只保留为 shadow。

## 2026-07-07 当前接入状态

已确认并记录的外部来源用途如下：

- GitHub `MaT1g3R/Slay-the-Spire-data`：run-history 级数据源，优先用于 importer/schema 验证、小样本 card prior 和楼层/死亡风险统计，不作为逐动作执行层训练源。
- GitHub `colinking/runlogger`：本地逐动作日志采集工具参考。当前只作为未来 `external_action_trace` schema 对齐方向，不直接训练 live controller。
- GitHub `rrenaud/slay_analysis`：历史 run 分析项目，可参考 fight/deck 特征工程；不直接作为 Ironclad A0 训练集。
- Hugging Face `slaythespire-codex`：静态卡牌文本、标签或 embedding reference；只补充语义先验，不产生胜负标签。

`_15` 批次未配置 `--external-prior-input`，因此外部数据没有影响任何 live 动作，也没有进入 A0 gate、`learned_memory`、`clean_trainable` 或 `pristine`。下一阶段只允许通过以下路径使用外部数据：

1. 用 `import_external_runs` 生成隔离的 `external_prior_manifest` 与 `card_reward_priors.jsonl`。
2. 用 `train_external_priors` 或 `climb_cycle --external-prior-input` 训练 scratch `card_prior_model.json`。
3. 在中文复盘里记录 source_id、source_uri、filters、examples、model_path、`runtime_authority=false`。
4. 只有通过本地 MCP clean/pristine 日志离线对比后，才允许把极低权重 prior 合并到 card reward assist；路线、药水、战斗执行层仍不接入外部 prior。

## 2026-07-07 _16 后接入状态

`_16` 批次仍未使用外部 prior：`external_prior=skipped examples=0`，因此外部数据没有影响 F16 Hexaghost、F18 Act2、F22 Chosen + Cultist 等 live 行为。外部数据目前只承担三个职责：

- source catalog：记录可用来源和 provenance，不进入 MCP 胜负统计。
- scratch prior：未来可训练 card reward prior，但必须 `runtime_authority=false`。
- schema/reference：帮助对齐 runlogger 逐动作日志或静态卡牌文本，不替代本地 MCP clean/pristine 日志。

新增的 neural combat value shadow 推理只使用本地 MCP combat search labels 训练，不使用外部数据。若后续把外部数据用于 combat/action 层，必须另建隔离 provenance 桶，并在复盘中明确写出“外部 prior 只做 shadow 对比，未进入 runtime、gate、learned_memory、pristine”。

## 2026-07-07 追加外部参考

新增两个只作参考/离线对照的来源：

- GitHub `alexdriedger/SlayTheSpireFightPredictor` / Slay-I：项目说明其目标是用机器学习预测一场战斗会受到多少伤害，并用“平均节省伤害”评估加牌、删牌和升级。它和本项目 `combat_value_shadow` 的方向一致，适合作为 combat value target 设计参考；当前不导入其数据，不接入 runtime。
- GitHub `JoeyRussoniello/sts-win-prediction`：使用大量 human run 的 floor-level reconstructed data 预测胜率，适合参考楼层级 win-probability / risk calibration；当前不作为 A0 MCP 训练源，也不能计入 gate。

这两个来源的共同边界：只允许进入 `external_reference` 或离线 ablation 记录；不得写入 `learned_memory`、不得标记为 `pristine`、不得直接扩大 `model_authority`。

## 2026-07-07 _18 后接入状态

`_18` 批次没有使用外部 prior：`external_prior=skipped examples=0`。因此三局中的 Perfected Strike/攻击包、Slime Boss 过关、Act2 死于 Champ、Shockwave/Offering/Fiend Fire 局死于 Slime Boss，均不能归因于外部数据。

本轮新增的 143 条 `search.combat_value_shadow` 来自本地 `_16` 训练出的 `models/combat_value_model.pt`，训练来源是本地 MCP clean/pristine combat search evidence，不是外部数据集。外部数据在下一阶段仍按以下边界使用：

- `MaT1g3R/Slay-the-Spire-data`、`rrenaud/slay_analysis`：只做 run-history/statistical prior 和 schema 检查，不训练逐动作执行层。
- `colinking/runlogger`：只作为未来 `external_action_trace` 对齐参考；没有对齐到本地 MCP JSONL 前不进入 controller。
- `Hugging Face slaythespire-codex`：只做静态卡牌文本/语义 reference。
- `SlayTheSpireFightPredictor` 和 `sts-win-prediction`：只做 combat value / floor-level risk 的目标设计参考或离线 ablation，不进入 runtime、gate、learned_memory、pristine。

若下一步真的导入外部样本，必须先生成隔离的 `external_prior_manifest`，在每篇中文复盘里写明 source、样本数、权重和 `runtime_authority=false`。在本地 A0 终局胜利可重复前，外部数据不能让模型直接接管战斗执行层。

## 2026-07-07 _21 后接入状态

`_21` 批次仍未使用外部 prior：没有 `--external-prior-input`，也没有本地 `data/external/` 原始样本可供训练。因此 `_21` 的两盘 Boss 死亡和一盘 Astrolabe GRID 诊断都不能归因于外部数据。

最新查证继续支持三条隔离泳道：

- `MaT1g3R/Slay-the-Spire-data` 的 README 列出多个 run history 数据集样本，包括 rotating sample 和 Ironclad sample，适合 importer/schema 验证和低权重 card/run prior。
- `colinking/runlogger` 的 README 展示逐动作 `_type` 日志，包括 `action:play_card`、`action:select_map` 以及 floor/combat state；它是未来 `external_action_trace` 对齐的首选参考，但当前不是已清洗训练集。
- Hugging Face `t22000t/slay-the-spire-1-card-embeddings` 记录 STS1 360 张卡、1024 维 unit-normalized text embedding，并可与 card metadata join。它属于 `external_reference`，可用于静态卡牌相似度和语义标签，不产生胜负或逐动作标签。

当前可执行策略：

1. 没有原始外部 run 文件时，不启动 `import_external_runs` 或 `train_external_priors`；只保留文档化来源和 schema 边界。
2. 若后续下载小样本，必须先写入 `data/external/sts_runs/<source>/`，生成 `external_prior_manifest`，再训练 `data/external_models/<source>/card_prior_model.json`。
3. 外部 prior 即使训练成功，也只能作为 scratch/shadow 或低权重 card reward assist 候选；路线、药水、商店、战斗出牌和 A0 gate 仍只信本地 MCP clean/pristine 日志。

## 2026-07-07 _22 后接入状态

`_22` 批次完成真实 MCP 爬塔和训练，但外部 prior 仍为 `skipped`：`external_prior_inputs_not_configured`，`runtime_authority=false`。本轮训练出的 shadow/card/combat value 模型全部来自本地 MCP 日志，具体为 `_22` 的 offline shadow rows 和 training manifest，不包含外部 run history。

本轮新增训练产物：

- `data/climb_cycles/cycle_20260707_ironclad_a0_win_22/training/models/card_value_model.json`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_22/training/models/combat_search_model.json`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_22/training/models/combat_value_model.pt`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_22/training/models/deck_quality_model.json`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_22/training/models/potion_tempo_model.json`
- `data/climb_cycles/cycle_20260707_ironclad_a0_win_22/training/models/route_risk_model.json`

这些产物是 cycle scratch，不自动提升到默认 runtime。原因：A0 终局未胜利，Act1 gate 仍缺 1 个 pristine clear，且 Act2 route/rest 风险仍不稳定。外部数据下一步仍不参与执行层；若要使用，必须先下载原始样本并生成隔离 `external_prior_manifest`。

## 2026-07-07 _23 后接入状态

`_23` 批次完成 3 局真实 MCP 爬塔和训练，仍未达成 A0 终局胜利。该批次运行时没有配置 `--external-prior-input`，因此 `_23` 的三局 live 行为、Act1 Boss clear、Act2 死亡和 F10 早期路线失败都不是外部数据造成的；训练 summary 中外部 prior 仍是 `external_prior=skipped examples=0`。

`_23` 后已完成第一条真实外部样本闭环：

- 来源：GitHub `MaT1g3R/Slay-the-Spire-data` 的 `runs/200-rotating-sample/IRONCLAD` 原始 `.run` 样本。
- 本地来源目录：`_external/Slay-the-Spire-data/runs/200-rotating-sample/IRONCLAD`。
- 导入产物：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json` 与 `card_reward_priors.jsonl`。
- 导入结果：50 局 accepted、0 rejected、889 条 `card_reward_priors`，角色为 `IRONCLAD`，样本 ascension 为 20，`source_weight=0.05`。
- 训练产物：`data/external_models/mat1g3r_200_rotating_ironclad/card_prior_model.json` 与 `external_prior_training_summary.json`。
- 训练结果：889 examples、151 card deltas、`prior_scale=0.20`、`max_delta=2.0`。
- 权限边界：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`；禁止进入 `clean_trainable`、`pristine`、`gate`、`learned_memory`、`runtime_authority`。

因为该样本来自 A20 rotating sample，而当前目标是 A0 终局胜利，所以它不能直接作为 A0 策略标签，也不能提升 live controller。它现在只承担三件事：验证外部 `.run` importer、训练隔离 scratch card prior、为后续离线 ablation 提供对照。若未来在 `climb_cycle` 中使用，必须显式传入：

```powershell
python -m slay_ai.climb_cycle `
  --name cycle_YYYYMMDD_ironclad_a0_win_ext_prior `
  --characters IRONCLAD `
  --ascension 0 `
  --attempts-per-target 3 `
  --external-prior-input data\external\sts_runs\mat1g3r_200_rotating_ironclad\external_manifest.json `
  --external-prior-scale 0.20 `
  --model-authority assist `
  --source-quality pristine
```

该命令只会在 cycle 训练阶段生成 scratch external prior summary；不会让外部 prior 在本局 live 出牌、路线、药水或商店中接管。中文复盘必须写明“外部 prior 参与训练产物，不参与本局动作”。

已验证的回放接入命令为 `cycle_20260707_ironclad_a0_win_23_external_replay`：使用 `_23` 的 3 条本地 MCP 日志做 `--skip-live` 回放训练，并显式传入上述 external manifest。结果为 `external_prior=trained examples=889`，cycle 内部产物位于 `data/climb_cycles/cycle_20260707_ironclad_a0_win_23_external_replay/training/external_models/mat1g3r_200_rotating_ironclad/`。该验证证明外部 prior 已接入训练阶段，但它没有改写 `_23` live 行为，也没有进入默认 runtime 模型。

## 2026-07-07 _24 后接入状态

`_24` 是第一轮显式把外部 prior 输入接入真实 live cycle 训练阶段的批次。命令传入：

```powershell
--external-prior-input data\external\sts_runs\mat1g3r_200_rotating_ironclad\external_manifest.json
--external-prior-scale 0.20
--external-prior-max-delta 2.0
```

结果：

- live 爬塔：3 局本地 MCP A0，全部 `clean_trainable/completed_clean`，无 diagnostic、无 infra。
- 外部 prior 训练：`external_prior=trained examples=889`，deltas=151。
- cycle 内产物：`data/climb_cycles/cycle_20260707_ironclad_a0_win_24/training/external_models/mat1g3r_200_rotating_ironclad/card_prior_model.json`。
- 权限：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。

边界结论：

- 外部 A20 prior 只参与 `_24` 训练阶段 summary 和 scratch external card model。
- 它没有改变 `_24` 的 live route、shop、rest、potion、combat action 或 card reward runtime authority。
- 它不进入 A0 gate、不改变 `pristine` 判定、不写入 `learned_memory`。
- `_24` 的失败归因仍基于本地 MCP 证据：F16 Hexaghost `deck_quality`、F8 Sentries `route_risk`、F28 Centurion/Mystic `route_risk`。

## 2026-07-07 外部 prior 融合候选

已把外部数据接入从“独立 scratch prior”推进到“训练阶段融合候选”，但仍不进入 live runtime。`climb_cycle` 现在在本地 `card_model` 和 `external_priors` 都训练成功时，额外生成：

`cycle_dir/training/models/card_value_model_external_prior_blend.json`

该模型把本地 clean/pristine 卡牌 delta 作为主体，把 external card prior 以保守参数混入候选产物。默认参数：

```powershell
--external-prior-blend-scale 0.25
--external-prior-blend-max-delta 0.75
```

边界：

- 融合候选 summary/metadata 必须写明 `runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`、`requires_audited_promotion=true`。
- 它不写入 `models/card_value_model.json`，不被 `runner` 默认加载，不进入 A0 gate、`learned_memory` 或 `pristine` 统计。
- 只有后续用本地 MCP clean/pristine 日志做离线对比，证明它改善卡牌奖励解释且没有加重早死，才允许讨论显式 promotion。

已验证回放：

- 命令：`cycle_20260707_ironclad_a0_win_24_external_blend_replay`，对 `_24` 三条本地 MCP 日志做 `--skip-live` 回放训练，并传入 `mat1g3r_200_rotating_ironclad` external manifest。
- 结果：`external_prior=trained examples=889 external_blend=trained`。
- Gate 仍为 `NEEDS_WORK`，`reached=2/3`、`cleared=1/2`、`pristine_cleared=1/2`，说明外部 prior 和融合候选没有污染 A0 胜利判定。

## 2026-07-07 _25 后接入状态

`_25` 是第一轮在真实 live cycle 中同时启用 external prior 和 external blend 候选训练的批次。结果：

- live 爬塔：3 局本地 MCP A0，均到达 Act1 Boss，均未胜利。
- 本地训练：card model `trained examples=11`，shadow rows=93，combat value examples=37。
- 外部 prior：`external_prior=trained examples=889`，deltas=151。
- 外部融合候选：`external_blend=trained`，deltas=151，base_deltas=24，external_only_deltas=127，adjusted_local_deltas=24。
- 输出：`data/climb_cycles/cycle_20260707_ironclad_a0_win_25/training/models/card_value_model_external_prior_blend.json`。

边界结论保持不变：

- `_25` 的三局 live 行为不是外部数据控制；route、potion、shop、rest、combat 仍由本地控制器与搜索执行。
- 第 2 局 F14 `Brutality` 是本地 card reward assist 的 runtime 接管，不是 external prior 接管。
- external blend 没有写入 `models/card_value_model.json`，没有进入 A0 gate、`pristine`、`learned_memory` 或默认 runtime。

## 2026-07-07 _33 外部 prior 候选训练

`_33` 在真实 A0 live cycle 后，重新训练了一份独立外部 card prior 候选，但没有写入默认 runtime 模型：

- 输入：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`。
- 来源：`MaT1g3R/Slay-the-Spire-data` 的 200 rotating Ironclad 样本。
- 本地导入规模：50 个 Ironclad run，ascension=20，card prior rows=889。
- 训练命令参数：`--min-count 2 --max-delta 6 --prior-scale 0.20`。
- 输出：`data/climb_cycles/cycle_20260707_ironclad_a0_win_33/training/external_prior_candidate_model/card_prior_model.json`。
- 结果：examples=889，deltas=151。

边界结论：

- `_33` 的 live 行为不是外部数据控制；外部 prior 没有控制路线、战斗、药水、商店或 MCP 点击。
- 该候选模型 `runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。
- 该数据集是 A20 外部 run history，只能作为低权重 card prior 和离线对照，不能进入本地 A0 gate、`pristine`、`learned_memory` 或 runtime authority。

## 2026-07-07 _34 外部 prior 与融合候选状态

`_34` 是在真实 A0 live cycle 中继续传入外部 prior，并自动生成本轮 external prior 与 external blend scratch 产物的批次。它没有产生 A0 胜利，也没有把外部模型提升为 live runtime。

本轮外部数据输入：

```powershell
--external-prior-input data\external\sts_runs\mat1g3r_200_rotating_ironclad\external_manifest.json
--external-prior-scale 0.20
--external-prior-max-delta 2.0
--external-prior-blend-scale 0.25
--external-prior-blend-max-delta 0.75
```

结果：

- live 爬塔：3 局本地 MCP A0，全部 `clean_trainable/completed_clean`，胜利 0。
- Act1 gate：PASS，`reached=3/3`、`cleared=2/2`、`pristine_cleared=2/2`。
- 外部 prior：`examples=889`，`deltas=151`，`source_quality=external_prior`。
- external blend：`deltas=151`，`base_deltas=29`，`external_only_deltas=122`，`adjusted_local_deltas=29`。
- 输出：
  - `data/climb_cycles/cycle_20260707_ironclad_a0_win_34/training/external_models/mat1g3r_200_rotating_ironclad/card_prior_model.json`
  - `data/climb_cycles/cycle_20260707_ironclad_a0_win_34/training/models/card_value_model_external_prior_blend.json`

边界结论：

- `_34` 的 live 行为不是外部数据控制；外部 prior/blend 没有控制路线、战斗、药水、商店或 MCP 点击。
- 外部 prior 与 external blend 都是 scratch/candidate 产物，`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。
- 外部数据禁止用于 `clean_trainable`、`pristine`、A0 gate、`learned_memory`、`runtime_authority`。
- 目前只能用于 card reward 的低权重先验、离线 ablation 和人工审计后的显式 promotion 讨论；不能因为外部 A20 数据而跳过本地 A0 终局胜利证据。
## 2026-07-07 _35 外部 prior 与 usable shadow 训练状态

`_35` 继续使用外部 prior 输入：

```powershell
--external-prior-input data\external\sts_runs\mat1g3r_200_rotating_ironclad\external_manifest.json
--external-prior-scale 0.20
--external-prior-max-delta 2.0
--external-prior-blend-scale 0.25
--external-prior-blend-max-delta 0.75
```

数据集边界不变：`mat1g3r_200_rotating_ironclad` 是外部 Ironclad A20 run history，本地导入 50 runs、889 card reward prior rows。它只训练 scratch external card prior 与 external blend；`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。

`cycle_20260707_ironclad_a0_win_35` 原始 live 训练未把 shadow rows 接入统计模型，因为 `source_quality=pristine` 会拒绝 `usable_with_recoveries` rows。后续验证周期 `cycle_20260707_ironclad_a0_win_35_usable_training` 使用同一批本地 MCP 日志、`--skip-live` 和 `--source-quality usable`，成功训练本地 shadow 统计模型：accepted_rows=415，route-risk=58，potion-tempo=199，deck-quality=3，combat-search=155。

结论：外部数据仍只用于 card prior/blend；本地 clean_trainable/usable MCP 日志才用于 route/potion/deck/combat-search shadow 统计模型。usable shadow lane 不改变 A0 gate、`pristine`、`clean_trainable` 或 learned memory 的定义，也不使外部数据获得 runtime authority。

## 2026-07-07 _36 外部 prior 与 weighted combat value 状态

`cycle_20260707_ironclad_a0_win_36` 继续使用 `mat1g3r_200_rotating_ironclad` 外部 prior。live 结果没有 A0 终局胜利，但第三局进入 Act3，产生了更有价值的 Act2 Boss 与 Act3 combat evidence。

本轮外部数据仍只作为 card reward prior/blend：

- 输入：`data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json`
- 样本：50 个外部 Ironclad A20 run，889 条 card reward prior rows。
- 权重：`--external-prior-scale 0.20`，`--external-prior-blend-scale 0.25`，`--external-prior-blend-max-delta 0.75`。
- 产物：external prior trained examples=889；external blend trained deltas=151。
- 权限：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`。

本轮真正加速成长的是本地 MCP usable 数据，而不是外部数据提权。补跑 `cycle_20260707_ironclad_a0_win_36_usable_retrain` 后，本地 usable shadow rows=863，combat search labels=226，combat value MLP 使用 `quality_policy=weighted` 训练成功 examples=226，但仍为 shadow/value 校准模型，`runtime_authority=false`。

结论：外部 prior 仍不得进入 A0 gate、`pristine`、`clean_trainable`、learned memory 或战斗执行层；weighted combat value 只使用本地 MCP usable combat-search evidence，不使用外部 run history。
