# 澄岳精密｜30类产物原始格式包

## 1. 本包用途与来源

依据《语义图谱产物·存储·调用·双向 Diff 规范》V2.0 的30类产物清单，将上一版JSON抽取包恢复为原生文件格式。本次是**格式恢复与重新封装**，没有重新抽取业务事实，没有改变节点编号、业务数值、计算规则、原文或字符偏移。

业务原文为《电池托盘焊接与检测技改项目可行性研究报告·内部评审稿》V1.0。企业、项目及数据均为虚构测试样本。上一版未使用人工核对答案；本次转换仅使用既有抽取包，并与已上传Word原件核对字节一致性。

包版本为 `v2`，`supersedes: v1`。原报告版本仍为V1.0；包版本变化不等于报告内容变化。

## 2. 文件格式与目录

```text
manifest.yaml
meta.yaml
provenance.yaml
outline.yaml
source/
  original.docx
  normalized.md
  offset_map.json
  assets/
    T00.csv … T17.csv                 # 18份独立表格；原件无图片素材
chunks/
  chunks.jsonl                       # 194条，每行一个完整JSON对象
  skeletons.jsonl                    # 194条，每行一个完整JSON对象
  embeddings.parquet                 # 零行、带schema，未生成真实向量
graph/
  nodes.yaml
  rules.yaml
  relations.yaml
  claims.yaml
  invariants.yaml
  graph.yaml
evidence/
  evidence.yaml
  excerpts/
    EX-*.md                          # 20份原文摘录
reasoning/
  derivation.yaml
  decisions.yaml
  trace.jsonl                        # 原入库轨迹76条，未虚构新的抽取事件
quality/
  conflicts.yaml
  gaps.yaml
  gate_report.yaml
style/
  style_profile.yaml
  section_templates/
    <section_id>.yaml                # 49份，含封面、说明、业务章节及附录子节
index/
  node_index.json
  term_index.json
  signature.yaml
README.md                            # 附加说明
validate_package.py                  # 附加校验脚本
SHA256SUMS                           # 附加文件校验和
```

原规范的30项是**30类产物**，其中3项是目录通配符。本包展开后共有114个产物文件，另有3个交付辅助文件，总计117个文件。没有为了凑“恰好30个文件”再次聚合目录。

`manifest.yaml` 逐项登记原清单编号、路径、格式、工序、主存/副本声明、可变性、Agent访问形式及实际文件清单。其量级按真实内容决定，没有按规范中的示例KB数补空数据或截断内容。存储声明不表示图库、PG、向量库和MCP服务已实际部署。

## 3. 结构约定

移除了上一版为“每项一个JSON”增加的统一外层封装，而不是只把后缀改名：

- `manifest.yaml`、`meta.yaml`、`provenance.yaml`、`style_profile.yaml`、`gate_report.yaml`、`signature.yaml`为顶层对象。
- `graph/nodes.yaml`、`rules.yaml`、`relations.yaml`、`claims.yaml`、`invariants.yaml`，以及`evidence/evidence.yaml`、`reasoning/derivation.yaml`、`decisions.yaml`、`quality/conflicts.yaml`、`gaps.yaml`为顶层列表。
- `outline.yaml`含`sections`列表及覆盖信息；`graph/graph.yaml`为含节点、规则等集合的合并快照。
- `index/node_index.json`顶层直接以`N001`等节点ID为键；`index/term_index.json`保留`terms`、`exact_label_index`及匹配策略。
- 三个JSONL文件逐行存放记录，不使用数组包装，也不插入注释行。
- 每个CSV保存原表格矩阵及换行；Markdown正文、摘录不添加额外标题或说明，以免改变偏移与内容哈希。
- 每节模板有独立YAML，模板中的骨架引用指向`chunks/skeletons.jsonl#<chunk_id>`。

原规范仅给出部分结构示例，没有为全部文件提供完整机器schema。本包仍保留上一版已公开的业务扩展字段（例如`decimal_value`、详细位置、守卫与渲染格式），不声称进行了字段级的完整官方schema认证。此前外层附带的执行契约、渲染说明、来源映射等辅助元数据转存于`provenance.yaml`的`artifact_metadata`，没有因拆封而丢弃。

## 4. 向量文件：格式已恢复，内容仍未生成

`chunks/embeddings.parquet`是带字段定义和文件元数据的**零行Parquet空表**，不是JSON改名，不包含随机、零值或哈希伪向量。元数据明确记录：

```text
status = not_generated
vector_count = 0
expected_chunk_count = 194
model = null
dimension = null
```

字段包含chunk_id、corpus_id、tenant_id、permission_level、section、model、dimension、embedding和status；embedding采用列表类型。预期chunk ID清单及未生成原因同时登记在Parquet元数据和provenance中。

`index/signature.yaml`保留上一版结构化特征，其`vector`仍为null。**当前包不能用于真实向量相似度检索**；需要实际计算向量后另行形成新版本。本次已解析校验Parquet文件头、尾部Thrift元数据和schema树；当前环境无PyArrow，因此未把通用Parquet引擎读入测试宣称为已执行。

## 5. 业务内容与使用边界

保留339个节点、74条规则（62计算、12判定）、1666条关系、48条论断、12个证据入口、630处绑定和77条推演说明。339个节点中也包含编号、期间、格式与交叉引用，不全是独立业务指标。

原始800kW/650kW配电分歧、11类资料缺口、10个未定义节点均保留。缺失值不改为0，未知融资条件不补公式。原文及附录摘录是冻结证据，做新项目改值时不可回写为新的事实。

计算采用`decimal_value`的完整精度；每处显示按`node_slots.format`处理单位换算、取整与小数位。共享表述允许一处mention同时支持多个节点，但必须有`also_supports_node_ids`声明。例如“各自仅有141.77万元余量”同时涉及N153和N154；两者后续不相等时须拆句复核，不可只替换一个数字。

原文段落、摘录、表格及源文件按原始内容保留；本文中的格式说明不构成新增业务依据。自由文本自动重写、并发传播事务、完整日期重排、权限脱敏与数据库服务不属于此次已实现功能。

## 6. 检查与复运行

本次恢复后重新执行：所有原生文件解析及哈希核对、原DOCX字节与OOXML完整性、74条基线规则回放、194个切块和630处绑定还原、20个摘录区间、18个CSV矩阵、节点索引与模板文件引用，以及合并图一致性。

重新检查使用：

```bash
python -m pip install PyYAML thrift
python validate_package.py
```

脚本也可接受解压目录参数；不修改数据、不调用大模型、不连接数据库。若安装了PyArrow，会额外使用其读取Parquet空表；否则执行Thrift尾部/schema校验并明确说明引擎校验未执行。

`quality/gate_report.yaml`保留原抽取闸门结论`pass_with_notes`，本次格式校验结果位于`format_conversion_validation`。该结论不代表语义召回率100%、项目批准、报价有效性或融资落实。

`SHA256SUMS`涵盖其余116个文件（包括manifest、说明和脚本），不计算自身，避免递归。
