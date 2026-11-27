# v2.2.2 final-r9 独立轻量验收

日期：2026-10-26  
验收对象：`.work/hu-v2-strength-v2.2.2`，`package_label=final-r9`  
性质：只读、独立真实 `GameState` 的工程与局部翻前行为验收；不是 solver、action value、Match/Swiss 强度或平台验收。

## 裁定

**工程门：通过。** 1326 个具体 BTN 首入组合、自然 limp/iso 小树、5.9–11.1BB 3-bet 边界、SDK、final-r9 镜像/归档和受限端点抽样均产生合法动作；独立复放未见 fallback 或金额账本冲突。

**局部基础行为门：有限通过。** v2.2.2 解决了 v2.2.1 的灰格自然 limp 全弃和 6–11BB off-grid 旧宽跟问题：自然首入确实产生 46 个 limp，保护组合中存在自然可达的 AA/KK/AKs/A5s；3/5/6BB iso 的具体响应均合法；5.9/11.1BB 使用显式保守边界。

**策略质量仍不通过/未证明。** 3BB iso 下 46/46 个自然 limp 都继续，6BB iso 下 15/46 继续；这些是机械策略覆盖结果，不是对手条件收益。Q、对手条件 EV、Match/Swiss 胜率和平台上传仍未验证。final-r9 未上传，不替换 v1.8.4。

## 独立方法

- 使用 [hu_v2_v222_independent_audit_2026_10_26.py](hu_v2_v222_independent_audit_2026_10_26.py)，显式把候选路径指向 v2.2.2；不读取开发审计结果作为动作真值。
- 真实 ChipZen `GameState`，100BB 有效筹码、零 ante、整数 chip、完整自然历史和 SDK 合法动作菜单。
- 1326 个具体组合、169 类别；自然 BTN 首入后只把实际 `call`/limp 的 46 个具体组合带入 BB iso 响应，未用人工 AA 夹具替代自然到达。
- 另构造 BB 面对 BTN limp 的 `BB_LIMP_OPTION` 状态、错价/ante/深度/不等筹码边界和实际 WebSocket 端点。

## 1. BTN 首入与自然保护可达性

独立 1326 组合结果：

| 动作 | 具体组合 |
|---|---:|
| raise-to 250（2.5BB） | 1052 |
| limp/call | 46 |
| fold | 228 |
| 合计 | 1326 |

46 个自然 limp 包含 24 个灰格可玩组合和 22 个保护组合。自然可达的具体保护组合包括：`AsAd`（AA）、`KsKh`（KK）、`AsKs`（AKs）和 `Ad5d`（A5s blocker）。这些牌在 BTN 首入实际返回 limp，而不是只存在于人工测试夹具；对 BB iso 后均能产生合法保护性 limp-reraise。

BB 面对 BTN limp 的独立 1326 组合状态另测得 `check=882`、`raise-to=300=444`，全部动作合法。这验证了自然 limp 后 BB check/iso 节点的动作菜单存在；它不证明 BB iso 频率或收益。

## 2. 自然 46 limp 对 BB iso 小树

以下分母始终是同一批自然产生的 46 个 BTN limp 组合：

| BB 实际 iso-to | fold | call | 合法 limp-reraise | raise-to | 合法/回退 |
|---:|---:|---:|---:|---:|---|
| 3.0BB / 300 | 0 | 42 | 4 | 900 | 46/0 |
| 5.0BB / 500 | 24 | 18 | 4 | 1500 | 46/0 |
| 6.0BB / 600 | 31 | 11 | 4 | 1800 | 46/0 |

3BB 下 46/46 继续，5BB 下 22/46 继续，6BB 下 15/46 继续。这个价格单调性和自然保护分支在工程上可解释，但没有 BB 对手频率、翻后实现率或 `Q_limp−Q_fold`，不能称为正 EV；尤其 6BB 的 15/46 继续率应作为后续对手条件审计输入。

## 3. BTN 对 3-bet 价格树

每档均审计全部 1326 具体组合。表中 `off_grid` 是 NodeKey/trace 标志，不等于非法金额。

