import { isValidElement, useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, ArrowRight, BookOpen, ChevronRight, CircleAlert, ExternalLink, FileCode2, FileText, GitBranch, Layers3, ListFilter, Search, ShieldCheck, X } from 'lucide-react'
import GraphPanel from './GraphPanel'
import { api, corpusPath, rawUrl, type Category, type CategoryPage, type CorpusArtifact, type CorpusSummary, type GraphEntity, type Project } from './api'
import './corpus-view.css'

type Rec = Record<string, any>
type Detail = { title: string; record: unknown; category?: number; source?: string; artifactId?: string }
type Purpose = { kind: 'category'; category: Category } | { kind: 'artifact'; artifact: CorpusArtifact; category?: Category; loading?: boolean; error?: string }
const valueLabels: Record<string, string> = { populated: '已生成', not_independently_human_reviewed: '未独立人工核对', ready_for_review: '待复核', schema_only_no_embeddings: '向量未生成', structured_features_only: '仅结构特征', not_generated: '未生成', open: '未解决', pass: '通过', pass_with_notes: '附保留事项通过', UNDEFINED: '未定义', UNEVALUABLE: '不可评估', computed: '已计算', provided: '已提供', decided: '已判定', reported: '原文报告', not_assessed: '未评估' }
const asText = (value: unknown): string => value == null ? '—' : typeof value === 'string' ? valueLabels[value] || value : typeof value === 'boolean' ? value ? '是' : '否' : String(value)
const pick = (value: Rec, ...keys: string[]) => keys.map((key) => value[key]).find((item) => item !== undefined && item !== null)
const graphCategories = new Set([13, 14, 15, 17, 18, 20, 23, 24, 28])
const focusByCategory: Record<number, string> = { 13: 'R006', 14: 'N034', 15: 'L010', 17: 'N034', 18: 'EV-06A', 20: 'N035', 23: 'X001', 24: 'G001', 28: 'N034' }

function Status({ value }: { value: unknown }) {
  const text = asText(value)
  const tone = /UNDEFINED|UNEVALUABLE|not_generated|未生成|未解决|未定义|不可评估|未独立|待|缺/i.test(text) ? 'warn' : /conflict|BLOCK|失败|不通过/i.test(text) ? 'danger' : /通过|已生成|已计算|已提供|validated|完成/i.test(text) ? 'success' : 'neutral'
  return <span className={`status status-${tone}`}>{text}</span>
}

function Field({ label, value }: { label: string; value: unknown }) {
  return <div className="field"><span>{label}</span><strong>{asText(value)}</strong></div>
}

function TextBlock({ text }: { text: string }) {
  return <div className="prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>
}

function tableCell(value: unknown): ReactNode {
  if (isValidElement(value)) return value
  if (value == null) return '—'
  if (Array.isArray(value)) return value.map(asText).join('、')
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${typeof item === 'object' ? Array.isArray(item) ? `${item.length} 项` : '详情可展开' : asText(item)}`).join(' · ')
  return asText(value)
}

function Table({ headers, rows, onRow }: { headers: string[]; rows: unknown[][]; onRow?: (index: number) => void }) {
  return <div className="table-wrap"><table><thead><tr>{headers.map((head, index) => <th key={`${head}-${index}`}>{head}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index} onClick={() => onRow?.(index)} className={onRow ? 'clickable' : ''}>{row.map((cell, col) => <td key={col}>{tableCell(cell)}</td>)}</tr>)}</tbody></table>{rows.length === 0 && <div className="empty">没有匹配记录</div>}</div>
}

function ArtifactList({ title, artifacts, projectId, categories, onPurpose, showCategory = false }: {
  title: string; artifacts: CorpusArtifact[]; projectId: string; categories: Category[];
  onPurpose: (artifact: CorpusArtifact) => void; showCategory?: boolean
}) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 20
  const matching = useMemo(() => artifacts.filter((artifact) => {
    const category = categories.find((item) => item.number === artifact.category_number)
    return `${artifact.path || artifact.name} ${artifact.purpose || ''} ${category?.name || ''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
  }), [artifacts, categories, search])
  const totalPages = Math.max(1, Math.ceil(matching.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const shown = matching.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  return <section className="artifact-section">
    <div className="artifact-heading"><div><h2>{title}</h2><small>{artifacts.length} 个文件 · 数据库记录</small></div><div className="search-input"><Search size={16} /><input aria-label={`搜索${title}`} value={search} onChange={(event) => { setSearch(event.target.value); setPage(1) }} placeholder="搜索文件或作用" /></div></div>
    <div className="table-wrap artifact-table"><table><thead><tr><th>文件</th>{showCategory && <th>类别</th>}<th>记录</th><th>操作</th></tr></thead><tbody>{shown.map((artifact) => {
      const category = categories.find((item) => item.number === artifact.category_number)
      return <tr key={artifact.artifact_id}><td><strong>{artifact.name}</strong><small className="artifact-path">{artifact.path || artifact.name}</small></td>{showCategory && <td>{category ? `${String(category.number).padStart(2, '0')} · ${category.name}` : '交付辅助资料'}</td>}<td>{artifact.record_count == null ? '—' : artifact.record_count}</td><td><div className="artifact-actions"><button className="subtle-button" onClick={() => onPurpose(artifact)}>作用说明</button><a className="text-button" href={rawUrl(projectId, artifact.artifact_id)} target="_blank" rel="noreferrer">原件 <ExternalLink size={14} /></a></div></td></tr>
    })}</tbody></table>{shown.length === 0 && <div className="empty">没有匹配文件</div>}</div>
    {totalPages > 1 && <div className="pagination"><span>共 {matching.length} 个 · 第 {currentPage}/{totalPages} 页</span><button disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button><button disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}>下一页</button></div>}
  </section>
}

function PurposeDrawer({ projectId, purpose, onClose, openCategory }: { projectId: string; purpose: Purpose; onClose: () => void; openCategory: (number: number) => void }) {
  const category = purpose.category
  const artifact = purpose.kind === 'artifact' ? purpose.artifact : null
  return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer purpose-drawer" role="dialog" aria-modal="true" aria-label="作用说明" onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><div><small>{artifact ? '入库文件 · 作用说明' : '产物类别 · 作用说明'}</small><h2>{artifact?.name || category?.name}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭作用说明"><X size={19} /></button></div>
    <div className="drawer-content">
      {purpose.kind === 'artifact' && purpose.loading ? <div className="empty">正在读取数据库说明…</div> : <>
        {purpose.kind === 'artifact' && purpose.error && <div className="notice error">{purpose.error}</div>}
        <div className="purpose-block"><h3>作用</h3><p>{artifact ? artifact.purpose || '暂无说明' : category?.description || '暂无说明'}</p></div>
        {(artifact?.writing_use || (!artifact && category?.actual_content)) && <div className="purpose-block"><h3>{artifact ? '写作使用' : '实际内容'}</h3><p>{artifact?.writing_use || category?.actual_content}</p></div>}
        {(artifact?.boundary || (!artifact && category?.boundary)) && <div className="purpose-block"><h3>{artifact ? '原说明表状态与边界' : '使用边界'}</h3><p>{artifact?.boundary || category?.boundary}</p></div>}
        {artifact && <div className="purpose-meta"><Field label="入库路径" value={artifact.path} /><Field label="记录数" value={artifact.record_count ?? '—'} /></div>}
        <div className="purpose-actions">
          {category && <button className="subtle-button" onClick={() => { openCategory(category.number); onClose() }}>查看内容预览 <ArrowRight size={15} /></button>}
          {artifact && <a className="text-button" href={rawUrl(projectId, artifact.artifact_id)} target="_blank" rel="noreferrer">打开原件 <ExternalLink size={15} /></a>}
        </div>
      </>}
    </div>
  </aside></div>
}

