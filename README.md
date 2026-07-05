# Slay the Spire Growing AI

这个项目是一套面向 MCPTheSpire 的可成长脚本。当前方向是先把稳定自跑、复盘和学习闭环做扎实，再逐步把策略推到四角色 A20：

- 通过 MCPTheSpire 读取游戏状态并执行动作。
- 用可解释的启发式策略完成战斗、奖励、路线、休息和事件选择；战斗会规划一整个安全动作批次。
- 把每一步决策写入 JSONL 对局日志，方便复盘。
- 把每局结果沉淀到学习记忆；也可以离线重放历史日志继续学习。
- 支持 campaign 循环挑战四角色 A20，并记录每个目标的尝试次数和胜利状态。

## 快速开始

先按 [run_logs/MCPTheSpire_启动说明.md](run_logs/MCPTheSpire_启动说明.md) 启动带 MCP 的杀戮尖塔，确认下面地址可用：

```powershell
curl.exe http://127.0.0.1:8080/mcp
```

继续当前存档并自动决策：

```powershell
python -m slay_ai.runner --continue --max-steps 500
```

从新局开始铁甲战士 A0：

```powershell
python -m slay_ai.runner --start --character IRONCLAD --ascension 0 --max-steps 1200
```

只看下一步会做什么，不执行：

```powershell
python -m slay_ai.runner --dry-run
```

循环挑战四角色 A20：

```powershell
python -m slay_ai.campaign --ascension 20 --attempts-per-target 200 --max-steps 2200 --use-all-hardware
```

如果想从 A0 到 A20 逐级爬：

```powershell
python -m slay_ai.campaign --ladder --ascension 20 --attempts-per-target 50
```

从已有 `ai_runs` 日志重建学习记忆：

```powershell
python -m slay_ai.learn ai_runs --reset
```

先清洗日志再训练/学习：

```powershell
python -m slay_ai.static_knowledge
python -m slay_ai.training_manifest ai_runs_strategy_probe64 ai_runs_strategy_probe65 ai_runs_strategy_probe66 ai_runs_strategy_probe67 ai_runs_strategy_probe68 ai_runs_strategy_probe69 ai_runs_strategy_probe70 ai_runs_strategy_probe71 ai_runs_strategy_probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72 --knowledge-dir data\static_knowledge
python -m slay_ai.train_card_model --manifest data\training_manifest_probe64_72.json --model-path models\card_value_model_probe64_72.json --min-count 1 --max-delta 6
python -m slay_ai.learn --manifest data\training_manifest_probe64_72.json --reset
```

## 目录

- `slay_ai/mcp_client.py`：最小 MCP JSON-RPC 客户端。
- `slay_ai/policy.py`：当前启发式策略。
- `slay_ai/memory.py`：默认策略记忆与赛后学习状态。
- `slay_ai/runner.py`：主循环、日志和命令行入口。
- `slay_ai/campaign.py`：四角色 / 指定进阶的循环挑战器。
- `slay_ai/learn.py`：离线读取 JSONL 对局日志并更新记忆。
- `slay_ai/training_manifest.py`：把日志分成 clean/diagnostic/infra，并导出路线、药水、boss 前卡组质量的影子训练样本。
- `slay_ai/static_knowledge.py`：校验和读取卡牌、怪物、药水静态知识表，用于给影子样本补特征。
- `slay_ai/readiness.py`：Act 1 boss/elite 准备度评分，当前作为 shadow gate，不直接接管路线。
- `data/static_knowledge/`：Act 1/Ironclad seed 知识库，后续逐步扩到四角色全局。
- `data/default_memory.json`：第一版卡牌、遗物、路线、战斗权重。
- `tests/test_policy.py`：无需启动游戏即可跑的策略测试。

## 学习方式

每次脚本遇到 `GAME_OVER` 都会更新 `data/learned_memory.json`：

- 胜利、死亡次数。
- 最近结局摘要。
- 卡牌奖励选择的轻量统计。
- 根据本局拿过的牌和结局微调卡牌 `delta`，下一局选牌会受影响。

`python -m slay_ai.learn ai_runs` 可以把历史日志再次喂给学习记忆。`--reset` 会先清空旧学习结果再重放日志。更推荐先用 `slay_ai.training_manifest` 生成清单，再让 `learn` 和 `train_card_model` 通过 `--manifest` 只读取 `clean_trainable` 日志，避免诊断日志污染模型。`--knowledge-dir data\static_knowledge` 会把卡牌、怪物、药水事实特征写入 shadow 样本，但不会把网上资料当作胜负标签。

## 硬件资源

`--use-all-hardware` 会记录 CPU/GPU 可用信息，并为后续离线训练保留资源意图。当前 MCP 控制通常仍是单游戏实例；GPU 更适合后续从 `ai_runs/*.jsonl` 训练价值模型或策略模型。

## 当前边界

这套脚本已经能循环挑战四角色 A20，但尚未通过真实长跑证明“四角色 A20 已完成”。完成这个目标需要让 campaign 实际跑出四个 A20 胜利，并用 `data/campaign_progress.json` 作为证据。

## 安全边界

脚本默认不会自动放弃已有存档。若要从新局开始，请先在游戏里处理存档，或明确使用 MCPTheSpire 的放弃接口后再运行。
## Multi-Agent 协作记录

多 agent 的长期角色、启动 prompt、复用方式和常用实跑命令记录在 [docs/multi_agent_workflow.md](docs/multi_agent_workflow.md)。