| 实际 3-bet-to | fold | call | raise | 2.2× raise-to | off-grid 行 |
|---:|---:|---:|---:|---:|---:|
| 5.9BB / 590 | 1272 | 0 | 54 | 1298 | 1326 |
| 6.0BB / 600 | 872 | 400 | 54 | 1320 | 1326 |
| 6.9BB / 690 | 872 | 400 | 54 | 1518 | 1326 |
| 7.0BB / 700 | 872 | 400 | 54 | 1540 | 0 |
| 10.0BB / 1000 | 872 | 400 | 54 | 2200 | 0 |
| 10.1BB / 1010 | 872 | 400 | 54 | 2222 | 1326 |
| 11.0BB / 1100 | 872 | 400 | 54 | 2420 | 1326 |
| 11.1BB / 1110 | 1272 | 0 | 54 | 2442 | 1326 |

8 个价格共 10,608 行，全部动作合法、0 fallback、ledger consistent。5.9/11.1BB 采用 `pdf_pressure_boundary_3bet_control`，没有回退到旧的宽松价格防守路径。

指定牌型复放：

- `97o`、`65o`：5.9–11.1BB 全部 fold。
- `65s`：5.9/11.1BB 在边界控制下 fold；6.0–11.0BB call。
- `AA`、`AKs`：所有测试价格均以当前实际 3-bet-to 的 2.2×整数 raise-to 建池。

因此，本轮没有发现上一版 6.9/10.1BB 的价值核心丢失 4-bet或低 offsuit 宽跟问题被机械平移到新边界。6.0–11.0BB 的 400-call 仍是简化结构混合，不是 GTO 或收益证明。

## 4. 边界与继承检查

- 深度：79/121BB 走 baseline；80/120BB 进入 100BB chart slice。
- ante=10：不启用 PDF chart，走 baseline。
- 2.4BB BB 对 open：标记 off-grid，走 `price_aware_defense`。
- 错价历史：`ledger=conflict`，走 `ledger_conflict_safe_baseline`，动作仍合法。
- 不等筹码 121BB：不启用 80–120BB chart。
- v2.2.1 必要继承：7/10BB 的 3-bet 走廊计数、`65s`/AA/AKs 结构动作和 2.2×金额路径与 v2.2.1 收据的连续走廊一致；v2.2.2 有意改变 BTN 首入分布（从 v2.2.1 的 1074/204/48 改为 1052/46/228），因此该差异属于本轮 limp 修复范围，不冒充无回归。

## 5. 镜像、归档、SDK 与受限端点

- [manifest-v2.2.2-final-r9.json](../../.work/hu-v2-strength-v2.2.2/dist/manifest-v2.2.2-final-r9.json) 声明 image ID `sha256:5fd91dfd91ffacec5581fcca5d438ff2dd3609a6dd75f19cda87d101cf97e1e9`；`docker image inspect hu-bot:2.2.2` 实测同一 ID、`linux/amd64`，容器入口版本为 `2.2.2/V2HUBot`。
- final-r9 归档实测 SHA-256 为 `465517530ad85e7b7866948bac65124fea9fc6bf9562a16b9792d03266b02b6b`，与 manifest 一致；OCI tar 含 `index.json`、`manifest.json`、`oci-layout` 和镜像 blobs。
- 独立 `python -m chipzen validate submission --check-connectivity` 全部检查通过，仅有既有 `os` restricted-import warning。
- 独立受限 WebSocket 抽样分两次保留：初次 20 请求为 20/20 合法 `turn_action`，p50 0.538ms、max 56.017ms；随后 100 请求为 100/100 合法，p50 0.427ms、max 1.019ms。两组均核对 request ID、动作菜单和 raise-to，未观察到 `action_rejected` 或 SDK/候选合法保底触发；56.017ms 是本机单次调度尾延迟峰值，不据此推断平台超时或平台 SLA。两组都只是本地端点观察，不是平台 SLA。

## 分项裁定

| 维度 | 裁定 |
|---|---|
| engineering | **pass**：真实 GameState、合法金额/动作、0 fallback、SDK、镜像、归档和受限端点通过 |
| local_behavior | **partial pass**：自然 limp/iso 与 6–11BB 价格小树闭合；3BB/6BB 继续率仍缺收益证据 |
| strategy_quality | **not passed**：机械继续率、保护组合覆盖和 PDF 方向不等于对手条件 EV/GTO |
| action_value / Q | **not proven**：无对手条件范围和完整未来街道模型 |
| match_strength | **not proven**：无实战日志、赛事结算或 Swiss/Match 对照 |
| uploaded / platform_active | **false / false**：final-r9 未上传、未激活 |

final-r9 可记为工程通过、自然 limp/iso 与 bounded off-grid 路由的局部行为修复通过；不能记为完整翻前策略完成、盈利改善或赛事强度通过。候选源码、镜像和 manifest 未被本验收修改。
