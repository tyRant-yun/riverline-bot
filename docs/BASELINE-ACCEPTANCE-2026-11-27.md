# 独立仓库基线验收

2026-11-27，在新目录 `E:\riverline-bot`、Python3.14.3和独立虚拟环境验证。

- 从SHA-256固定的v2.2.2 final-r9源码ZIP提取228个源码/历史JSON资产，未带临时日志、环境、镜像或凭据；完整清单见 `provenance/baseline.json`。
- 首次pytest为379通过、2失败、2跳过；失败均因旧实验依赖仓库旁的v1.3源码。将必要18文件只读对照收入 `references/hu-overbet-v1.3.0` 并登记逐文件SHA-256，仅修改 `lab/conditional_eval.py` 的离线查找路径。未修改 `submission/hu_bot` 当前策略。
- 最终 `python -m pytest -q --import-mode=importlib`：**381 passed, 2 skipped，67.68秒**。两项跳过分别缺可选v1.2.3对照及未随发行分发的v1.0工件，没有伪造其通过。
- `scripts/package-core.py` 验证并打包五个与总产品固定摘要一致的核心文件。产品的独立仓库安装、旧冻结ZIP安装、损坏核心拒绝且不留部分安装输出，三项实测通过。

上述结果只说明独立仓库工程基线可运行。没有连接比赛平台、没有上传替换现有版本，没有证明行动价值、整体胜率或GTO。原始 `E:\hu bot` 冻结归档保持原哈希。参考目录只用于离线历史对照，不能进入实时决策依赖。

后续策略修改须建立新版本和新的产品允许指纹；`provenance/baseline.json` 是导入时的历史，不要求修改后的实验脚本仍与初始字节相同。不要覆盖原始来源收据。
