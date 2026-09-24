# 语构 · 通用专业报告平台

面向专业报告的本机写作工作台：项目文件 → 人工确认事实 → 规则计算 → 章节候选 → Plate 编辑 → 核对 → DOCX / PDF / 审计包。

当前版本可以运行和演示，是一个有明确边界的开发基线。它不等于已经具备跨行业自动完成整份专业报告的能力。

## 先回答：30 类资料已经用于写作了吗？

**30 类都已入库并有专门的页面展示；只有部分资料已经验证了写作贡献。**

| 范围 | 当前实际状态 |
|---|---|
| 历史语料 | 澄岳精密虚构报告 `cy_tray_20260918@v2`；七组、30类、114产物文件+3辅助文件，全部进入 PostgreSQL |
| 数据完整性 | 117资产、3,462记录、728图对象；1,666原始关系与566条由明示字段派生的关系分开保存；116条SHA-256通过（清单不包含自身） |
| 展示 | 30类均有内容化预览、定位、详情、作用说明及原始入口；正常运行读取数据库 |
| 引导写作 | 49章节可查包与来源；仅 S4 需求/销售、S7.1 身份/报价、S5.2 配电冲突披露通过限定案例验证 |
| 实际消费 | 8类留下段落来源回链；另4类在工作包/核对中出现；18类尚无本轮记录级写作消费证据。回链也不等于独立必要性已证明 |
| 模型边界 | 当前模型主要接收项目事实；完整的已审核结构/规则/论断写作包尚未传给模型，列为下一优先项 |
| 未生成 | 向量文件零行；签名向量与匹配权重未生成，没有语义向量检索 |

逐类证据见 [30 类资料写作应用清单](docs/30类资料写作应用清单.md)，实际案例、遮蔽实验与失败边界见 [写作支撑验证报告](docs/写作支撑验证报告.md)。历史 `pass_with_notes` 不会成为新报告的交付许可；11/12内部证据没有完整原件，49模板关键覆盖配置仍不足。

## 文档入口与开发顺序

| 文档 | 内容 |
|---|---|
| [产品需求文档](docs/产品需求文档.md) | 用户任务、完整业务范围、已实现与规划、验收标准 |
| [详细设计文档](docs/详细设计文档.md) | 实际架构、数据模型、接口、状态、Plate/规则/模型/导出、技术缺口 |
| [开发清单](docs/开发清单.md) | 当前功能30项、后续15项的状态、优先级、依赖、验收标准；后续按此清单推进 |
| [30类资料写作应用清单](docs/30类资料写作应用清单.md) | 入库身份、实际消费、作用、未用原因与逐项结论 |
| [写作支撑验证报告](docs/写作支撑验证报告.md) | 三种包、21组案例、13次受控遮蔽、真实交互及导出核对 |
| [UI体验优化与验收](docs/UI体验优化与验收_2026-09-24.md) | 主流程、窄屏、实际前后端联动和最新55/9项回归 |
| [仓库交付记录](docs/仓库交付记录_2026-09-24.md) | 本轮文档、发布清理、安装与测试结果 |
| [写作支撑演示脚本](docs/写作支撑演示脚本.md) | 原本机QA演示步骤；其中已命名项目不会随Git复制 |
| [数据与第三方说明](NOTICE.md) | 虚构语料、公开报告、测试数据及未上传内容 |

下一轮默认顺序是 **NEXT-01 模型消费审核后写作包 → NEXT-02 必要信息等价替换实验 → NEXT-03 审计补全**。不为凑够30类而制造调用，也不在本轮重新启动已暂缓的抽取扩展。

## 技术组成

- 前端：React 19、TypeScript、Vite、Plate 53、React Flow。
- 后端：Python 3.12+、FastAPI、SQLAlchemy、Decimal受限规则、python-docx、ReportLab。
- 存储：PostgreSQL 16保存项目/语料/事实/报告；项目上传原件保存在服务端本地存储。
- 模型：可配置兼容文本对话、视觉、MinerU等接口；调用只产生待审核结果。
- 当前没有登录、多租户权限、向量库、独立图数据库或 Semantica 运行时。

Plate 已集成本流程需要的章节层级、正文、引用、列表、表格、链接、事实引用、斜杠菜单、撤销重做、粘贴、保存/比较/导出；没有复制 Plate 演示站全部插件。

## 从 GitHub 安装

### 1. 环境

需要 Git、Docker Desktop（或 Docker Engine + Compose）、Node.js 20.19+ / 22、Python 3.12+ 和 `uv`。当前验证环境为 macOS；Linux和Windows尚未完成整轮安装验证。首次安装和拉取PostgreSQL镜像需要网络，配置模型前可以走手工事实与确定性写作流程。

