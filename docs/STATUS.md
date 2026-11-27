# Bot当前状态

更新：2026-11-27。本轮仅整理文档，未修改策略或运行适配器。

当前HU基线为v2.2.2 final-r9，来源于固定源码ZIP；[清单](../provenance/baseline.json)记录归档和文件指纹。产品使用 `adapters/riverline` 的固定JSONL契约，原始冻结来源保持不变。

| 路径 | 职责与状态 |
| --- | --- |
| `submission/hu_bot/v2_*` | 当前HU策略/时限/合法性运行时；主入口选择v2 |
| `adapters/riverline` | 有界产品请求，只接收自己的牌与公共事实 |
| `scripts/package-core.py` | 校验并打包产品允许的五个核心文件 |
| `lab/tests/tools` | 离线实验、回归与历史打包；旧工具路径按各自版本核对 |
| `references/hu-overbet-v1.3.0` | 18文件只读测试对照，不进入实时路径 |
| `reports` | 原发行包含的历史JSON和分析脚本；不是当前验收入口 |

最近独立基线 `276cfe9`：381 passed、2 skipped（可选v1.2.3对照、v1.0工件未分发），固定核心包通过；[验收原记录](archive/2026-11-27/BASELINE-ACCEPTANCE-2026-11-27.md)。本轮没有重新运行pytest，也没有连接比赛平台或上传。

工程/局部行为已有证据，行动价值与整体Match强度未证明，不能声称GTO或推荐替换平台版本。六人策略、旧Poker Agent竞赛方向和新的平台验证尚待开发。实时路径不引入数据库、产品HTTP、LLM或外部Solver。

原始CRLF核心字节由Git属性保留，跨机器克隆后也须匹配允许指纹。文档提交不改变策略版本；后续升级先在源仓验证，再由产品固定提交与文件摘要集成。
