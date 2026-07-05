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

## 目录

- `slay_ai/mcp_client.py`：最小 MCP JSON-RPC 客户端。
- `slay_ai/policy.py`：当前启发式策略。
- `slay_ai/memory.py`：默认策略记忆与赛后学习状态。
- `slay_ai/runner.py`：主循环、日志和命令行入口。
- `slay_ai/campaign.py`：四角色 / 指定进阶的循环挑战器。
- `slay_ai/learn.py`：离线读取 JSONL 对局日志并更新记忆。
- `data/default_memory.json`：第一版卡牌、遗物、路线、战斗权重。
- `tests/test_policy.py`：无需启动游戏即可跑的策略测试。

## 学习方式

每次脚本遇到 `GAME_OVER` 都会更新 `data/learned_memory.json`：

- 胜利、死亡次数。
- 最近结局摘要。
- 卡牌奖励选择的轻量统计。
- 根据本局拿过的牌和结局微调卡牌 `delta`，下一局选牌会受影响。

`python -m slay_ai.learn ai_runs` 可以把历史日志再次喂给学习记忆。`--reset` 会先清空旧学习结果再重放日志。

## 硬件资源

`--use-all-hardware` 会记录 CPU/GPU 可用信息，并为后续离线训练保留资源意图。当前 MCP 控制通常仍是单游戏实例；GPU 更适合后续从 `ai_runs/*.jsonl` 训练价值模型或策略模型。

## 当前边界

这套脚本已经能循环挑战四角色 A20，但尚未通过真实长跑证明“四角色 A20 已完成”。完成这个目标需要让 campaign 实际跑出四个 A20 胜利，并用 `data/campaign_progress.json` 作为证据。

## 安全边界

脚本默认不会自动放弃已有存档。若要从新局开始，请先在游戏里处理存档，或明确使用 MCPTheSpire 的放弃接口后再运行。
## Multi-Agent 协作记录

多 agent 的长期角色、启动 prompt、复用方式和常用实跑命令记录在 [docs/multi_agent_workflow.md](docs/multi_agent_workflow.md)。