```bash
git clone https://github.com/tianqi0514/924yugou.git
cd 924yugou
docker compose up -d --wait
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r backend/requirements.lock
uv pip install --python .venv/bin/python --no-deps -e backend
npm --prefix frontend ci
```

`frontend/package-lock.json` 与 `backend/requirements.lock` 锁定交付依赖；更新依赖应单独测试。所有后续命令如无说明均在仓库根目录执行。

### 2. 初始化数据库与内置资料

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/bootstrap.py --with-public-reports
```

首次导入及公开长报告解析可能需要数分钟。此命令先创建项目表，再导入并核验澄岳语料，最后建立四份公开报告各自的演示项目。可重复运行，不清空既有事实和报告。仅需澄岳时省略 `--with-public-reports`。四个普通演示项目只有原件和定位片段，事实台账为空，不会自动批准候选。

首次启动后端后会登记澄岳只读项目。入库后正常浏览从 PostgreSQL 读取语料，源文件目录仅用于初始化、核验与重建。禁止执行历史包内的 `validate_package.py`。

### 3. 启动后端、前端

终端一，在仓库根目录：

```bash
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端二，在仓库根目录：

```bash
npm --prefix frontend run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。健康接口：[http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)；接口结构：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。前端`/api`代理到后端8000。

数据库仅映射 `127.0.0.1:55432`，前后端默认也只监听本机。Compose中的 `report / local_development_only` 是公开的本机开发账户，不能作为团队部署凭据。

停止：前后端终端分别按Ctrl-C，再运行 `docker compose down`。常规停止不要加 `-v`，该选项会删除数据库卷。

## 第一次使用

### 查看30类历史资料

1. 项目下拉框选择“澄岳精密”，打开“项目资料”。
2. 按七组进入类别，查看业务记录、图谱和来源；“作用说明”按需打开。
3. #23可核对800/650 kW未解决冲突；#11显示“向量未生成”。这些是历史数据，不会被自动填入新项目。

### 不依赖模型走一遍写作

1. 新建普通项目，上传 [合成QA原件](test-fixtures/ux-integration/项目事实_QA合成原件.docx)。它只验证软件，不是业务证据。
2. 在“项目事实”创建数值事实：`first_year_demand`需求300000套、`qualified_capacity`合格能力254016套、`planned_sales`计划销售量暂不填。逐个预览并确认两个输入，并绑定到上传原件的对应片段。
3. 在“规则计算”创建目标 `planned_sales`，表达式 `min(first_year_demand, qualified_capacity)`，得到254016套。
4. 在“报告写作”新建报告，选澄岳v2只读参考和S4章节。“映射事实”将N017/N034/N035分别映射到以上三个字段，核对必选记录和来源，预览候选。先取消一次，再重新生成并确认加入报告。
5. 在Plate输入 `/` 插入表格，使用“事实”插入引用；预览改动并保存，刷新检查。进入核对导出，处理阻断项，人工核对后下载ZIP。
6. 把需求预览为0：计算也为0，取消不改变300000。确认后旧引用过时，正式导出被阻断；更新正文、绑定当前修订对应的新证据并重新核对后才能交付。

ZIP含 `report.docx`、`report.pdf`、`audit.json`。关键冲突、缺证据或过时引用不会静默放行；允许的预审稿明确标示保留事项。点击正文事实可追溯项目事实、规则、输入与原文。澄岳的三个章节演示不应套用到事故/审计等不同类型报告。

### 使用模型抽取和起草

在“模型配置”新增真实可达的端点、model与API Key，测试连接并绑定“事实抽取”“章节起草”或“扫描页识别”。预设中的本机地址只是连接示例，不代表内置了模型服务。已保存的本机配置不会被模板替换。

上传PDF/DOCX后按页抽取，核对候选的主语、单位、值、时点与引用后确认入账；候选不等于项目事实。起草结果先待审，取消不会入稿。模型未配置或不可用会显示状态，不用固定文字冒充模型输出。图片生成/向量模型可登记和测试，当前没有写作功能消费它们。

## 配置与敏感数据

| 环境变量 | 用途 / 默认 |
|---|---|
| `DATABASE_URL` | SQLAlchemy PostgreSQL URL；默认本机55432的report_platform |
| `DOCUMENT_STORAGE_DIR` | 上传原件目录；默认仓库storage/documents |
| `MODEL_SETTINGS_DIR` | 模型配置目录；默认storage/model-settings |
| `MODEL_CONFIG_FERNET_KEY` | 可选外部Fernet主密钥；未设置时生成权限受限的本机master.key |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 可选旧文本模型环境；可从配置页导入受保护存储 |
| `REPORT_PLATFORM_API_TARGET` | 启动Vite时覆盖API代理；默认http://127.0.0.1:8000 |

环境变量须由启动进程传入；当前后端不自动读取仓库 `.env`。密钥仅在服务端加密保存，接口显示掩码。`storage/`、`.env*`、主密钥、备份、日志、个人导出全部已忽略；不要强制加入Git。Git克隆不会携带原机器的项目、模型设置或QA成稿。

## 备份、升级与重建

升级既有实例前停止写入并备份数据库：

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U report -d report_platform -Fc > backups/before-upgrade.dump
```

