# HU bot v1.9.2：规则账本与受限性能基准候选

从冻结 `.work/hu-river-v1.9.1/` 复制到独立目录。正式运行策略保持冻结；新增规则账本/决策快照适配层、重连去重和受限性能基准。旧候选与已上传的 v1.8.4 均保留。本候选未上传，等待架构集成和独立验收。

本轮不改变策略表和默认开关。`RuleSnapshot`、`DecisionSnapshot`、`BettingLedger` 保留 SDK 权威 `pot/to_call/min_raise/max_raise`、原始行动事件、raise-to 总额、增量投入、可争底池和可能返还的分层表示；历史/权威冲突记录并触发原四窗口范围表的保守回退。平台不等额全下返还、真实升盲表和 Swiss 规则仍保持 Unknown。

开发方结果见 `reports/RULES-LEDGER-V192.md` 和 `reports/performance-baseline.json`。`dist/manifest-v1.9.2.json` 记录本地源码与表指纹；本候选未上传，也不替换用户确认的 v1.8.4。

