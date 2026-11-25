# 贡献指南

欢迎报告使用问题、改进中文文档或提交代码。涉及报销流程的新功能，可以先在 Issue 里说明使用角色、现有步骤和期望结果，方便讨论数据归属和历史兼容。 [新建 Issue](https://github.com/wang-jingcheng/LabInvoice/issues/new/choose)

## 本地开发

按 README 创建并进入虚拟环境，从项目根目录运行。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

需要检查界面时，安装浏览器并运行隔离的端到端测试。可用 `--help` 查看各功能对应的测试套件。

```bash
python -m playwright install chromium
python scripts/run_browser_checks.py --suite full
python scripts/run_browser_checks.py --suite member-accounts
```

Linux 缺少浏览器系统依赖时，可执行 `python -m playwright install --with-deps chromium`。若已有 Chrome 或 Chromium，可将 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指向其可执行文件。测试为每次运行生成独立演示数据库，截图和结果写入 `test-results/browser/`。

## 提交改动

- 尽量让每个 PR 聚焦一个问题，并写明用户如何验证。
- 改动金额、归类、归档和迁移时，使用临时数据验证旧记录、票据原件和导出结果。
- 使用合成样例复现问题，公开内容仅放脱敏日志、截图和测试票据。
- 讨论聚焦实际用例与实现，尊重参与者。

安全问题请按 [安全报告说明](SECURITY.md) 私下提交。
