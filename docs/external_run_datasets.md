# 外部爬塔数据集记录

更新时间：2026-07-07。用途：记录网上可用的 Slay the Spire 数据源，并规定它们进入本项目学习系统的边界。具体使用方案见 `docs/external_data_usage_plan.md`。

## 结论

网上有现成爬塔历史数据，但大多数是 `.run`/metrics 级别的结果日志，不是逐动作 MCP 轨迹。它们适合做卡牌、遗物、路线、胜率、楼层生存的先验或 shadow pretraining；不适合直接训练当前执行层动作控制，更不能作为本项目 A0 终局胜利证明。

当前原则：

- 外部数据必须单独保存 provenance，例如 `data/external/sts_runs/<source>/`。
- 外部数据的 manifest 分类必须是 `external_run_history`、`external_reference` 或类似专用桶，不能伪装成 `clean_trainable` / `pristine`。
- 默认只用于 shadow 模型、卡牌价值先验、静态知识补全、ETL/schema 测试和离线消融。
- 不直接混入 `learn --manifest`、`train_card_model --manifest` 的本地 MCP clean 数据，除非命令显式声明外部来源并在 summary 中记录权重。
- 不用外部数据扩大 `model_authority`。模型接管 live 执行层仍必须来自本地 MCP 复盘、验证和 A0 终局证据。

## 可用来源

| 来源 | 类型 | 可用点 | 风险/限制 |
| --- | --- | --- | --- |
| 77 Million Runs / STS Metrics Dump：<https://www.reddit.com/r/slaythespire/comments/jt5y1w/77_million_runs_an_sts_metrics_dump/> | 大规模 run history / metrics dump | 作者说明有超过 7700 万局，按 `.json.gz` 分块，包含 2018-2019 与 2020 年 7-11 月数据；适合做 card/relic/path/win-rate 先验和大样本统计 | 混合玩家水平、旧版本补丁、非逐动作；容易学到玩家偏好而不是最优策略 |
| MaT1g3R/Slay-the-Spire-data：<https://github.com/MaT1g3R/Slay-the-Spire-data> | 主播/玩家 run history 样本与分析代码 | 有 vmService 等玩家授权 run history 样本；适合做小规模高质量 ETL 样例和报告格式参考 | 数据量小，来源偏特定玩家风格；仍多为 run history 而不是逐动作轨迹 |
| rrenaud/slay_analysis：<https://github.com/rrenaud/slay_analysis> | 分析项目与 preprocessing 参考 | 使用 2020 monthly 数据生成 per-fight deck info；适合参考“如何把历史日志转成战斗级特征” | 项目说明主要限 Defect；不是本项目 Ironclad A0 直接训练集 |
| Slay-I / SlayTheSpireFightPredictor：<https://github.com/alexdriedger/SlayTheSpireFightPredictor> | 神经网络项目/战斗结果预测参考 | README 说明模型使用 32.5 万以上战斗数据，来源为 Spire Logs 与 Jorbs；适合参考“预测战斗损血 -> 评估加牌/升级/删牌”的建模目标 | 不等于可直接下载的逐动作训练集；来源和模型目标与当前 MCP 控制器不同 |
| colinking/runlogger：<https://github.com/colinking/runlogger> | 逐动作日志 Mod | 会记录 play_card、事件选择、状态快照等逐动作信息；未来如果要学执行层，这是比普通 `.run` 更接近我们 JSONL 的格式 | 主要是采集工具，不是已整理的大型历史数据；格式可能变化，需单独解析 |
| Run History Plus：<https://github.com/modargo/RunHistoryPlus> / Steam Workshop | 增强本地 run history 的 Mod | 可改善本地 `.run` 文件质量，适合本机长期采集自己的历史 | 仍偏结果/楼层历史，不是完整逐动作控制日志 |
| Hugging Face slaythespire-codex：<https://huggingface.co/blog/t22000t/slay-the-spire-ai-collection> | 静态卡牌元数据与 embedding 数据集 | 2026 年发布的 STS1/STS2 卡牌元数据、文本 embedding、多模态 embedding；适合静态知识、相似卡检索、卡牌先验 | 不是爬塔日志；不能提供路线/战斗/胜负标签；STS2 部分不能直接混入 STS1 控制策略 |

## 接入顺序

1. 先写外部导入器，只做 schema 验证、source provenance、character/ascension/patch/date 过滤。
2. 先导入小样本，例如 77M dump 的 12 万局 sample 或 MaT1g3R 样本，生成 `external_run_history` manifest。
3. 只训练 scratch/shadow 模型，输出与本地 MCP 模型分离，例如 `data/external_models/<source>/...`。
4. 用离线 replay/ablation 比较外部先验是否改善本地复盘指标，例如卡牌选择解释、Act 1 boss readiness、路线风险排序。
5. 只有当外部先验在本地 clean MCP 日志上表现稳定，才允许作为 `assist` 的弱先验；仍不能计入胜利、pristine 或 gate 通过。

