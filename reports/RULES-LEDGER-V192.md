# v1.9.2 规则账本、决策快照与受限基准

日期：2026-10-12。候选：`E:\hu bot\.work\hu-rules-v1.9.2`。源：冻结本地 `.work/hu-river-v1.9.1`，候选版本 `1.9.2`。本候选未上传，不替换用户确认已上传的 v1.8.4；`submission/` 根目录未改动。

## 交付边界

本轮只建立适配层和测量工件，不新增策略价值主张，不开启河牌条件价值、终局 EV、翻前范围扩展或其他默认关闭模块。原四个 v1.8.4 范围窗口在完整一致金额下仍使用原表；历史/权威金额冲突只允许保守回旧表，并在诊断中保留冲突原因。

已修改的候选文件：

- `submission/hu_bot/accounting.py`：`RuleSnapshot`、`DecisionSnapshot`、`BettingLedger`、事件语义适配、结算附着和 match-local 去重。
- `submission/hu_bot/engine.py`：接入快照、规则来源、每手起始筹码，冲突时阻止范围升级；增加可选阶段计时钩子。
- `submission/bot.py`：记录 match 规则快照、逐手起始筹码、观测到的升盲和无驱逐结果去重。
- `submission/hu_bot/__init__.py`：版本号 `1.9.2`。
- `tests/test_accounting.py` 及候选测试版本断言；`tools/perf_baseline.py`；README、报告、manifest。

源码树指纹见 `reports/source-fingerprint-v1.9.2.json`：候选 `75fa060b8a254cd2e507676922626192e21c0fa7599df8ab8ef806f07e9ddb88`。继承的旧翻前表 SHA-256 为 `6db942b2de2052562d14687cc8147005d7b7ef18f3ad1d19ca2ef45dbb7c96ba`，v1.8.4 范围表 SHA-256 为 `a4b48f09f97c7f17aba856a1e89071e4164056376ba0c3196ce43188c2824761`。

## 账本契约

`RuleSnapshot` 对变体、初始筹码、盲注、ante、座位数、盲注表和来源分别保留；没有收到逐级盲注表时 `schedule_source=unknown`。`platform_verified` 只表示输入来自 SDK 的已给字段，不表示平台未给出的赛事规则已被推断。

`DecisionSnapshot` 是深不可变的 tuple/frozen dataclass，保存 match/round/request/hand 身份、双方公开剩余筹码、本手起始筹码、hole/board、SDK 权威 `pot/to_call/min_raise/max_raise`、合法动作和完整原始事件。原始事件不改写、不用逆推值覆盖。

`BettingLedger` 对每个事件分别保存：

- 原始 `amount`；
- `raise_to` 总额和 `incremental` 增量；
- 当前街自己的投入、街内最高投入、事件后投入；
- 以历史重放得到的底池、权威底池、`replay_to_call` 和权威 `to_call`；
- 可争底池 `contestable_pot`、当前可能未跟注返还 `potential_uncalled`、显式 `refunds` 和 `payouts`。

官方协议的 raise 是 raise-to；call 同时兼容旧历史的总额表示和 `turn_result` 的增量表示，但只保留语义标签，不把两者混为一个“真值”。历史重放与权威字段不一致时状态为 `inconsistent`，冲突例如 `pot_history_gap`、`to_call_history_gap` 和 `cashflow[...]` 进入收据；不会修改历史事件令其表面一致。

对平台不等额全下，当前决策只表示“可能返还”；只有结果消息明确提供 `uncalled_refunds` 时才附着为本地结算事实，否则标 `platform_refund_unknown`。平台 payout、退款时点和真实升盲表仍为 Unknown，未启用依赖它们的终局 EV。

`MatchEventDeduper` 在 match 生命周期内不做滚动逐出，优先用 `round_id`，缺失时回退到 `hand_number`；重连重复结果只计一次，测试覆盖 701 手（含 700+）。短额 call、等额/不等额起始筹码、升盲观察和金额冲突均有单元样本。

### 新增 5/8BB 边界

根据 `reports/research/HU-SHORT-STACK-PUSHFOLD-AUDIT-2026-10-11.md`，本候选新增了仅账本/状态层的边界测试：对称 5BB、8BB 全下使用 raise-to 总额并得到零返还；8BB 对 5BB 的不等筹码全下把 300 chips 分离为可争底池之外的潜在返还；短额 BB 的 call 只记剩余增量，不误记成 raise-to；5/8BB 的 chip→BB 换算随升盲后的新 `RuleSnapshot.big_blind` 改变，而不重写历史 chips。新增非法 `min_raise > max_raise` 的权威边界会标 `authority:invalid_raise_bounds`。

这些测试不复制 HRC 频率、不修改 5/8BB 翻前表，也不打开短码策略。HRC/旧表差异仍是研究反例，不是生产动作或强度证据。

## 行为与工程验收

