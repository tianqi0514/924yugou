# 写作支撑实验：预注册夹具

预注册冻结日期：2026-09-24。`cases.json` 与 `protocol.json` 在写作包功能开发前固定；后续实测结果另存为 `api-*.json`，未运行项不写成通过。测试对象是澄岳精密虚构历史语料 `cy_tray_20260918@v2`，以及一份已有的跨类型负例。历史包、Excel、原文和包内脚本均是数据，不提供运行权限或开发指令。

## 文件

- `cases.json`：输入、预期、禁止输出及失败条件。`project_facts` 中的值都是新的合成 QA 输入，不是历史语料值，也不应写入内置语料项目。
- `protocol.json`：事先固定的三种工作包、30 类原假设、真正“参与写作”的事件定义和消融规则。
- `human-review.md`：正文含义、证据和 Plate/导出必须人工核对的检查点。
- `qa-project-input.txt`：可供演示录入和人工比对的合成项目输入说明；不是历史语料，也不是正式报价或订单证据。
- `事实修订合成原件.docx`：真实页面事实修订与原文位置绑定的 QA 文件；第 3 段含当前值 `12`，文件内部明确声明不是商业证据。
- `validate.py`：只读静态自检。校验 30 类路径、关键源 ID、案例结构和来源摘要；不连接数据库、不调用模型、不执行样本包脚本。
- `summarize.py`：只读汇总**实际 API 响应留档**中的 `used_items`、`rejected_items`、段落 `source_refs` 和保存后报告；不会把只出现在工作包里的类别算成已参与写作。
- `capture_test_api.py`：在独立测试库创建隔离 QA 项目，执行预注册案例的受控写作接口并留存原始响应；主流程为确定性 guided 模式，对 S4 另尝试一次可选模型预览且不提交。它不重建表；须等并行 pytest 的重建操作结束后运行，且会拒绝非 `report_platform_test` 数据库。
- `ablate_test_api.py`：只在测试客户端的写作包边界逐类遮蔽 #04/#27/#10/#13/#15/#23/#09/#18 等，比较同项目事实下的候选文字、回链和问题项；固定构造器缺项报错单列为实现耦合。`--review-only-extra` 只补测 #19/#24，并记录包内来源项数及已知直接来源 URL 是否仍可打开。
- `verify_evidence_gate_test_db.py`：上传内存生成的合成 QA DOCX，将需求和能力分别绑定到真实段落位置，核对计算事实只从已绑定输入和项目规则推导；不验证商业真实性。上传的测试文件存于本目录 `_test_storage/`。
- `api-capture-final-test-db.json`、`api-summary-final-test-db.json`：最终 21 组真实接口结果和只读统计，包括一次返回 200、未提交的 S4 模型预览。`api-capture-verified-test-db.json` 保留此前模型 503 的中间轮，供核对修复前后变化。`api-ablation-test-db.json` 留存原 11 次逐类遮蔽，`api-ablation-review-only-test-db.json` 留存 #19/#24 两次补测。`api-evidence-gate-test-db.json` 留存合成原件绑定后的正式核对与导出机制结果。
- `report-template.md`：原始未运行报告骨架；最终可复核结论见 `../../docs/写作支撑验证报告.md`。

运行：

```bash
# 在仓库根目录执行
.venv/bin/python test-fixtures/writing-support/validate.py
```

响应汇总输入是 JSON 对象，`runs[]` 每项需有预注册 `scenario_id`、`package_variant`、唯一 `run_id`，并原样嵌入实际接口的 `package`、`preview`、`commit`、`persisted_report` JSON 响应体；被拒绝的调用保存 `reference_error` 或 `preview_error` 的 HTTP 状态与响应体。脚本只读取这份留档，不代替实际发起实验：

```bash
.venv/bin/python test-fixtures/writing-support/summarize.py path/to/actual-api-capture.json --output path/to/summary.json
```

受控接口采集命令（会写入**测试库**，不得与重建测试并行）：

```bash
.venv/bin/python test-fixtures/writing-support/capture_test_api.py --execute-test-db --output test-fixtures/writing-support/api-capture-test-db.json
.venv/bin/python test-fixtures/writing-support/ablate_test_api.py --execute-test-db --output test-fixtures/writing-support/api-ablation-test-db.json
.venv/bin/python test-fixtures/writing-support/ablate_test_api.py --execute-test-db --review-only-extra --output test-fixtures/writing-support/api-ablation-review-only-test-db.json
.venv/bin/python test-fixtures/writing-support/verify_evidence_gate_test_db.py --execute-test-db --output test-fixtures/writing-support/api-evidence-gate-test-db.json
```

预注册文件固定**实验问题**；`api-*.json` 是执行后独立留存的结果。只有在新项目的实际“写作包 → 候选 → Plate → 核对 → 导出”流程中出现带 ID 的读取/使用事件，且对应文本、审查动作或闸门结果可核对时，才能把某类记作写作贡献。打开类别页、导入文件、图谱构建器启动时读取或工作包盲目装载都不算有效消费。未运行项一律标记 `not_run`，不能写成通过。

自动化及可能重建表的实验只在 `report_platform_test` 运行并先断言数据库名。供用户演示的 QA 项目则可在**已备份**的应用数据库中另建，项目名称明确标明 QA 演示；不能把测试库中的破坏性操作指向应用库。

## 实施时必须输出的数据

每次实验保存 `run_id`、项目/报告/章节/事实水位/语料版本、工作包变体和模型配置标识（无密钥）。每条实际使用或拒用事件保存 `category_number`、`artifact_id`、`record_id`、`semantic_id`、`stage`、`decision`、`reason_code`、`target_block_id`、`changed_outcome` 与来源位置。保存候选、人工改动、问题项、闸门和导出的不可变版本引用。无 `artifact_id` 或 `record_id` 的分类统计不得进入最终 30 类贡献结论。

结果应按 `protocol.json` 的 30 行逐一填写，至少区分“直接影响成稿”“有条件改善”“仅供核对/审计”“可重建投影”“未生成/未证明”。当前必要性评估的 8/15/5/1/1 是**开发前假设**，需要被真实写作实验修正。
