# Riverline Bot

Riverline 的独立 Bot 开发仓库。总产品：[poker_coach](https://github.com/tyRant-yun/poker_coach)；真实求解：[riverline-solver](https://github.com/tyRant-yun/riverline-solver)。本仓库保持私有，没有给原创策略指定新的开源许可证。

当前基线为 HU v2.2.2 final-r9 的固定源码发行，来自 `E:\hu bot` 的只读冻结归档。原始 SHA-256、逐文件指纹见 `provenance/baseline.json`；历史说明见 `docs/source-release-readme.md`，原工程/局部行为结论见 `docs/evidence/`。这些历史收据不等于本仓库重新验收，更不等于行动价值或整体强度已证明。

## 目录与边界

- `submission/hu_bot/v2_*`：当前 HU 核心；`submission/bot.py` 的主入口选择 v2。同目录旧策略保留为对照，不擅自删除。
- `adapters/riverline/`：总产品使用的有界JSONL适配器及固定核心哈希，只接收行动者自己的牌与公共事实。
- `lab/`、`tests/`、`tools/`：离线实验、回归和打包工具。历史工具可能仍引用旧候选/平台路径，使用前核对范围。
- `reports/`：随源码归档带入的历史JSON/分析脚本；未带入临时日志或平台凭据。
- `scripts/package-core.py`：产生产品本地适配器需要的五文件核心包，不连接比赛平台。

产品拥有规则/手牌/会话权威；Bot只能提出动作。真实Solver、数据库、HTTP产品服务和LLM不得进入实时决策热路径。工程合法、局部行为、行动价值、整体强度和平台上线分别记录；当前强度未证明，不自动替换已上传的平台版本。

## 本地开发

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q --import-mode=importlib
.venv/Scripts/python.exe scripts/package-core.py
```

打包输出在忽略的 `build/core/`，可运行 `python adapters/riverline/worker.py build/core` 接收版本化JSONL。该入口不会建立平台连接。需要改变产品适配器允许的策略文件时，必须同时发布新版本/指纹与兼容测试；不能改完核心仍冒用旧v2.2.2标识。

旧 `E:\poker agent\riverline-poker-coach` 的证据教学、范围和外部日志设计可继续开发，但应按模块适配，不能把其FastAPI/数据库/整个旧产品带入Bot。详细采用裁决位于总产品的 `docs/riverline-repository-organization-2026-11-27.md`。
