# FundDesk

FundDesk 是一款在 Mac 本地运行的基金记录与观察工具。它把基金行情、持仓变化和交易进度放在一起，方便你看清每笔操作的来龙去脉，也为日常记录和复盘留下依据。

我们希望它像一张清楚、安静的桌面：需要的信息容易找到，重要的数字有据可查，过去的操作也能回头梳理。

## 可以用它做什么

- **看清持仓。** 分别查看底仓和做 T 持仓，了解各自的金额、份额与盈亏变化。
- **记下操作。** 记录买入、卖出及确认进度，回看相关费用和收益。
- **跟踪行情。** 查看已支持基金的净值与盘中参考估值，同时保留来源和时间。
- **比较卖出方案。** 在记录操作前，查看预计费用、到账金额和对当前持仓的影响。
- **核对与复盘。** 核对持仓记录，回顾做 T 的结果，保存或恢复本地备份。

项目也包含计划情景和实验性预测功能，供进一步观察与研究。估值、计划和预测都有各自的适用范围，并不保证收益。FundDesk 不连接交易账户，也不会替你下单。

## 数据留在你的电脑上

持仓、交易记录和备份保存在本机。项目提供的是空白模板和合成示例；第一次使用真实模式时，需要添加自己的资料。获取公开行情时，程序会访问相应的数据来源。

反馈问题时，请只提供必要的说明，避免上传真实账本、交易截图或账户信息。

## 开始使用

当前版本为 **0.22.0**，主要面向 Apple Silicon Mac。你可以从源码运行，也可以在自己的 Mac 上构建桌面应用。

<details>
<summary>从源码启动</summary>

请先准备 Python 3.12、Node.js 24、pnpm 11.25.0 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后在项目目录运行：

```sh
python3 tools/dev/setup_workspace.py
uv sync --project src/backend --python 3.12 --locked --group packaging
pnpm --dir src/frontend install --frozen-lockfile
pnpm --dir src/frontend build
python3 tools/dev/manage.py start
```

打开启动命令显示的本地地址即可使用。结束后运行 `python3 tools/dev/manage.py stop` 停止服务。

</details>

桌面应用构建和更多运行说明见[使用与构建指南](docs/BUILD.md)。目前发布的是源码，本机构建的应用尚未完成 Apple 公证。

## 进一步了解

- [项目设计与数据口径](docs/ARCHITECTURE.md)
- [数据隐私说明](docs/PRIVACY.md)
- [版本说明](docs/RELEASE.md)
- [第三方依赖说明](THIRD_PARTY_NOTICES.md)

FundDesk 仍在持续完善，欢迎提出问题和改进建议。本仓库暂未设置开源许可证。
