# 独立前后端集成验证

在 `frontend/` 执行：

```bash
npx playwright test --config playwright.integration.config.ts
```

测试只允许连接 `report_platform_test`。需要其他连接参数时，使用 `REPORT_PLATFORM_TEST_DATABASE_URL` 指向同名数据库。配置和 Python 夹具脚本均会检查数据库名称。后端仅监听 `127.0.0.1:8001`，前端仅监听 `127.0.0.1:5175`；上传原件和模型设置使用 `tmp/integration/` 下独立目录。

用例使用真实 HTTP，不拦截 API。每次通过界面创建一个带时间戳的新 QA 项目，不清空已有测试记录。失败时保留 Playwright trace、截图和 HTTP/浏览器异常附件。DOCX 夹具由 `backend/scripts/setup_integration_qa.py` 生成，导出 ZIP 由 `backend/scripts/check_integration_export.py` 解包检查。

写作闭环中，事实值改为 0 后，旧 S4 候选的规则版本必须阻断导出；测试从界面重新预览并“替换本章”，保留其他手工段落和表格，然后更新其事实 token、重新核对并导出。

`eia-writing.spec.ts` 使用同目录内的江门公开环评 PDF 建立独立测试项目，只取第5页投资数据。它覆盖两个章节、原件位置、预算上限20→15万元的条件翻转、取消候选、过时引用、逐项更新、核对和情景分析导出。现在还会在 Plate 中人工改写条件句与结果表标签，保存刷新后核对并局部更新，确认人工文字保留。测试模型调用仍由本机专用QA项目单独执行，不将接口替身测试写成真实模型验收；具体文件解包和页面目视结果见 `docs/环评报告写作验收_2026-09-26.md` 与 `docs/人工编辑局部更新验收_2026-09-26.md`。