同时在安全位置备份 `storage/`；只备份数据库无法恢复上传原件和模型密钥。运行 `bootstrap.py` 可补齐当前新增表与语料数据；本项目尚无完整版本化迁移系统，升级前须核对[开发清单NEXT-10](docs/开发清单.md)。

冻结语料需要重新生成数据库投影时：

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/import_builtin_corpus.py --rebuild
PYTHONPATH=backend .venv/bin/python backend/scripts/verify_corpus_file_purposes.py
```

`--rebuild`先核验源复制件，再事务化重建语料投影；不是重新抽取报告，也不执行历史脚本。恢复数据库属于覆盖操作：停后端、确认备份时点并保留当前快照后，可使用 `pg_restore --clean --if-exists --no-owner --no-privileges` 恢复到指定库；此操作会失去备份之后的数据，不在启动命令中自动执行。

## 测试与真实结果

### 独立测试数据库

首次建立测试库：

```bash
docker compose exec -T postgres createdb -U report report_platform_test
```

库已存在时无需重建。后端测试会清理此库，所有重建测试先断言库名；不得改成应用库，也不要与使用同一测试库的浏览器验收或另一轮pytest并行。

```bash
cd backend
../.venv/bin/pytest -q tests
```

前端构建与浏览器组件交互测试：

```bash
cd frontend
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright自动启用5174端口，9项测试使用受控API响应，不要求生产后端。它们验证交互，不等同真实数据库端到端；真实前后端联动目前另有人工浏览器验收记录，自动化补齐列为NEXT-13。

截至2026-09-24，最近完整回归为 **后端55/55、前端9/9，构建通过**。实际浏览器已验证取消/确认、Plate斜杠/表格/事实引用、保存刷新、正式导出、0值影响和旧引用阻断；DOCX/PDF/审计包已解包核查。三节历史写作试验稿因关键冲突及缺新项目原件而只能预审，是预期阻断；合成QA原件只证明软件机制。

本轮仓库安装核查与针对性测试见[交付记录](docs/仓库交付记录_2026-09-24.md)。在线模型质量以各次[公开报告验证](test-fixtures/public-reports/README.md)为准，不能把历史请求成功当作当前模型可用。

## 目录

```text
backend/app/          API、数据模型、规则、语料、抽取、写作、导出
backend/scripts/      初始化、导入、核验和离线评估
backend/tests/        独立测试数据库上的后端回归
frontend/src/         项目/资料/事实/规则/报告/模型界面
frontend/tests/       Playwright交互测试
demo-data/            冻结虚构语料复制件（用于导入）
test-fixtures/        公开报告、合成QA、预注册实验与结果
docs/                 当前PRD、详细设计、清单与验收证据
compose.yaml          仅本机暴露的PostgreSQL
storage/              运行时上传文件/模型设置（不上传Git）
backups/              本机备份（不上传Git）
```

## 当前限制

- 30类全入库不等于30类均必要；局部写作固定构造器的缺项报错只能证明实现耦合。更多报告类型、真实新项目证据、等价替换实验尚缺。
- 模型写作包输入、全过程审计、规则独立版本和不可变交付快照仍需补齐。完整语义Diff、正文反向修订事实尚未实现。
- 证据绑定和数字闸门主要为位置/词面核查，不能独立判断真实性、因果或专业结论。历史内部附件大多不完整。
- PDF跨页表、复杂合并单元格、OCR字框回链与长任务恢复不足；多页抽取一次最多8页，每页最多12000字，关闭页面可能中止后续页。
- DOCX复杂原版式、PDF宽表、中文字体细节与专业报告目录页眉仍需扩展测试。
- 无认证/权限，不应直接暴露到团队网络；需完成迁移、备份恢复和部署安全清单后再做团队试点。
- 主前端包约1.39MB（gzip约432KB）有体积提示；已验证流程可用，按页面拆包列为后续性能项。

数据来源与使用边界见 [NOTICE](NOTICE.md)。
