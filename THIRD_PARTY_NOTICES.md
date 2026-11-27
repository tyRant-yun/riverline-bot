# 第三方与来源

本项目策略、范围 likelihood、状态层、评估器、记忆与实验工具为本次编写的原创实现。
没有复制 WizGTO、CFR-Poker-Bot、TexasSolver、ARES-HU 等项目源码，没有引入 AGPL solver。
两段用户提供的聊天规划作为需求与架构参考，原始 Canonical 存于 docs。

运行依赖：

- [Chipzen SDK / chipzen-bot 0.4.0](https://github.com/chipzen-ai/chipzen-sdk)：Apache-2.0。
  使用公开 Bot、GameState、Action、run_bot API；SDK 自带许可证随 Python distribution 保留。
- [websockets 15.0.1](https://github.com/python-websockets/websockets)：BSD-3-Clause，SDK 网络传输依赖。
- [Python](https://www.python.org/) 与 Debian 官方 Python 容器基础镜像：依各自随附许可分发。

仅开发依赖，不进入上传镜像：

- [Treys 0.1.8](https://github.com/ihendley/treys)：MIT，用于独立评估器 oracle 和离线结算。
- pytest：MIT；Ruff：MIT。

打包镜像保留 pip 安装后的 dist-info / licenses，不移除依赖许可文件。
未给用户原创策略自动指定新的开源许可证；策略所有权与发布许可由用户决定。
