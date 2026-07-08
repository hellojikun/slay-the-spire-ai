# 诊断复盘：20260707_171219_ironclad_a0

## 本局概况

- 源日志：`runs\ai_runs_climb_cycle_ironclad_a0_win_32\20260707_171219_ironclad_a0.jsonl`
- manifest：无；进程在 F21 后被强制停止，未完整落盘。
- 分类：diagnostic / incomplete，不进入 clean_trainable promotion。
- 角色/进阶：`IRONCLAD` A0
- stdout 观察：已击败 Act1 Boss，进入 Act2，并推进到 F21。
- 重要限制：由于没有 manifest，本局不能作为正式 A0 胜负、gate clear 或 pristine training 证据。

## 启发式与搜索影响

- 本局大量动作仍由 one-turn search、direct lethal、现有路线策略和执行保护控制。
- Act1 Boss 战中出现 Whirlwind 与药水 tempo 的有效窗口，stdout 显示在 Boss 战后拿到 Boss relic，并进入 Act2。
- Act2 F19/F21 仍大量依赖 one-turn search 处理多敌和高 incoming；这说明战斗执行层尚未由模型接管。

## 学习与模型影响

- route-risk assist 小样本封顶继续生效，路线日志多次出现 `model risk -12.0`。
- 这说明模型在路线层有 assist 影响，但仍不是 pilot，也没有直接点击 MCP。
- 因为本局未生成 manifest，本局不进入 Cycle 32 usable shadow candidate 训练。
- stdout 只能作为方向性诊断：小样本 cap 后存在清 Act1 的苗头，但还没有形成可审计训练样本。

## 外部数据影响

- 外部 `mat1g3r_200_rotating_ironclad` 仍只是 card prior/blend 先验。
- 本局的 Act1 clear 观察来自本地 stdout，不来自外部数据；但缺 manifest，所以也不作为正式 gate 证据。

## 执行完整性

- 进程长时间无输出后被手动停止。
- 停止前最后可见输出已到 F21，但没有 final manifest、campaign update 或 run summary。
- 该问题应归为执行层/流程完整性风险：后续需要让长局在中断时也能写 partial manifest，或提高 MCP 等待恢复能力。

## 后续影响

- 不把本局当作 A0 胜利或 Act1 gate clear 证据。
- 但本局提示：路线风险小样本限幅可能确实改善 Act1 生存，下一轮应继续跑完整 A0，并优先保证 manifest 落盘。
- 下一轮可以考虑提高可观测性：对超过固定时间无 stdout 的 run 写 heartbeat/partial summary。