function RecordCards({ records, title, subtitle, onDetail }: { records: Rec[]; title: (r: Rec) => string; subtitle?: (r: Rec) => string; onDetail: (r: Rec) => void }) {
  return <div className="card-list">{records.map((record, index) => <button key={String(record.id || record.node || record.file || index)} className="record-card" onClick={() => onDetail(record)}><span className="record-icon"><FileCode2 size={17} /></span><span className="record-body"><strong>{title(record)}</strong>{subtitle && <small>{subtitle(record)}</small>}</span><ChevronRight size={17} /></button>)}</div>
}

function HierarchyPreview({ records, templates, onDetail }: { records: Rec[]; templates?: boolean; onDetail: (record: Rec) => void }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const ordered = useMemo(() => templates ? [...records].sort((a, b) => String(a.section_id).localeCompare(String(b.section_id), 'zh-CN', { numeric: true })) : records, [records, templates])
  const visible: { record: Rec; id: string; level: number; hasChildren: boolean }[] = []
  const ancestors: { id: string; level: number }[] = []
  ordered.forEach((record, index) => {
    const id = String(record.section_id || record.id)
    const level = Math.max(1, Number(record.level || 1))
    while (ancestors.length && ancestors[ancestors.length - 1].level >= level) ancestors.pop()
    if (!ancestors.some((item) => collapsed.has(item.id))) visible.push({ record, id, level, hasChildren: Math.max(1, Number(ordered[index + 1]?.level || 1)) > level })
    ancestors.push({ id, level })
  })
  return <div className="outline-tree">{visible.map(({ record, id, level, hasChildren }) => <div className="hierarchy-row" key={id} style={{ paddingLeft: 10 + (level - 1) * 20 }}>
    {hasChildren ? <button className="hierarchy-toggle" aria-label={`${collapsed.has(id) ? '展开' : '收起'} ${record.title}`} onClick={() => setCollapsed((old) => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next })}><ChevronRight size={15} className={collapsed.has(id) ? '' : 'expanded'} /></button> : <span className="hierarchy-spacer" />}
    <button className="hierarchy-record" onClick={() => onDetail(record)}><span className="mono">{id}</span><strong>{record.title}</strong><small>{templates ? `${record.paragraph_order?.length || 0} 段 · ${record.tables?.length || 0} 表` : `${record.pages?.length || 0} 页 · ${record.chunks?.length || 0} 块`}</small><ChevronRight size={15} /></button>
  </div>)}</div>
}

function ObjectOverview({ record, preferred }: { record: Rec; preferred?: string[] }) {
  const keys = preferred || Object.keys(record).filter((key) => !['source_locations', 'artifact_metadata', 'files', 'excerpts', 'sections', 'nodes', 'rules', 'relations', 'claims', 'invariants'].includes(key))
  return <div className="field-grid">{keys.filter((key) => record[key] !== undefined).map((key) => <Field key={key} label={key} value={typeof record[key] === 'object' ? Array.isArray(record[key]) ? `${record[key].length} 项` : '查看详情' : record[key]} />)}</div>
}

function DocxPreview({ record }: { record: Rec }) {
  const [mode, setMode] = useState<'text' | 'tables'>('text')
  const paragraphs = record.paragraphs || []
  const tables = record.tables || []
  return <><div className="notice"><BookOpen size={17} /> {record.preview_kind}</div><div className="segmented margin-bottom"><button className={mode === 'text' ? 'active' : ''} onClick={() => setMode('text')}>正文 · {paragraphs.length}</button><button className={mode === 'tables' ? 'active' : ''} onClick={() => setMode('tables')}>表格 · {tables.length}</button></div>{mode === 'text' ? <div className="document-sheet">{paragraphs.map((text: string, index: number) => <p key={index}><span className="paragraph-number">{String(index + 1).padStart(3, '0')}</span>{text}</p>)}</div> : <div className="stack">{tables.map((table: Rec, index: number) => <div className="surface" key={index}><div className="surface-heading"><strong>{table.name}</strong><span>{table.rows.length} 行</span></div><Table headers={table.rows[0] || []} rows={table.rows.slice(1)} /></div>)}</div>}</>
}

function OffsetPreview({ projectId, normalizedAssetId, records, onDetail }: { projectId: string; normalizedAssetId: string; records: Rec[]; onDetail: (r: Rec) => void }) {
  const [text, setText] = useState('')
  const [selected, setSelected] = useState<Rec | null>(null)
  useEffect(() => { fetch(rawUrl(projectId, normalizedAssetId)).then((r) => r.text()).then(setText).catch(() => setText('')) }, [projectId, normalizedAssetId])
  return <div className="split-layout"><div><Table headers={['块 ID', '类型', '页码', '字符区间']} rows={records.map((r) => [r.chunk_id || r.block_id, r.type, r.page, (r.normalized_span || []).join('–')])} onRow={(index) => setSelected(records[index])} /></div><div className="surface sticky-panel"><h3>定位对照</h3>{selected ? <><Field label="Word 位置" value={selected.source_locator?.xpath} /><Field label="页码" value={selected.page} /><Field label="原文片段" value={text.slice(selected.normalized_span?.[0] || 0, selected.normalized_span?.[1] || 0)} /><button className="text-button" onClick={() => onDetail(selected)}>查看完整对照记录</button></> : <div className="empty">选择左侧记录查看定位</div>}</div></div>
}

function SkeletonPreview({ projectId, records, onDetail }: { projectId: string; records: Rec[]; onDetail: (r: Rec) => void }) {
  const [raw, setRaw] = useState<Record<string, string>>({})
  const loadOriginal = (id: string) => {
    api<CategoryPage>(corpusPath(projectId, `/categories/9?search=${encodeURIComponent(id)}&size=20`)).then((data) => {
      const match = data.records.find((item) => item.id === id)
      setRaw((previous) => ({ ...previous, [id]: String(match?.text || '未找到原文') }))
    }).catch(() => setRaw((previous) => ({ ...previous, [id]: '原文读取失败' })))
  }
  return <div className="stack">{records.map((record) => <div className="surface chunk-compare" key={record.id}><div className="surface-heading"><div><strong>{record.id}</strong><span>{record.section} · {record.type}</span></div><div><Status value={record.reusable ? '可复用候选' : '不可直接复用'} /><button className="subtle-button" onClick={() => onDetail(record)}>槽位 {record.node_slots?.length || 0}</button></div></div><div className="comparison-grid"><div><small>占位骨架</small><p>{String(record.skeleton_md).split(/(\{\{node:[^}]+\}\})/g).map((part, index) => part.startsWith('{{node:') ? <mark key={index}>{part}</mark> : part)}</p></div><div><small>原文对照</small>{raw[record.id] ? <p>{raw[record.id]}</p> : <button className="text-button" onClick={() => loadOriginal(record.id)}>读取对应原文 <ArrowRight size={14} /></button>}</div></div></div>)}</div>
}

