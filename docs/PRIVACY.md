# 数据隐私与提交规则

可以提交源码、锁文件、空模板、合成测试夹具和本仓库说明。禁止提交真实数据库及 WAL、原图、交易记录、账户资料、资金计划、备份、运行日志、截图、个人工作记忆、凭据、签名证书及含本机路径的安装产物。

`data` 默认全部忽略，只保留目录说明；`input` 只允许模板；`config/app/desktop.json` 是每个克隆的本地身份，不进入 Git。构建、测试、缓存及备份输出均不提交。模板不填入默认交易渠道或个人资金偏好。

发布版初始真实基金列表、持仓、计划、分类与标签为空。用户自行新增后才形成真实资料。支持公开行情的代码清单只用于数据解析与身份校验，不会建立任何真实基金或订单。

测试在临时工作区生成合成交易；其中的产品代码用于适配和规则覆盖，金额、日期及份额不代表使用者的账户记录。压缩研究示例须明确标记 `SYNTHETIC`。

首次提交前运行：

```sh
python3 tools/release/check_public_tree.py
```

建立 Git 仓库后检查暂存/已跟踪文件：

```sh
python3 tools/release/check_public_tree.py --tracked
git diff --cached --stat
```

检查工具只报告文件名与规则，不回显匹配内容。发布候选还应使用本地秘密扫描工具检查最终 ZIP 解压内容。自动规则不能识别所有个人背景，仍需确认所添加的说明、样例与截图均可公开。

不要在公开 Issue、PR 或日志中粘贴数据库、真实交易与完整诊断包。`.gitignore` 不会自动移除已跟踪文件；历史中出现的敏感数据需另行清理。[GitHub 忽略文件说明](https://docs.github.com/en/get-started/git-basics/ignoring-files)、[敏感数据处理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。

