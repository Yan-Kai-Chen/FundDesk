# 构建与验证

在 README 的安装步骤后，可用 `tools/quality/test.command` 执行测试与前端生产构建。所有生成内容写入被忽略的目录。Python 版本限制为 3.12，前端通过 packageManager 声明固定 pnpm 版本。

Mac 桌面构建：

```sh
src/backend/.venv/bin/python tools/desktop/build.py
```

只在 Apple Silicon Mac 构建。输出包含应用目录、ZIP 和本地构建清单。它们可能包含构建路径和本地身份，不属于可提交的源码。脚本没有自动上传步骤。`--install` 会安装到本机，只有需要本地安装时才使用；源码验证不需要安装。

每份克隆先运行 `tools/dev/setup_workspace.py`。已有身份不会被覆盖，不应将本地 `desktop.json` 复制给其他人。默认签名为 ad-hoc；签名证书、密码和 Keychain 资料保持在本机。

Swift 策略测试可在 macOS 单独编译到忽略目录：

```sh
mkdir -p data/runtime/swift-test
xcrun swiftc -swift-version 5 src/desktop/DesktopCore.swift tests/desktop/CoreTests.swift -o data/runtime/swift-test/CoreTests
data/runtime/swift-test/CoreTests data/runtime/swift-test/workspace
```

冻结后端的隔离验证：

```sh
src/backend/.venv/bin/python tools/quality/smoke_desktop.py
```

此工具在临时目录创建合成数据库，验证内置运行环境、模板、分仓、备份与重启。不要把本地运行输出直接作为公开附件。