function NormalizedPreview({ text, query, locationSpan }: { text: string; query: string; locationSpan?: [number, number] | null }) {
  const [section, setSection] = useState(-1)
  const headings = useMemo(() => [...text.matchAll(/^(#{1,4})\s+(.+)$/gm)].map((match) => ({ title: match[2], level: match[1].length, start: match.index || 0 })), [text])
  const chosen = headings[section]
  const end = chosen ? (headings.slice(section + 1).find((item) => item.level <= chosen.level)?.start ?? text.length) : text.length
  const visibleText = chosen ? text.slice(chosen.start, end) : text
  const matches = useMemo(() => {
    if (!query.trim()) return [] as number[]
    const found: number[] = [], lower = text.toLocaleLowerCase(), needle = query.trim().toLocaleLowerCase()
    for (let pos = lower.indexOf(needle); pos >= 0 && found.length < 20; pos = lower.indexOf(needle, pos + needle.length)) found.push(pos)
    return found
  }, [text, query])
  return <>
    {locationSpan && <div className="surface source-focus"><strong>原文定位 · 字符 {locationSpan[0]}–{locationSpan[1]}</strong><p>{text.slice(Math.max(0, locationSpan[0] - 90), locationSpan[0])}<mark>{text.slice(locationSpan[0], locationSpan[1])}</mark>{text.slice(locationSpan[1], locationSpan[1] + 90)}</p></div>}
    {query.trim() && <div className="source-results"><strong>正文搜索 · {matches.length}{matches.length === 20 ? '+' : ''} 处匹配</strong>{matches.map((pos) => <div key={pos}><small>字符 {pos}</small><span>{text.slice(Math.max(0, pos - 35), pos)}<mark>{text.slice(pos, pos + query.trim().length)}</mark>{text.slice(pos + query.trim().length, pos + query.trim().length + 55)}</span></div>)}{matches.length === 0 && <p>没有找到匹配内容</p>}</div>}
    <div className="source-layout"><nav aria-label="正文章节导航" className="source-outline"><strong>章节定位</strong><button className={section === -1 ? 'active' : ''} onClick={() => setSection(-1)}>全文</button>{headings.map((item, index) => <button key={`${item.start}-${index}`} className={section === index ? 'active' : ''} style={{ paddingLeft: 11 + (item.level - 1) * 10 }} onClick={() => setSection(index)}>{item.title}</button>)}</nav><div className="source-body"><TextBlock text={visibleText} /></div></div>
  </>
}

function CategoryContent({ projectId, number, page, catalog, onDetail, onGraph, locationSpan, query }: { projectId: string; number: number; page: CategoryPage; catalog: Category[]; onDetail: (record: unknown, title?: string) => void; onGraph: (id: string) => void; locationSpan?: [number, number] | null; query: string }) {
  const r = page.records as Rec[]
  const first = r[0] || {}
  const assetId = (path: string) => page.category.artifacts?.find((asset) => asset.path === path)?.artifact_id || path
  if (number === 1) return <><ObjectOverview record={page.context || {}} preferred={['corpus_id', 'version', 'status', 'review_status', 'source_fingerprint', 'gate_verdict', 'embedding_status']} /><h3 className="section-subtitle">内容目录 · {page.total} 类</h3><Table headers={['编号', '内容', '分组', '记录', '状态']} rows={r.map((x) => { const category = catalog.find((item) => item.number === x.no); return [x.no, category?.name || x.path, category?.group, category?.record_count ?? '—', <Status value={x.content_status} />] })} onRow={(i) => onDetail(r[i], `产物 ${r[i].no}`)} /></>
  if (number === 2) return <><div className="field-grid"><Field label="项目名称" value={first.title} /><Field label="报告类型" value={first.document_type} /><Field label="行业" value={first.industry} /><Field label="项目类型" value={first.project_type} /><Field label="项目编号" value={first.project_number} /><Field label="建设单位" value={first.business_entity} /><Field label="地点" value={first.location} /><Field label="基准日期" value={first.as_of} /><Field label="版本" value={first.version} /><Field label="编制部门" value={(first.compiled_by || []).join('、')} /></div><h3 className="section-subtitle">规模关联</h3><div className="chip-row">{Object.entries(first.scale_refs || {}).map(([name, id]) => <span className="chip" key={name}>{name} · {String(id)}</span>)}</div><button className="text-button" onClick={() => onDetail(first, '完整资料卡')}>查看来源与其他字段</button></>
  if (number === 3) return <><div className="field-grid"><Field label="原始文件" value={first.source_filename} /><Field label="抽取开始" value={first.extraction_started_at} /><Field label="抽取完成" value={first.extraction_finished_at} /><Field label="抽取器" value={first.extractor} /><Field label="人工核对" value={first.human_review?.performed ? '已进行' : '尚未进行'} /><Field label="格式转换" value={first.native_format_export?.performed_at} /></div><h3 className="section-subtitle">处理记录</h3><div className="timeline">{(first.events || []).map((event: string, i: number) => <div key={i}><i />{event}</div>)}</div><button className="subtle-button" onClick={() => onDetail(first, '完整溯源记录')}>查看输入与排除输入</button></>
  if (number === 4) return <HierarchyPreview records={r} onDetail={(section) => onDetail(section, section.title)} />
  if (number === 5) return <DocxPreview record={first} />
  if (number === 6) return <NormalizedPreview text={first.text || ''} query={query} locationSpan={locationSpan} />
  if (number === 7) return <OffsetPreview projectId={projectId} normalizedAssetId={catalog.find((item) => item.number === 6)?.artifacts?.[0]?.artifact_id || 'source/normalized.md'} records={r} onDetail={(record) => onDetail(record, '原文定位')} />
  if (number === 8) return <div className="stack">{r.map((asset) => <div className="surface" key={asset.file}><div className="surface-heading"><strong>{asset.name}</strong><span>{asset.row_count} 行 · {asset.column_count} 列</span></div><Table headers={asset.rows?.[0] || []} rows={(asset.rows || []).slice(1)} /><a className="text-button" href={rawUrl(projectId, assetId(asset.file))} target="_blank" rel="noreferrer">打开原始 CSV <ExternalLink size={14} /></a></div>)}</div>
  if (number === 9) return <div className="stack">{r.map((chunk) => <button className="surface chunk-item" key={chunk.id} onClick={() => onDetail(chunk, chunk.id)}><div className="surface-heading"><strong>{chunk.id}</strong><span>{chunk.section} · {chunk.type} · 第 {chunk.page} 页</span></div><p>{chunk.text}</p><small>区间 {chunk.span?.join('–')} · {chunk.nodes?.length || 0} 个节点</small></button>)}</div>
  if (number === 10) return <SkeletonPreview projectId={projectId} records={r} onDetail={(record) => onDetail(record, record.id)} />
  if (number === 11) return <><div className="notice warn"><CircleAlert size={17} /> 零行 · 向量未生成 · 检索不可用</div><div className="field-grid"><Field label="记录数" value={first.rows} /><Field label="状态" value={first.status} /><Field label="预期切块数" value={first.metadata?.expected_chunk_count} /><Field label="模型" value={first.metadata?.model || '未设置'} /></div><h3 className="section-subtitle">字段结构</h3><Table headers={['字段', '类型']} rows={(first.columns || []).map((x: Rec) => [x.name, x.type])} /></>
  if (number === 12) return <Table headers={['ID', '名称', '类型 / 角色', '原文值', '单位', '状态', '关联']} rows={r.map((x) => [<b className="mono">{x.id}</b>, x.label, `${x.kind} · ${x.role}`, asText(x.value), x.unit, <Status value={x.status} />, <button className="text-button" onClick={(e) => { e.stopPropagation(); onGraph(x.id) }}>查看图谱</button>])} onRow={(i) => onDetail(r[i], r[i].label)} />
  if (number === 13) return <div className="stack">{r.map((rule) => <div className="surface" key={rule.id}><div className="surface-heading"><div><strong>{rule.id} · {rule.target}</strong><span>{rule.name || rule.kind}</span></div><button className="subtle-button" onClick={() => onGraph(rule.id)}><GitBranch size={15} /> 依赖图</button></div><div className="expression">{rule.expr}</div><div className="chip-row">{(rule.deps || []).map((dep: string) => <span className="chip" key={dep}>{dep}</span>)}<Status value={rule.kind} /></div><button className="text-button" onClick={() => onDetail(rule, rule.id)}>查看缺值守卫和来源</button></div>)}</div>
  if (number === 14) return <Table headers={['关系 ID', '起点', '关系类型', '终点', '传播']} rows={r.map((x) => [x.id, x.from_id, <span className="chip">{x.type}</span>, x.to_id, x.propagate])} onRow={(i) => onDetail(r[i], r[i].id)} />
  if (number === 15) return <div className="stack">{r.map((x) => <div className="surface" key={x.id}><div className="surface-heading"><div><strong>{x.id} · {x.label}</strong><span>{x.strength}</span></div><button className="subtle-button" onClick={() => onGraph(x.id)}><GitBranch size={15} /> 支撑关系</button></div><p>{x.pattern}</p><div className="chip-row"><small>事实</small>{(x.supports || []).map((v: string) => <span className="chip" key={v}>{v}</span>)}<small>证据</small>{(x.must_cite || []).map((v: string) => <span className="chip" key={v}>{v}</span>)}</div><button className="text-button" onClick={() => onDetail(x, x.label)}>查看原文与复核要求</button></div>)}</div>
  if (number === 16) return <Table headers={['ID', '校验条件', '适用对象', '执行结果', '违例处理']} rows={r.map((x) => [x.id, x.description, (x.nodes || []).join('、') || '语料整体', <Status value={x.check?.status || '未执行'} />, x.on_violation])} onRow={(i) => onDetail(r[i], r[i].id)} />
  if (number === 17) return <div className="metric-grid">{r.map((x) => <div className="metric" key={x.collection}><span>{x.collection}</span><strong>{x.count}</strong></div>)}</div>
  if (number === 18) return <div className="stack">{r.map((x) => <div className="surface" key={x.id}><div className="surface-heading"><div><strong>{x.id} · {x.title}</strong><span>{x.version} · {x.formed_at}</span></div><Status value={x.original_document_available ? '完整原件可用' : '仅有摘录'} /></div><p>{x.credibility}</p><div className="chip-row"><span className="chip">{x.source_type}</span><span className="chip">{x.excerpts?.length || 0} 段摘录</span>{(x.limitations || []).slice(0, 2).map((limit: string) => <span className="chip" key={limit}>{limit}</span>)}</div><div className="inline-actions"><button className="subtle-button" onClick={() => onGraph(x.id)}>关联图谱</button><button className="text-button" onClick={() => onDetail(x, x.title)}>查看限制与摘录</button></div></div>)}</div>
  if (number === 19) return <div className="stack">{r.map((x, index) => <div className="surface" key={x.file}><div className="surface-heading"><strong>{x.link?.evidence_id || '证据'} · 摘录 {index + 1}</strong><a href={rawUrl(projectId, assetId(x.file))} target="_blank" rel="noreferrer" className="text-button">原始摘录 <ExternalLink size={14} /></a></div><TextBlock text={x.text} />{x.link && <div className="chip-row"><span className="chip">第 {x.link.page} 页</span><span className="chip">字符 {x.link.span?.join('–')}</span><button className="text-button" onClick={() => onDetail(x, `${x.link.evidence_id} · 摘录 ${index + 1}`)}>查看来源位置</button></div>}</div>)}</div>
  if (number === 20) return <div className="stack">{r.map((x) => <div className="surface" key={x.node}><div className="surface-heading"><div><strong>{x.node} · {x.label}</strong><span>链长 {x.chain?.length || 0} · 深度 {x.depth}</span></div><button className="subtle-button" onClick={() => onGraph(x.node)}><GitBranch size={15} /> 推演图</button></div><p>{x.narrative_zh}</p><div className="chip-row"><Status value={x.evaluable ? '可评估' : '不可评估'} /><span className="chip">原稿 {asText(x.reported_value)}</span><span className="chip">回放 {asText(x.replayed_value_decimal)}</span></div><button className="text-button" onClick={() => onDetail(x, x.label)}>查看逐步推演</button></div>)}</div>
  if (number === 21) return <div className="stack">{r.map((x, i) => <button className="surface decision-card" key={x.id || i} onClick={() => onDetail(x, x.label || x.id)}><span className="record-icon"><ShieldCheck size={18} /></span><div><strong>{x.id || `判定 ${i + 1}`} · {x.node_id} · {x.chosen ? '已选择' : '未选择'}</strong><p>{x.reason}</p><small>依据 {(x.basis || []).join('、')} · 重新讨论条件 {(x.revisit_if || []).join('、') || '未记录'}</small></div><ChevronRight size={16} /></button>)}</div>
  if (number === 22) return <div className="timeline trace-list">{r.map((x) => <button key={x.event_id} onClick={() => onDetail(x, x.event_id)}><i /><strong>{x.event_id}</strong><span>{x.type}</span><span>{x.rule_id} → {x.node_id}</span><Status value={x.status} /></button>)}</div>
  if (number === 23) return <div className="stack">{r.map((x) => <div className="surface conflict-card" key={x.id}><div className="surface-heading"><strong>{x.id} · {pick(x, 'what', 'label', 'topic')}</strong><Status value={x.status} /></div><div className="conflict-sources">{(x.linked_nodes || []).map((node: Rec, i: number) => <div key={node.id}><small>来源 {i + 1} · {x.evidence?.[i] || '来源待核'}</small><strong>{node.value} {node.unit}</strong><span>{node.label}</span></div>)}</div><p>状态：未解决 · 责任方：{x.owner}。{x.resolution_required}</p><div className="inline-actions"><button className="subtle-button" onClick={() => onGraph(x.id)}>查看冲突图</button><button className="text-button" onClick={() => onDetail(x, x.id)}>查看来源与处理要求</button></div></div>)}</div>
  if (number === 24) return <Table headers={['ID', '缺口', '责任方', '状态', '阻断范围', '关联']} rows={r.map((x) => [x.id, x.what, x.owner, <Status value={x.status} />, x.blocking_scope, <button className="text-button" onClick={(e) => { e.stopPropagation(); onGraph(x.id) }}>关联图谱</button>])} onRow={(i) => onDetail(r[i], r[i].what)} />
  if (number === 25) return <><div className="field-grid"><Field label="闸门结论" value={first.verdict} /><Field label="检查范围" value={first.scope} /><Field label="项目审批" value={first.project_approval_verdict} /><Field label="无条件实施" value={first.unconditional_implementation_allowed} /><Field label="冲突数" value={first.conflicts?.length} /><Field label="缺口数" value={first.gaps?.length} /><Field label="通过项" value={(first.tests || []).filter((test: Rec) => test.status === 'pass').length} /><Field label="向量状态" value={first.embedding_status} /></div><div className="notice warn">历史闸门 · 不构成项目批准</div><h3 className="section-subtitle">校验结果</h3><Table headers={['校验项', '结果', '说明']} rows={(first.tests || []).map((x: Rec) => [x.invariant_id, <Status value={x.status} />, x.detail])} /><div className="chip-row"><strong>保留事项</strong>{(first.conflicts || []).map((id: string) => <span className="chip" key={id}>{id}</span>)}{(first.gaps || []).map((id: string) => <span className="chip" key={id}>{id}</span>)}</div><button className="text-button" onClick={() => onDetail(first, '完整闸门报告')}>查看保留事项与回放详情</button></>
  if (number === 26) return <><div className="field-grid"><Field label="人称" value={first.person} /><Field label="语气" value={first.tone} /><Field label="平均句长" value={first.avg_sentence_len} /><Field label="句子数" value={first.sentence_count} /><Field label="金额单位" value={first.number_format?.default_amount_unit} /><Field label="金额小数位" value={first.number_format?.display_amount_decimals} /><Field label="产能取整" value={first.number_format?.capacity_rounding} /><Field label="表题格式" value={first.table_caption} /></div><h3 className="section-subtitle">术语与数字格式</h3><RecordCards records={first.terminology || []} title={(x) => x.canonical} subtitle={(x) => (x.observed_aliases || []).join('、')} onDetail={(x) => onDetail(x, x.canonical)} /><button className="text-button" onClick={() => onDetail(first, '完整风格配置')}>查看全部风格字段</button></>
  if (number === 27) return <HierarchyPreview records={r} templates onDetail={(template) => onDetail(template, template.title)} />
  if (number === 28) return <Table headers={['节点', '名称', '角色', '出现次数', '章节', '关联']} rows={r.map((x) => [x.id, x.label, x.role, x.mentions?.length || 0, (x.sections || []).join('、'), <button className="text-button" onClick={(e) => { e.stopPropagation(); onGraph(x.id) }}>关系图</button>])} onRow={(i) => onDetail(r[i], r[i].label)} />
  if (number === 29) return <><div className="field-grid"><Field label="精确标签数" value={Object.keys((page.context as Rec)?.exact_label_index || {}).length} /><Field label="同数值视为同一事实" value={(page.context as Rec)?.matching_policy?.numeric_equality_is_not_identity ? '否' : '规则未说明'} /><Field label="匹配需期间与范围" value={(page.context as Rec)?.matching_policy?.period_and_scope_required ? '是' : '规则未说明'} /><Field label="未观察别名自动启用" value={(page.context as Rec)?.matching_policy?.unobserved_aliases_not_auto_enabled ? '否' : '规则未说明'} /></div><Table headers={['规范术语', '已观察别名', '节点', '消歧说明']} rows={r.map((x) => [x.canonical, (x.observed_aliases || []).join('、'), (x.node_ids || []).join('、'), x.disambiguation])} onRow={(i) => onDetail(r[i], r[i].canonical)} /></>
  if (number === 30) return <><div className="notice warn">向量与匹配权重未生成</div><div className="field-grid"><Field label="行业" value={first.industry} /><Field label="项目类型" value={first.project_type} /><Field label="产品" value={first.product} /><Field label="来源日期" value={first.source_date} /><Field label="匹配状态" value={first.status} /><Field label="向量" value={first.vector == null ? '未生成' : '已有'} /></div><h3 className="section-subtitle">工艺关键词</h3><div className="chip-row">{(first.process_keywords || []).map((word: string) => <span className="chip" key={word}>{word}</span>)}</div><button className="text-button" onClick={() => onDetail(first, '全部语料特征')}>查看完整特征</button></>
  return <div className="empty">此类产物暂无预览</div>
}

function RecordObject({ value }: { value: Rec }) {
  const labels: Record<string, string> = {
    id: 'ID', key: '字段键', title: '标题', label: '名称', name: '名称', type: '类型', kind: '类型', role: '角色', status: '状态',
    value: '值', unit: '单位', source: '来源', page: '页码', section: '章节', section_id: '章节 ID', chunk_id: '切块 ID',
    rule_id: '规则 ID', rule: '计算规则', node_id: '节点 ID', target: '目标', expression: '表达式', expr: '表达式', scope: '适用范围',
    description: '内容', text: '原文', pattern: '论断', narrative_zh: '推演说明', reason: '依据',
    version: '版本', formed_at: '形成日期', as_of: '时点', owner: '责任方', blocking_scope: '阻断范围',
    review_status: '核对状态', content_status: '内容状态', source_type: '来源类型',
    original_document_available: '完整原件', reusable: '可复用', evaluable: '可评估',
    reported_value: '原稿值', replayed_value_decimal: '回放值', status_reason: '状态原因',
  }
  const fields = Object.entries(value).filter(([key, item]) => !key.startsWith('_') && !Array.isArray(item) && (item === null || typeof item !== 'object'))
  const renderFields = (entries: [string, unknown][]) => entries.map(([key, item]) => <div key={key}><small>{labels[key] || key.replaceAll('_', ' ')}</small><span>{asText(item)}</span></div>)
  return <><div className="detail-object">{renderFields(fields.slice(0, 10))}</div>{fields.length > 10 && <details className="record-more"><summary>更多字段 · {fields.length - 10}</summary><div className="detail-object">{renderFields(fields.slice(10))}</div></details>}</>
}

function TemplateDetail({ template, openCategory, openGraph }: { template: Rec; openCategory: (n: number, query?: string) => void; openGraph: (id: string) => void }) {
  const order = (template.paragraph_order || []) as Rec[]
  const tables = (template.tables || []) as Rec[]
  const patterns = (template.argument_patterns || []) as Rec[]
  const chunkTypes: Record<string, string> = { heading: '标题', paragraph: '正文', caption: '表题', table: '表格', list: '列表' }
  const strength: Record<string, string> = { conflicted: '存在冲突', boundary: '边界判断', supported: '有支撑', conditional: '有条件' }
  return <div className="template-detail">
    <div className="field-grid"><Field label="章节" value={`${template.section_id} · ${template.title}`} /><Field label="层级" value={template.level} /><Field label="结构复用" value={template.reusable ? '可作为结构参考' : '只读参考'} /></div>
    {template.note && <p className="template-note">{template.note}</p>}
    <h3>段落顺序 <span>{order.length} 段</span></h3>
    <div className="template-order">{order.length ? order.map((item, index) => <div key={`${item.chunk_id}-${index}`}><span>{index + 1}</span><strong>{item.chunk_id}</strong><small>{chunkTypes[item.type] || item.type}</small></div>) : <p>未记录段落顺序</p>}</div>
    <h3>表格结构 <span>{tables.length} 张</span></h3>
    {tables.length ? <div className="stack">{tables.map((table, index) => <div className="template-linked" key={table.table_id || index}><strong>{table.table_id} · {table.caption}</strong><small>{(table.columns || []).join(' · ')}</small><button className="text-button" onClick={() => openCategory(8, String(table.table_id || table.chunk_id))}>查看表格 <ArrowRight size={14} /></button></div>)}</div> : <p className="template-empty">本节无表格</p>}
    <h3>论证模式 <span>{patterns.length} 条</span></h3>
    {patterns.length ? <div className="stack">{patterns.map((item, index) => <div className="template-linked" key={item.claim_id || index}><strong>{item.claim_id} · {item.pattern}</strong><small>{strength[item.strength] || item.strength || '状态未记录'} · 支撑事实：{(item.requires_support || []).join('、') || '未记录'}</small><div className="inline-actions"><button className="text-button" onClick={() => openCategory(15, String(item.claim_id))}>查看论断 <ArrowRight size={14} /></button><button className="text-button" onClick={() => openGraph(String(item.claim_id))}>关系图 <GitBranch size={14} /></button></div></div>)}</div> : <p className="template-empty">未记录论证模式</p>}
    <h3>骨架引用 <span>{order.filter((item) => item.skeleton_ref).length} 处</span></h3>
    <div className="template-references">{order.filter((item) => item.skeleton_ref).map((item, index) => <button key={`${item.chunk_id}-${index}`} className="subtle-button" onClick={() => openCategory(10, String(item.chunk_id))}>{item.chunk_id} · 查看骨架 <ArrowRight size={14} /></button>)}{!order.some((item) => item.skeleton_ref) && <p className="template-empty">没有骨架引用</p>}</div>
    {(template.must_cover || []).length > 0 && <><h3>必须覆盖</h3><div className="chip-row">{template.must_cover.map((item: string) => <span className="chip" key={item}>{item}</span>)}</div></>}
  </div>
}

function DetailDrawer({ projectId, detail, wordAssetId, onClose, openCategory, openGraph, openLocation }: { projectId: string; detail: Detail; wordAssetId: string; onClose: () => void; openCategory: (n: number, query?: string) => void; openGraph: (id: string) => void; openLocation: (span: [number, number]) => void }) {
  const [expanded, setExpanded] = useState(false)
  const data = detail.record as Rec
  const groupLabels: Record<string, string> = { source: '来源', mentions: '出现位置', applies_to_periods: '适用期间', deps: '依赖', nodes: '节点', relations: '关系', supports: '支撑事实', must_cite: '必引证据', sections: '章节', chunks: '切块', files: '文件', evidence: '证据', limitations: '限制', excerpts: '摘录', alternatives: '备选项', basis: '依据', revisit_if: '重议条件', chain: '推演步骤' }
  const groupLabel = (key: string) => groupLabels[key] || key.replaceAll('_', ' ')
  const locations = (data?.source_locations || (data?.source_location ? [data.source_location] : data?.link ? [data.link] : data?.source_locator ? [{ ...data, span: data.normalized_span || data.span || data.char_span }] : [])) as Rec[]
  return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer" onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><div><small>产物详情</small><h2>{detail.title}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭详情"><X size={19} /></button></div>
    <div className="drawer-content">
      {detail.source && <div className="notice"><FileText size={16} /> 来源：{detail.source}<a href={rawUrl(projectId, detail.artifactId || detail.source)} target="_blank" rel="noreferrer" className="text-button">核对原件 <ExternalLink size={14} /></a></div>}
      {detail.category && <button className="text-button" onClick={() => { openCategory(detail.category!); onClose() }}>打开所属类别 <ArrowRight size={15} /></button>}
      {detail.category === 27 ? <TemplateDetail template={data} openCategory={(number, search) => { openCategory(number, search); onClose() }} openGraph={(id) => { openGraph(id); onClose() }} /> : data && typeof data === 'object' ? <>
        <RecordObject value={data} />
        {locations.length > 0 && <div className="detail-group"><h3>原文位置 <span>{locations.length} 处</span></h3><div className="location-list">{locations.slice(0, expanded ? 100 : 8).map((loc, i) => <div key={i}><div><strong>第 {loc.page ?? '—'} 页</strong><span>{loc.chunk_id || loc.block_id || loc.id}</span><small>{loc.source_locator?.xpath || loc.source_locator?.part}</small></div>{(loc.span || loc.char_span) && <button className="text-button" onClick={() => { openLocation((loc.span || loc.char_span) as [number, number]); onClose() }}>定位统一正文 <ArrowRight size={14} /></button>}</div>)}</div><a className="text-button" href={rawUrl(projectId, wordAssetId)} target="_blank" rel="noreferrer">打开 Word 原件 <ExternalLink size={14} /></a></div>}
        {Object.entries(data).filter(([key, value]) => Array.isArray(value) && key !== 'source_locations' && !key.startsWith('_')).map(([key, value]) => <details className="detail-group record-group" key={key}><summary>{groupLabel(key)} <span>{(value as unknown[]).length} 项</span></summary><div className="detail-list">{(value as unknown[]).slice(0, expanded ? 300 : 12).map((item, i) => <div key={i}>{item && typeof item === 'object' ? <RecordObject value={item as Rec} /> : <span>{String(item)}</span>}</div>)}</div>{(value as unknown[]).length > 12 && !expanded && <button className="text-button" onClick={() => setExpanded(true)}>展开全部</button>}</details>)}
        {Object.entries(data).filter(([key, value]) => !key.startsWith('_') && value && typeof value === 'object' && !Array.isArray(value)).map(([key, value]) => <details className="detail-group record-group" key={key}><summary>{groupLabel(key)}</summary><RecordObject value={value as Rec} /></details>)}
      </> : <p>{asText(data)}</p>}
      <details className="raw-detail"><summary>查看结构化原始记录</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>
    </div>
  </aside></div>
}

type RecordFilters = { node_group: string; status: string; owner: string; rule_id: string; date_from: string; date_to: string; blocking: string }
const emptyRecordFilters: RecordFilters = { node_group: '', status: '', owner: '', rule_id: '', date_from: '', date_to: '', blocking: '' }
function CategoryFilters({ number, options, values, change }: { number: number; options: Record<string, string[]>; values: RecordFilters; change: (key: keyof RecordFilters, value: string) => void }) {
  if (number === 12) return <div className="category-filters"><select aria-label="节点分组" value={values.node_group} onChange={(event) => change('node_group', event.target.value)}><option value="">全部节点</option><option>事实</option><option>判断</option><option>结构数字</option></select><select aria-label="节点状态" value={values.status} onChange={(event) => change('status', event.target.value)}><option value="">全部状态</option>{(options.statuses || []).map((value) => <option key={value}>{value}</option>)}</select></div>
  if (number === 22) return <div className="category-filters"><input aria-label="轨迹规则 ID" value={values.rule_id} onChange={(event) => change('rule_id', event.target.value)} placeholder="规则 ID，如 R006" /><label>起始日期<input aria-label="轨迹起始日期" type="date" value={values.date_from} onChange={(event) => change('date_from', event.target.value)} /></label><label>结束日期<input aria-label="轨迹结束日期" type="date" value={values.date_to} onChange={(event) => change('date_to', event.target.value)} /></label></div>
  if (number === 24) return <div className="category-filters"><select aria-label="缺口责任方" value={values.owner} onChange={(event) => change('owner', event.target.value)}><option value="">全部责任方</option>{(options.owners || []).map((value) => <option key={value}>{value}</option>)}</select><select aria-label="缺口状态" value={values.status} onChange={(event) => change('status', event.target.value)}><option value="">全部状态</option>{(options.statuses || []).map((value) => <option key={value}>{value}</option>)}</select><input aria-label="筛选阻断范围" value={values.blocking} onChange={(event) => change('blocking', event.target.value)} placeholder="筛选阻断范围" /></div>
  return null
}

export default function CorpusView({ project }: { project: Project }) {
  const [summary, setSummary] = useState<CorpusSummary | null>(null)
  const [categories, setCategories] = useState<Category[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [page, setPage] = useState<CategoryPage | null>(null)
  const [pageNo, setPageNo] = useState(1)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('全部')
  const [recordFilters, setRecordFilters] = useState<RecordFilters>(emptyRecordFilters)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [purpose, setPurpose] = useState<Purpose | null>(null)
  const [locationSpan, setLocationSpan] = useState<[number, number] | null>(null)
  const [graphFocus, setGraphFocus] = useState<string | null>(null)
  const [mode, setMode] = useState<'content' | 'graph'>('content')
  const [error, setError] = useState('')
  const [allFilesOpen, setAllFilesOpen] = useState(false)
  const [categoryFilesOpen, setCategoryFilesOpen] = useState(false)

  useEffect(() => {
    if (!project.has_corpus) return
    Promise.all([api<CorpusSummary>(corpusPath(project.id, '/summary')), api<Category[]>(corpusPath(project.id, '/categories'))]).then(([one, two]) => { setSummary(one); setCategories(two) }).catch((cause: Error) => setError(cause.message))
  }, [project.id, project.has_corpus])
  useEffect(() => {
    if (!project.has_corpus || selected === null) return
    let active = true
    const params = new URLSearchParams({ page: String(pageNo), size: selected === 4 || selected === 27 ? '100' : '30', search: query })
    for (const [key, value] of Object.entries(recordFilters)) if (value) params.set(key, value)
    api<CategoryPage>(corpusPath(project.id, `/categories/${selected}?${params}`)).then((result) => { if (active) { setPage(result); setError('') } }).catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [project.id, project.has_corpus, selected, pageNo, query, recordFilters])

  const selectedCategory = selected === null ? null : categories.find((item) => item.number === selected)
  const visibleGroups = useMemo(() => summary?.groups.filter((group) => filter === '全部' || group.name === filter) || [], [summary, filter])
  const allArtifacts = useMemo(() => {
    const indexed = new Map<string, CorpusArtifact>()
    for (const category of categories) for (const artifact of category.artifacts || []) indexed.set(artifact.artifact_id, artifact)
    for (const artifact of summary?.auxiliary_assets || []) indexed.set(artifact.artifact_id, artifact)
    return [...indexed.values()].sort((left, right) => (left.category_number ?? 99) - (right.category_number ?? 99) || (left.path || left.name).localeCompare(right.path || right.name, 'zh-CN'))
  }, [categories, summary])
  const selectCategory = (number: number, search = '') => { setRecordFilters(emptyRecordFilters); setLocationSpan(null); setSelected(number); setPage(null); setPageNo(1); setQuery(search); setMode('content'); setGraphFocus(null); setDetail(null); setCategoryFilesOpen(false) }
  const openArtifactPurpose = (artifact: CorpusArtifact) => {
    const category = categories.find((item) => item.number === artifact.category_number)
    setPurpose({ kind: 'artifact', artifact, category, loading: true })
    api<CorpusArtifact>(corpusPath(project.id, `/artifacts/${encodeURIComponent(artifact.artifact_id)}`)).then((loaded) => {
      setPurpose((current) => current?.kind === 'artifact' && current.artifact.artifact_id === artifact.artifact_id
        ? { kind: 'artifact', artifact: { ...artifact, ...loaded }, category } : current)
    }).catch((cause: Error) => {
      setPurpose((current) => current?.kind === 'artifact' && current.artifact.artifact_id === artifact.artifact_id
        ? { kind: 'artifact', artifact, category, error: `单文件详情读取失败：${cause.message}` } : current)
    })
  }
  const showEntity = async (entity: GraphEntity) => {
    try {
      const result = await api<{ category_number?: number; detail: unknown; entity: GraphEntity }>(corpusPath(project.id, `/entities/${encodeURIComponent(entity.id)}`))
      setDetail({ title: `${entity.id} · ${entity.label}`, record: result.detail || result.entity, category: result.category_number, source: entity.source, artifactId: categories.flatMap((item) => item.artifacts || []).find((item) => item.path === entity.source)?.artifact_id })
    } catch (cause) { setError((cause as Error).message) }
  }

  if (!project.has_corpus) return <main className="page"><div className="breadcrumb">项目 / {project.name} / 项目资料</div><div className="page-header"><h1>项目资料</h1></div><section className="workspace-card"><div className="empty">暂无项目资料</div></section></main>

  return <main className="page">
    {selected === null ? <>
      <div className="breadcrumb">{project.name} / 项目资料</div><div className="page-header"><div><h1>项目资料</h1></div><div className="corpus-header-status"><span className="status status-neutral">历史语料 · 只读{summary ? ` · ${summary.version}` : ''}</span><Status value={summary?.review_status || '正在读取'} /></div></div>
      {error && <div className="notice error">{error}</div>}
      {summary && <>
        <div className="metric-grid corpus-metrics">
          <div className="metric"><span>类别</span><strong>{categories.length}</strong><small>{summary.groups.length} 组</small></div>
          <div className="metric"><span>文件</span><strong>{allArtifacts.length}</strong><small>{summary.integrity.valid ? '校验通过' : '完整性异常'}</small></div>
          <div className="metric"><span>关系</span><strong>{summary.counts.relations?.toLocaleString()}</strong><small>{summary.counts.nodes} 节点</small></div>
          <div className="metric"><span>向量</span><strong className="metric-word">未生成</strong></div>
        </div>
        <div className="section-heading">
          <div><h2>内容目录</h2></div>
          <div className="toolbar"><button className="subtle-button" onClick={() => setAllFilesOpen((open) => !open)} aria-expanded={allFilesOpen}>{allFilesOpen ? '收起文件' : `全部文件 ${allArtifacts.length}`}</button><select aria-label="产物分组" value={filter} onChange={(event) => setFilter(event.target.value)}><option>全部</option>{summary.groups.map((group) => <option key={group.name}>{group.name}</option>)}</select></div>
        </div>
        {allFilesOpen && <div id="corpus-assets"><ArtifactList title="全部文件" artifacts={allArtifacts} categories={categories} projectId={project.id} onPurpose={openArtifactPurpose} showCategory /></div>}
        {visibleGroups.map((group) => <section className="catalog-group" key={group.name}>
          <div className="catalog-group-title"><Layers3 size={18} /><h3>{group.name}</h3><span>{group.count} 类</span></div>
          <div className="catalog-grid">{categories.filter((category) => category.group === group.name).map((category) => <div key={category.number} className="catalog-card">
            <button className="catalog-card-main" onClick={() => selectCategory(category.number)} aria-label={'查看' + category.name + '内容'}>
              <div className="catalog-top"><span className="catalog-num">{String(category.number).padStart(2, '0')}</span><Status value={category.content_status === 'populated' ? '已生成' : category.content_status === 'schema_only_no_embeddings' ? '向量未生成' : category.content_status} /></div>
              <strong>{category.name}</strong>
            </button>
            <div className="catalog-bottom"><span>{category.artifacts?.length ?? category.count} 个文件 · {category.record_count ?? 0} 条记录</span><button className="text-button" onClick={() => setPurpose({ kind: 'category', category })}>作用说明</button></div>
          </div>)}</div>
        </section>)}
      </>}
    </> : <>
      <div className="breadcrumb"><button onClick={() => setSelected(null)}>{project.name} / 项目资料</button><ChevronRight size={14} />{selectedCategory?.group}<ChevronRight size={14} />第 {String(selected).padStart(2, '0')} 类</div>
      <div className="page-header"><div><button className="back-link" onClick={() => setSelected(null)}><ArrowLeft size={16} /> 返回目录</button><h1>{selectedCategory?.name}</h1></div><div className="category-header-actions">{selectedCategory && <button className="subtle-button" onClick={() => setPurpose({ kind: 'category', category: selectedCategory })}>作用说明</button>}<span className="catalog-num large">{String(selected).padStart(2, '0')}</span></div></div>
      {error && <div className="notice error">{error}</div>}
      <div className="category-meta"><span>{selectedCategory?.group}</span><span>{selectedCategory?.format}</span><span>{page?.total ?? selectedCategory?.record_count ?? 0} 条记录</span><Status value={selectedCategory?.content_status} /></div>


      <div className="category-workspace"><div className="workspace-toolbar"><h2>内容预览</h2><div className="toolbar"><div className="search-input"><Search size={16} /><input aria-label="搜索当前产物" value={query} onChange={(event) => { setQuery(event.target.value); setPageNo(1) }} placeholder="搜索当前产物" /></div>{graphCategories.has(selected) && <div className="segmented"><button className={mode === 'content' ? 'active' : ''} onClick={() => setMode('content')}><ListFilter size={15} /> 内容</button><button className={mode === 'graph' ? 'active' : ''} onClick={() => setMode('graph')}><GitBranch size={15} /> 图谱</button></div>}</div></div><CategoryFilters number={selected} options={page?.filter_options || {}} values={recordFilters} change={(key, value) => { setRecordFilters((previous) => ({ ...previous, [key]: value })); setPageNo(1); setPage(null) }} />{mode === 'graph' ? <GraphPanel projectId={project.id} initialFocus={graphFocus || focusByCategory[selected]} onEntity={showEntity} /> : page ? <><CategoryContent projectId={project.id} number={selected} page={page} catalog={categories} locationSpan={locationSpan} query={query} onDetail={(record, title) => setDetail({ title: title || '记录详情', record, category: selected, source: (record as Rec)?.file || selectedCategory?.files[0], artifactId: (record as Rec)?._artifact_id || selectedCategory?.artifacts?.[0]?.artifact_id })} onGraph={(id) => { setGraphFocus(id); setMode('graph') }} />{page.total > page.size && <div className="pagination"><span>共 {page.total} 条 · 第 {page.page} 页</span><button disabled={pageNo <= 1} onClick={() => setPageNo(pageNo - 1)}>上一页</button><button disabled={pageNo * page.size >= page.total} onClick={() => setPageNo(pageNo + 1)}>下一页</button></div>}</> : <div className="empty">正在读取产物…</div>}</div>
      {selectedCategory && <div className="corpus-files-toggle"><button className="subtle-button" onClick={() => setCategoryFilesOpen((open) => !open)} aria-expanded={categoryFilesOpen}>{categoryFilesOpen ? '收起文件' : `入库文件 ${selectedCategory.artifacts?.length || 0}`}</button>{categoryFilesOpen && <ArtifactList title="入库文件" artifacts={selectedCategory.artifacts || []} categories={categories} projectId={project.id} onPurpose={openArtifactPurpose} />}</div>}
    </>}
    {detail && <DetailDrawer projectId={project.id} detail={detail} wordAssetId={categories.find((item) => item.number === 5)?.artifacts?.[0]?.artifact_id || 'source/original.docx'} onClose={() => setDetail(null)} openCategory={selectCategory} openGraph={(id) => { selectCategory(15); setGraphFocus(id); setMode('graph') }} openLocation={(span) => { selectCategory(6); setLocationSpan(span) }} />}
    {purpose && <PurposeDrawer projectId={project.id} purpose={purpose} onClose={() => setPurpose(null)} openCategory={selectCategory} />}
  </main>
}
