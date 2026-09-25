# 独立前后端集成验证

在 `frontend/` 执行：

```bash
npx playwright test --config playwright.integration.config.ts
```

测试只允许连接 `report_platform_test`。需要其他连接参数时，使用 `REPORT_PLATFORM_TEST_DATABASE_URL` 指向同名数据库。配置和 Python 夹具脚本均会检查数据库名称。后端仅监听 `127.0.0.1:8001`，前端仅监听 `127.0.0.1:5175`；上传原件和模型设置使用 `tmp/integration/` 下独立目录。

用例使用真实 HTTP，不拦截 API。每次通过界面创建一个带时间戳的新 QA 项目，不清空已有测试记录。失败时保留 Playwright trace、截图和 HTTP/浏览器异常附件。DOCX 夹具由 `backend/scripts/setup_integration_qa.py` 生成，导出 ZIP 由 `backend/scripts/check_integration_export.py` 解包检查。

写作闭环中，事实值改为 0 后，旧 S4 候选的规则版本必须阻断导出；测试从界面重新预览并“替换本章”，保留其他手工段落和表格，然后更新其事实 token、重新核对并导出。
