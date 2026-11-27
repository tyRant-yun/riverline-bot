# Riverline Bot

独立HU Bot开发仓库，当前策略基线为v2.2.2 final-r9。最近独立工程回归381通过、2项明确跳过；行动价值、整体强度和GTO未证明。[当前状态](docs/STATUS.md)

总产品：[poker_coach](https://github.com/tyRant-yun/poker_coach)；真实求解：[riverline-solver](https://github.com/tyRant-yun/riverline-solver)。本仓库保持私有，原创策略未自动指定开源许可证。

## 开发与产品接入

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q --import-mode=importlib
.venv/Scripts/python.exe scripts/package-core.py
```

`build/core/`为固定五文件包，产品可用 `npm run bot:setup -- ../riverline-bot` 安装相同固定版本。离线适配器入口：

```powershell
.venv/Scripts/python.exe adapters/riverline/worker.py build/core
```

该入口接收有界JSONL，不连接比赛平台。更改策略需要新版本/指纹和对应集成验证，不能冒用旧v2.2.2标识。

## 文档与目录

- [状态/目录/边界](docs/STATUS.md) · [后续开发](docs/DEVELOPMENT.md)
- [来源清单](provenance/baseline.json) · [第三方记录](THIRD_PARTY_NOTICES.md)
- [历史交付与旧说明](docs/archive/README.md) · [历史研究数据](reports/README.md)

`submission/hu_bot/v2_*`为当前核心，`submission/bot.py`主入口选择v2；旧策略保留作对照。`adapters/riverline`归属产品适配，`lab/tests/tools`用于离线研究与回归，`references`是冻结测试对照。完整文档入口见[docs](docs/README.md)。