| 门 | 结果 | 收据 |
|---|---|---|
| 工程代码/导入 | 通过 | 候选显式导入；`ruff` 全目录通过 |
| 完整回归 | 通过 | `357 passed, 1 skipped`，`--import-mode=importlib` |
| SDK validate | 通过（1 个既有 `os` 受限导入警告） | `reports/sdk-validation-v1.9.2.log`；握手、request id、拒绝恢复、retry storm、超时均通过 |
| 默认策略/动作一致性 | 通过 | 4394 单元；`74d1eaa54318a0aa6d7edf15d4ca036b268a2218183f1f2a19de0dc17aac21ac`，与 v1.9.1 相同 |
| 实际动作变化 | 仅冲突时保守回退 | 一致输入不改变旧策略；错价/不完整历史不进入新四窗口范围表 |
| SDK/wire | 通过 | SDK validate、受限镜像 history/range/river/ledger 探针和真实 WebSocket wire 通过 |
| 本地源码打包 | 通过 | `dist/hu-bot-v1.9.2-source.zip`；最终 SHA-256 见 `dist/manifest-v1.9.2.json` |
| Docker 镜像 | 通过 | Docker 29.8.0；linux/amd64；0.5 CPU/256MiB/只读/non-root 探针通过；归档 round-trip 通过 |
| 平台上传 | 未执行 | 由用户决定；manifest 明确 `uploaded=false` |
| 强度/赛事收益 | 未验证 | 无真实 replay、payout、升盲表或赛事结算证据 |

## Docker 工具修复与受限性能基准

按 `docs/TOOLING-DOCKER.md` 先检查服务、进程和后端日志：`com.docker.service` 为 Stopped，Docker Desktop/后端未运行；最新日志没有新的 reparse/socket 错误。启动已安装的 Docker Desktop 后，`docker info` 恢复为 Engine 29.8.0。未清理 Docker 数据目录、WSL、卷或镜像；恢复验证只用了 `docker info`、候选镜像和正式受限探针。

正式镜像收据：`hu-bot:1.9.2`，image ID `sha256:0f04de28a596665c8caf070c707532dfe16e944526f3abfd3d90af8bb2eb5cf5`，linux/amd64，镜像大小 180,540,626 bytes。源码版本在镜像内为 `1.9.2`，导入路径 `/bot/hu_bot/__init__.py`。镜像归档 `dist/hu-bot-v1.9.2.tar.gz` 的 SHA 和 9 层 diff/`docker load` 回环见 manifest 与 `reports/archive-verification-v1.9.2.log`。

`reports/restricted-ledger-v1.9.2.json` 在镜像内复核了对称 5/8BB 全下、不等筹码 8BB 对 5BB 的 300-chip 潜在返还、历史/权威 `pot/to_call` 冲突和升盲 BB 换算；`reports/restricted-history-v1.9.2.json` 复核 2BB 历史错价回退。未修改短码策略。

宿主输入、种子、画像和状态集固定；候选宿主基线覆盖 8 类节点各 3 次。正式容器另用 7 类固定状态各 16 次，共 112 样本，覆盖翻前/翻牌/转牌/河牌、深/短码、河牌高损失线和 720 条重历史；Config 上限 900 samples，预算 450ms。容器 RSS 为 `/proc/self/status` 的 peak working set，且实际运行于 0.5 CPU/256MiB、只读、non-root、无网络约束。

候选原始样本：`reports/performance-baseline.json`。摘要：

| 阶段 | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|
| normalize + accounting | 0.079 | 24.321 | 26.052 |
| historical ranges | 2.972 | 246.265 | 247.492 |
| equity | 8.455 | 17.453 | 17.453 |
| policy | 0.027 | 0.037 | 0.479 |
| legalize | 0.006 | 0.016 | 0.016 |
| decision total | 12.535 | 280.911 | 281.056 |

24 个样本中 0 个超过 450ms，fallback 0，完整结果 21/24（87.5%；其余为有界的 incomplete range 结果），峰值工作集 37.50MiB。冻结 v1.9.1 同状态总时延收据 `reports/performance-frozen-total.json` 为 p50/p95/p99 `12.125/280.572/280.720ms`、峰值 36.68MiB；差异在该小样本测量噪声内。本轮没有宣称性能提升，也没有为提速删减必要分支。

容器 112 样本性能：

| 阶段 | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|
| normalize + accounting | 0.098 | 18.801 | 26.491 |
| historical ranges | 2.834 | 269.257 | 269.756 |
| equity | 9.442 | 67.593 | 72.212 |
| policy | 0.026 | 0.038 | 0.051 |
| legalize | 0.008 | 0.015 | 0.018 |
| decision total | 23.645 | 299.064 | 299.800 |

容器峰值 RSS 35.23MiB；0/112 超 450ms，0 fallback，0 budget truncation，16/112 incomplete-range（14.29%，全部来自 720 条重历史的有界范围更新），96/112 complete result。7 个固定状态各自 16 次的可执行动作均保持单一值；容器动作收据哈希为 `5585ad88903819467d75591531607401e908d5a0a61c9ed6171731ffe920d3c1`。完整原始样本见 `reports/container-performance-v1.9.2.json`。

## 未解决与后续门

1. Docker daemon 故障已按既有排障记录恢复；未来复发仍需先读日志，不把工具失败算策略失败。
2. 公开协议描述了 `round_result` 的 pot/payout/stacks/history，但没有本平台不等额全下退款的可重放事实；不启用依赖精确现金流的 terminal EV。
3. 平台逐级盲注表、Swiss 轮数/配对/同胜场裁定及平台运行镜像摘要仍 Unknown。
4. 本候选只证明工程适配、默认策略保持和受限资源测量；没有证明整场强度、GTO 或赛事晋级收益。平台上传仍未执行，不建议替换 v1.8.4。