详细决策：外部数据分为 `external_reference`、`external_run_history`、`external_action_trace` 三条泳道。第一阶段已接入 `external_run_history` 的卡牌 prior 导入/训练：`slay_ai.import_external_runs` 和 `slay_ai.train_external_priors`。77M 小样本 route/deck prior 与 `slaythespire-codex` 静态 reference 仍按隔离泳道推进；不训练 live 执行层。

## 2026-07-07 查证补充

- MaT1g3R/Slay-the-Spire-data 明确提供多个 run history 数据集样本，包括 200-run rotating sample、Ironclad sample、Defect sample 等；适合作为 importer/schema 验证，不作为通用策略标签。
- colinking/runlogger 记录逐动作 JSON 行，包括 `select_map`、`play_card` 和 state snapshots；这是未来 `external_action_trace` importer 的优先参考格式。
- rrenaud/slay_analysis 依赖 2020 monthly 数据并生成 per-fight deck info；它适合参考战斗/牌组特征工程，但项目说明主要限 Defect，不直接喂给 Ironclad A0。
- 公开统计文章记录过 75M+ runs / 2020 18M runs 级别的数据量，也明确老版本/补丁漂移问题；因此只作为低权重 prior 或抽样统计，不作为 live 胜利证据。
- Hugging Face `slaythespire-codex` 是 2026 年发布的卡牌元数据与 embedding 集合；它属于 `external_reference`，可补卡牌相似度/语义标签，但不能提供路线、战斗动作或胜负标签。

## 当前项目中的默认使用方式

- A0 终局胜利目标仍只承认本地 MCP live run。
- `climb_cycle --external-prior-input` 可以在训练阶段读取 `external_prior_manifest` 或 `card_reward_priors.jsonl`，训练 scratch `external_card_prior`；它只写 cycle summary 和 `data/climb_cycles/.../training/external_models/...`，不写本地 MCP clean/pristine，不计入 gate。
- 中文单局复盘必须区分“本局实时行为来自启发式/搜索/本地模型”，外部数据最多写成“背景先验”，不能写成“本局学习结果”。
- 如果某次训练使用了外部数据，cycle summary 必须写出来源、行数、过滤条件、权重、目标模型和是否进入 runtime。
- 如果外部数据与本地 MCP 复盘冲突，优先相信本地 MCP clean/pristine 证据。

## 2026-07-07 实际导入记录

已接入一个小规模、隔离的外部 run-history 样本，用于验证 importer 和 scratch card prior 训练：

| 字段 | 值 |
| --- | --- |
| source_id | `mat1g3r_200_rotating_ironclad` |
| 来源 | `MaT1g3R/Slay-the-Spire-data` 的 `runs/200-rotating-sample/IRONCLAD` |
| source_uri | <https://github.com/MaT1g3R/Slay-the-Spire-data/tree/master/runs/200-rotating-sample/IRONCLAD> |
| 本地原始目录 | `_external/Slay-the-Spire-data/runs/200-rotating-sample/IRONCLAD` |
| 本地 manifest | `data/external/sts_runs/mat1g3r_200_rotating_ironclad/external_manifest.json` |
| prior rows | `data/external/sts_runs/mat1g3r_200_rotating_ironclad/card_reward_priors.jsonl` |
| 导入结果 | 50 runs accepted、0 rejected、889 card prior rows |
| 过滤/权重 | `--character IRONCLAD`、`--limit-runs 50`、`--source-weight 0.05` |
| ascension | 样本为 A20；当前 A0 目标不能直接把它当作策略标签 |
| scratch 模型 | `data/external_models/mat1g3r_200_rotating_ironclad/card_prior_model.json` |
| 训练结果 | 889 examples、151 card deltas、`prior_scale=0.20`、`max_delta=2.0` |
| 权限 | `runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true` |

该样本当前只允许用于 `external_run_history` 的 card prior / offline ablation。它不训练逐动作执行层，不写入 `learned_memory`，不影响 A0 gate，也不证明任何本地胜利。由于 A20 与 A0 分布不同，它的后续用途应优先是“低权重对照”和“卡牌奖励候选解释”，而不是直接合并到默认 card model。

## 2026-07-07 训练阶段融合状态

当前项目已经可以在 `climb_cycle` 训练阶段生成低权重融合候选：

- 输入：本地 MCP clean/pristine card model + `mat1g3r_200_rotating_ironclad` external card prior。
- 输出：`cycle_dir/training/models/card_value_model_external_prior_blend.json`。
- 默认融合参数：`external_prior_blend_scale=0.25`、`external_prior_blend_max_delta=0.75`。
- 权限：`runtime_authority=false`、`runtime_default_enabled=false`、`does_not_control_live_mcp=true`、`requires_audited_promotion=true`。

已用 `_24` 三条本地 MCP 日志做 `cycle_20260707_ironclad_a0_win_24_external_blend_replay` 验证，训练摘要为 `external_prior=trained examples=889 external_blend=trained`。该 replay 不构成新胜利证据，也不改变任何 live 行为；它只提供离线候选，供后续用本地 clean/pristine 日志对比卡牌奖励解释质量。

## 不直接使用的来源

STS2 社区 run history 网站和 STS2 日志工具只能作为产品/格式参考。STS2 规则、卡池、敌人和数值与 STS1 不同，不进入当前 Ironclad A0 控制器训练。
