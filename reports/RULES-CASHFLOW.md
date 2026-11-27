# v1.9.1 金额、结算和赛程前置核对

来源状态：官方公开 [Poker Game State Protocol](https://github.com/chipzen-ai/chipzen-sdk/blob/main/docs/protocol/POKER-GAME-STATE-PROTOCOL.md) 是草案协议；本地 `lab/match.py` 是研究引擎。尚无此截图的原始 SDK 消息或平台 `round_result`。

| 合约 | 已核对 | 证据边界 |
|---|---|---|
| `raise.amount` | raise-to 本街总额；`min_raise`/`max_raise` 也是总额 | 官方协议 3.3、3.5、5.4；本地回归与完整手重放 |
| `pot` | 当前街已投筹包括在当前底池；`call` 可短额全下 | 官方协议 2、3.3；本地筹码守恒 |
| 本地不等额全下 | 超额投筹按双方最终投入之差返还，可争底池不含该差额 | `tests/test_cashflow_contract.py` 和 `lab/match.py`；不是平台结算真值 |
| 平台不等额全下 | `round_result` 有 `pot`、`payouts`、`stacks`、完整 `action_history`；公开协议没有未跟注额返还字段或这条 7139/155 示例 | 未验证具体退款时点、pot 呈现和终局 EV |
| 平台赛程 | `match_start.game_config` 给初始盲注/ante，缺实际赛事逐手盲注变化消息 | 本轮固定 100BB 零 ante 及截图前缀均标 `assumed_for_research` |

截图路径的明确假设：河前可争底池 7654；我方先下注 5052 后只剩 155。若对手 raise-to 7139，而我方短额 call155，则对手最后 1932 未获跟注；本地有效 raise-to 5207、最终可争底池 18068，和 7139 后退 1932 的**假设净额**相同。初始 5052 下注的价值须依据对手弃牌、跟注、加注的分别条件范围衡量；面对加注后的 155 跟注须另按加注条件范围评价。此算式不能证明平台怎样记录投筹、退款或 payout。

取得平台真值所需可重放字段：同一 `match_id`/`round_id` 的 `match_start.game_config`、截图手完整 `round_start`、每个 `turn_request.state`（尤其河牌下注前和加注后 `pot`、双方 `stack`、`to_call`、`min_raise`、`max_raise`、`action_history`）、相应 `turn_action`/`turn_result`，以及 `round_result.result` 的 `pot`、`payouts`、`stacks`、`action_history` 和 showdown。赛程还需后续各手的盲注/ante 及手数。未取得前 `terminal_ev_enabled=False`，所有终局与范围实验标明本地假设。
