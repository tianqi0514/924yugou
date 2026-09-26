import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, FileSearch, FileUp, RefreshCw, X } from 'lucide-react'
import { api, post, type Project } from './api'
import './documents-view.css'

type DocumentItem = { id: string; filename: string; file_kind: string; sha256: string; pages: number; segments: number; status: string; created_at: string }
type Segment = { ref: string; page: number; text: string; locator?: string; kind?: string }
type Candidate = { id: string; label: string; value: string; unit: string; data_type: string; source_ref: string; excerpt: string; source_valid: boolean; review_status: string; fact_key: string | null; extraction_run_id: string | null; extraction_origin: 'MODEL' | 'TABLE' | 'UNKNOWN'; extraction_model: string | null; extraction_model_version: string | null; approved_fact?: { key: string; label: string; value: string; unit: string; source: string } | null }
type Detail = { document: DocumentItem; page: number; segments: Segment[]; candidates: Candidate[] }
type PageInfo = { page: number; segment_count: number; char_count: number; candidate_count: number; pending_count: number; invalid_count: number; extractable: boolean; reason: string }
type BatchResult = { page: number; created?: number; refreshed?: number; error?: string; skipped?: string }
type SearchHit = { ref: string; page: number; locator: string; excerpt: string }
function parsePageRange(input: string, maximum: number): number[] {
  const selected = new Set<number>()
  for (const part of input.trim().split(/[,，\s]+/).filter(Boolean)) {
    const match = /^(\d+)(?:[-–](\d+))?$/.exec(part)
    if (!match) throw new Error('页码格式应为 5,8-10；多个页码用逗号分开')
    const start = Number(match[1]), end = Number(match[2] || match[1])
    if (start < 1 || end > maximum || end < start) throw new Error(`页码须在 1–${maximum} 之间，范围起点不能大于终点`)
    if (end - start > 7) throw new Error('单次最多选择 8 页，请缩小范围')
    for (let page = start; page <= end; page++) selected.add(page)
    if (selected.size > 8) throw new Error('单次最多选择 8 页，请分批抽取')
  }
  if (!selected.size) throw new Error('请先输入要抽取的页码')
  return [...selected].sort((a, b) => a - b)
}

function CandidateCard({ item, segments, projectId, documentId, reload, expanded, onToggle }: { item: Candidate; segments: Segment[]; projectId: string; documentId: string; reload: () => Promise<void>; expanded: boolean; onToggle: () => void }) {
  const [label, setLabel] = useState(item.label)
  const [value, setValue] = useState(item.value)
  const [unit, setUnit] = useState(item.unit)
  const [kind, setKind] = useState(item.data_type)
  const [ref, setRef] = useState(item.source_ref)
  const [supportRefs, setSupportRefs] = useState(['', ''])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const endpoint = `/projects/${projectId}/documents/${documentId}/candidates/${item.id}`
  const act = async (decision: 'approve' | 'reject') => {
    setBusy(true); setError('')
    try {
      await post(endpoint + `/${decision}`, decision === 'approve' ? { label, value, unit, data_type: kind, source_ref: ref, support_refs: supportRefs.filter(Boolean) } : {})
      await reload(); onToggle()
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const mainSegment = segments.find((segment) => segment.ref === ref)
  const labelVisible = (mainSegment?.text || '').replace(/\s+/g, '').includes(label.replace(/\s+/g, ''))
  return <div id={`candidate-${item.id}`} className={`candidate-card candidate-compact ${expanded ? 'expanded' : ''}`}>
    <div className="candidate-title"><div><strong>{item.approved_fact?.label || item.label}</strong><small>{item.approved_fact ? `${item.approved_fact.value}${item.approved_fact.unit}` : `${item.value}${item.unit}`} · {item.source_ref}{/-t\d+-r\d+$/.test(item.source_ref) ? ' · 表格行' : ''} · {item.extraction_origin === 'TABLE' ? '表格解析' : item.extraction_origin === 'MODEL' ? item.extraction_model || '模型建议' : '历史候选'}</small></div><span className={`status ${item.review_status === 'APPROVED' ? 'status-success' : item.review_status === 'REJECTED' ? 'status-neutral' : item.source_valid ? 'status-warn' : 'status-danger'}`}>{item.review_status === 'APPROVED' ? '已确认' : item.review_status === 'REJECTED' ? '已拒绝' : item.source_valid ? '待审核' : '引用需核对'}</span>{item.review_status === 'PENDING' && <button type="button" className="subtle-button" onClick={onToggle}>{expanded ? '收起' : '核对'}</button>}</div>

    {item.review_status === 'PENDING' && expanded && <><div className="candidate-form"><label>事实名称<input value={label} onChange={(event) => setLabel(event.target.value)} /></label><label>值<input value={value} onChange={(event) => setValue(event.target.value)} /></label><label>单位<input value={unit} onChange={(event) => setUnit(event.target.value)} /></label><label>类型<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="text">文本</option><option value="integer">整数</option><option value="decimal">小数</option><option value="date">日期</option></select></label></div><label className="candidate-ref">值所在位置<select value={ref} onChange={(event) => { setRef(event.target.value); setSupportRefs(['', '']) }}>{segments.map((segment) => <option key={segment.ref} value={segment.ref}>{segment.ref} · {segment.text.slice(0, 45)}</option>)}</select></label><p className="candidate-excerpt">{mainSegment?.text || item.excerpt}</p>{!labelVisible && <div className="notice warn candidate-context-hint">名称未出现在当前片段，请核对位置。</div>}<details className="candidate-support"><summary>补充原文位置</summary>{supportRefs.map((selected, index) => <label key={index}>补充位置 {index + 1}<select value={selected} onChange={(event) => setSupportRefs((old) => old.map((value, slot) => slot === index ? event.target.value : value))}><option value="">不添加</option>{segments.filter((segment) => segment.ref !== ref && !supportRefs.some((value, slot) => slot !== index && value === segment.ref)).map((segment) => <option key={segment.ref} value={segment.ref}>{segment.ref} · {segment.text.slice(0, 55)}</option>)}</select></label>)}{supportRefs.filter(Boolean).map((extra) => <p className="candidate-excerpt" key={extra}><b>{extra}</b> · {segments.find((segment) => segment.ref === extra)?.text}</p>)}</details><div className="inline-actions"><button disabled={busy || !label.trim() || !value.trim()} onClick={() => void act('approve')}><Check size={13} /> 确认入台账</button><button disabled={busy} onClick={() => void act('reject')}><X size={13} /> 拒绝</button></div></>}
    {item.review_status !== 'PENDING' && item.fact_key && <div className="candidate-finished">已入事实台账 <b>{item.fact_key}</b>{item.approved_fact && (item.approved_fact.value !== item.value || item.approved_fact.label !== item.label || item.approved_fact.unit !== item.unit) && <span> · 模型原建议：{item.label} {item.value}{item.unit}</span>}</div>}
    {error && <div className="notice error" role="alert">{error}</div>}
  </div>
}

export default function DocumentsView({ project, notify }: { project: Project; notify: (message: string) => void }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [documentId, setDocumentId] = useState('')
  const [page, setPage] = useState(() => Math.max(1, Number(new URLSearchParams(window.location.search).get('page')) || 1))
  const [activeSegment, setActiveSegment] = useState(new URLSearchParams(window.location.search).get('segment') || '')
  const [jumpValue, setJumpValue] = useState('1')
  const [rangeValue, setRangeValue] = useState('1')
  const [pageMap, setPageMap] = useState<PageInfo[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [modelReady, setModelReady] = useState(false)
  const [ocrReady, setOcrReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ done: 0, total: 0, current: 0 })
  const [batchResults, setBatchResults] = useState<BatchResult[]>([])
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null)
  const cancelBatch = useRef(false)
  const [error, setError] = useState('')
  const [fileQuery, setFileQuery] = useState('')
  const [sourceQuery, setSourceQuery] = useState('')
  const [searchHits, setSearchHits] = useState<SearchHit[]>([])
  const [searchTotal, setSearchTotal] = useState(0)
  const [searchBusy, setSearchBusy] = useState(false)
  const [searched, setSearched] = useState(false)
  const searchGeneration = useRef(0)
  const loadList = useCallback(async () => {
    const items = await api<DocumentItem[]>(`/projects/${project.id}/documents`)
    const requested = new URLSearchParams(window.location.search).get('document')
    setDocuments(items)
    setDocumentId((old) => items.some((item) => item.id === requested) ? requested! : items.some((item) => item.id === old) ? old : items[0]?.id || '')
  }, [project.id])
  const loadDetail = useCallback(async () => {
    if (!documentId) { setDetail(null); return }
    setDetail(await api<Detail>(`/projects/${project.id}/documents/${documentId}?page=${page}`))
  }, [project.id, documentId, page])
  const loadPageMap = useCallback(async () => {
    if (!documentId) { setPageMap([]); return }
    const result = await api<{ pages: PageInfo[] }>(`/projects/${project.id}/documents/${documentId}/pages`)
    setPageMap(result.pages)
  }, [project.id, documentId])
  useEffect(() => { void loadList().catch((cause: Error) => setError(cause.message)); void api<{ configured: boolean; ocr_configured?: boolean }>('/model/status').then((value) => { setModelReady(value.configured); setOcrReady(Boolean(value.ocr_configured)) }) }, [loadList])
  useEffect(() => { void loadDetail().catch((cause: Error) => setError(cause.message)) }, [loadDetail])
  useEffect(() => { void loadPageMap().catch((cause: Error) => setError(cause.message)) }, [loadPageMap])
  useEffect(() => { setJumpValue(String(page)); setActiveCandidateId(null) }, [page, documentId])
  useEffect(() => {
    if (!activeSegment || !detail?.segments.some((segment) => segment.ref === activeSegment)) return
    window.requestAnimationFrame(() => document.getElementById(`segment-${activeSegment}`)?.scrollIntoView({ block: 'center' }))
  }, [activeSegment, detail])
  useEffect(() => () => { cancelBatch.current = true }, [])
  const reloadCurrent = async () => { await Promise.all([loadDetail(), loadPageMap()]) }
  const navigate = (target: number, segment = '') => {
    if (!Number.isInteger(target) || target < 1 || target > (detail?.document.pages || 0)) { setError('页码超出文件范围'); return }
    setError(''); setPage(target); setRangeValue(String(target)); setActiveSegment(segment)
    const url = new URL(window.location.href)
    url.searchParams.set('page', String(target))
    if (segment) url.searchParams.set('segment', segment)
    else url.searchParams.delete('segment')
    window.history.replaceState({}, '', url)
  }
  const chooseDocument = (id: string) => {
    searchGeneration.current += 1
    setDocumentId(id); setPage(1); setRangeValue('1'); setActiveSegment(''); setPageMap([]); setBatchResults([])
    setSearchHits([]); setSearchTotal(0); setSourceQuery(''); setSearched(false); setSearchBusy(false)
    setBatchProgress({ done: 0, total: 0, current: 0 }); setError('')
    const url = new URL(window.location.href)
    url.searchParams.set('document', id)
    url.searchParams.delete('page'); url.searchParams.delete('segment')
    window.history.replaceState({}, '', url)
  }
  const runSearch = async (offset = 0) => {
    if (!documentId || sourceQuery.trim().length < 2) { setError('至少输入两个字符'); return }
    const generation = ++searchGeneration.current
    setSearchBusy(true); setError('')
    try {
      const result = await api<{ total: number; results: SearchHit[] }>(`/projects/${project.id}/documents/${documentId}/search?q=${encodeURIComponent(sourceQuery.trim())}&offset=${offset}`)
      if (generation !== searchGeneration.current) return
      setSearchHits((old) => offset ? [...old, ...result.results] : result.results)
      setSearchTotal(result.total); setSearched(true)
    } catch (cause) { if (generation === searchGeneration.current) setError((cause as Error).message) }
    finally { if (generation === searchGeneration.current) setSearchBusy(false) }
  }
  const updateSourceQuery = (value: string) => {
    searchGeneration.current += 1
    setSourceQuery(value); setSearchHits([]); setSearchTotal(0); setSearched(false); setSearchBusy(false)
  }
  const upload = async (file: File | undefined) => {
    if (!file) return
    if (!/\.(pdf|docx)$/i.test(file.name)) { setError('只支持 PDF 或 DOCX 文件'); return }
    if (!file.size || file.size > 30 * 1024 * 1024) { setError('文件为空或超过 30 MB'); return }
    setBusy(true); setError('')
    try {
      const form = new FormData(); form.append('file', file)
      const response = await fetch(`/api/projects/${project.id}/documents`, { method: 'POST', body: form })
      if (!response.ok) { const body = await response.json(); throw new Error(body.detail || '上传失败') }
      const created = await response.json() as DocumentItem
      await loadList(); chooseDocument(created.id); notify('原件已保存')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const recognize = async () => {
    if (!documentId) return
    setBusy(true); setError('')
    try {
      const result = await post<{ chars: number }>(`/projects/${project.id}/documents/${documentId}/ocr?page=${page}`, {})
      await reloadCurrent(); notify(`识别出 ${result.chars} 字，请先核对文字，再抽取事实候选`)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const extractSelected = async () => {
    if (!documentId || !detail) return
    let selected: number[]
    try { selected = parsePageRange(rangeValue, detail.document.pages) }
    catch (cause) { setError((cause as Error).message); return }
    const selectedDocument = documentId
    cancelBatch.current = false; setBatchBusy(true); setError(''); setBatchResults([])
    setBatchProgress({ done: 0, total: selected.length, current: selected[0] })
    const results: BatchResult[] = []
    for (const currentPage of selected) {
      if (cancelBatch.current) break
      setBatchProgress({ done: results.length, total: selected.length, current: currentPage })
      const summary = pageMap.find((item) => item.page === currentPage)
      if (summary && !summary.extractable) {
        results.push({ page: currentPage, skipped: summary.reason })
      } else {
        try {
          const result = await post<{ created: number; refreshed: number }>(`/projects/${project.id}/documents/${selectedDocument}/extract?page=${currentPage}`, {})
          results.push({ page: currentPage, created: result.created, refreshed: result.refreshed })
        } catch (cause) { results.push({ page: currentPage, error: (cause as Error).message }) }
      }
      setBatchResults([...results]); setBatchProgress({ done: results.length, total: selected.length, current: currentPage })
    }
    setBatchBusy(false)
    await Promise.all([loadDetail(), loadPageMap()]).catch((cause: Error) => setError(cause.message))
    const created = results.reduce((total, row) => total + (row.created || 0), 0)
    notify(`已处理 ${results.length}/${selected.length} 页，新增 ${created} 条待审候选`)
  }
  const pendingPages = pageMap.filter((item) => item.pending_count > 0)
  const nextPendingPage = pendingPages.find((item) => item.page > page) || pendingPages.find((item) => item.page < page)
  const pendingCandidates = detail?.candidates.filter((candidate) => candidate.review_status === 'PENDING') || []
  const activeCandidateIndex = pendingCandidates.findIndex((candidate) => candidate.id === activeCandidateId)
  const nextCandidate = pendingCandidates.length > 1 || activeCandidateIndex < 0
    ? pendingCandidates[(activeCandidateIndex + 1) % pendingCandidates.length] : undefined
  const visibleDocuments = documents.filter((item) => item.filename.toLocaleLowerCase().includes(fileQuery.trim().toLocaleLowerCase()))
  return <main className="page"><div className="breadcrumb">项目 / {project.name} / 项目文件</div><div className="page-header"><div><h1>项目文件</h1></div>{project.has_corpus ? <span className="status status-neutral">只读</span> : <label className="primary-button upload-button"><FileUp size={15} /> 上传 PDF / DOCX<input type="file" accept=".pdf,.docx" disabled={busy || batchBusy} onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = '' }} /></label>}</div>
    {error && <div className="notice error" role="alert">{error}</div>}
    <div className="document-layout"><aside className="workspace-card document-list"><h2>已上传文件</h2>{documents.length > 1 && <input aria-label="搜索文件" className="document-list-search" value={fileQuery} onChange={(event) => setFileQuery(event.target.value)} placeholder="搜索文件名" />}{documents.length === 0 ? <p className="empty">{project.has_corpus ? '暂无文件' : '暂无文件，点击右上角上传'}</p> : visibleDocuments.length ? visibleDocuments.map((item) => <button className={item.id === documentId ? 'active' : ''} key={item.id} disabled={busy || batchBusy} onClick={() => chooseDocument(item.id)}><strong>{item.filename}</strong><small>{item.file_kind.toUpperCase()} · {item.pages} {item.file_kind === 'pdf' ? '页' : '份'} · {item.status === 'OCR_REQUIRED' ? '部分页需 OCR' : '可查看'}</small></button>) : <div className="empty">没有匹配文件</div>}</aside>
      <div className="document-main">{detail ? <><section className="workspace-card"><div className="workspace-toolbar"><div><h2>{detail.document.filename}</h2></div><div className="toolbar"><a className="subtle-button" href={`/api/projects/${project.id}/documents/${documentId}/original`} target="_blank" rel="noreferrer">打开原件</a><button className="subtle-button" onClick={() => void loadDetail()}><RefreshCw size={13} /> 刷新</button></div></div><div className="document-search"><input aria-label="搜索原文" value={sourceQuery} onChange={(event) => updateSourceQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void runSearch() }} placeholder="搜索全文" /><button disabled={searchBusy || sourceQuery.trim().length < 2} onClick={() => void runSearch()}>{searchBusy ? '搜索中…' : '搜索'}</button>{searched && <button className="text-button" onClick={() => updateSourceQuery('')}>清除</button>}</div>{searched && <div className="document-search-results"><strong>匹配片段 {searchTotal}</strong>{searchHits.map((hit) => <button key={hit.ref} onClick={() => navigate(hit.page, hit.ref)}><b>第 {hit.page} 页 · {hit.ref}</b><span>{hit.excerpt}</span></button>)}{searchHits.length < searchTotal && <button className="subtle-button" disabled={searchBusy} onClick={() => void runSearch(searchHits.length)}>查看更多</button>}</div>}<div className="document-page-nav"><button disabled={page <= 1 || batchBusy} onClick={() => navigate(page - 1)}>上一页</button><span>{detail.document.file_kind === 'pdf' ? `PDF 第 ${page} / ${detail.document.pages} 页` : 'DOCX 文字与表格'}</span><button disabled={page >= detail.document.pages || batchBusy} onClick={() => navigate(page + 1)}>下一页</button><label>跳转 <input aria-label="跳转页码" type="number" min={1} max={detail.document.pages} value={jumpValue} onChange={(event) => setJumpValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') navigate(Number(jumpValue)) }} /></label><button disabled={batchBusy} onClick={() => navigate(Number(jumpValue))}>前往</button></div>{pageMap.find((item) => item.page === page) && <div className="document-page-status">{pageMap.find((item) => item.page === page)?.segment_count} 段原文 · {pageMap.find((item) => item.page === page)?.pending_count} 条待审</div>}{pendingPages.length > 0 && <div className="document-pending-pages"><strong>待审页</strong>{pendingPages.slice(0, 24).map((item) => <button key={item.page} className={page === item.page ? 'active' : ''} disabled={batchBusy} onClick={() => navigate(item.page)}>{item.page} <small>{item.pending_count}</small></button>)}{pendingPages.length > 24 && <span>另有 {pendingPages.length - 24} 页</span>}{nextPendingPage && <button className="subtle-button" disabled={batchBusy} onClick={() => navigate(nextPendingPage.page)}>{nextPendingPage.page > page ? '下一待审页' : '返回首个待审页'}</button>}</div>}<div className="document-source-scroll">{detail.segments.length ? detail.segments.map((segment) => <div id={`segment-${segment.ref}`} className={`document-segment ${segment.kind === 'table_row' ? 'document-table-row' : ''} ${activeSegment === segment.ref ? 'document-segment-active' : ''}`} key={segment.ref}><small>{segment.ref} {segment.locator || ''}{segment.kind === 'table_row' ? ' · 表格行' : segment.kind === 'ocr' ? ' · OCR 待核对' : ''}</small><p>{segment.text}</p></div>) : <div className="empty">本页无可读取文字。{detail.document.file_kind === 'pdf' && <div className="inline-actions"><button className="primary-button" disabled={busy || batchBusy || !ocrReady} onClick={() => void recognize()}>{busy ? '识别中…' : '识别当前页'}</button>{!ocrReady && <small>扫描页识别未配置</small>}</div>}</div>}</div></section>
        {!project.has_corpus && <><section className="workspace-card"><div className="workspace-toolbar"><h2>抽取事实</h2></div><div className="document-batch-controls"><input aria-label="待抽取页码" value={rangeValue} onChange={(event) => setRangeValue(event.target.value)} placeholder="如 5,8-10" disabled={batchBusy || busy} /><button className="primary-button" disabled={batchBusy || busy || !modelReady || !pageMap.length} onClick={() => void extractSelected()}><FileSearch size={14} /> {batchBusy ? '抽取中…' : '抽取'}</button>{batchBusy && <button className="subtle-button" onClick={() => { cancelBatch.current = true }}>当前页完成后停止</button>}</div>{batchProgress.total > 0 && <div className="document-batch-progress">已处理 {batchProgress.done}/{batchProgress.total} 页{batchBusy && ` · 正在处理第 ${batchProgress.current} 页`}</div>}{batchResults.length > 0 && <div className="document-batch-results">{batchResults.map((row) => <button key={row.page} type="button" onClick={() => navigate(row.page)} disabled={batchBusy}><b>第 {row.page} 页</b><span>{row.error ? `失败：${row.error}` : row.skipped ? `跳过：${row.skipped}` : `新增 ${row.created} 条${row.refreshed ? `，更新 ${row.refreshed} 条来源校验` : ''}`}</span></button>)}</div>}</section><section className="workspace-card"><div className="workspace-toolbar"><h2>本页候选</h2>{nextCandidate && <button className="subtle-button" onClick={() => { setActiveCandidateId(nextCandidate.id); window.requestAnimationFrame(() => document.getElementById(`candidate-${nextCandidate.id}`)?.scrollIntoView({ block: 'center' })) }}>下一待审项</button>}</div>{!modelReady && <div className="notice warn">事实抽取模型未配置</div>}{detail.candidates.length ? <><div className="candidate-counts">{pendingCandidates.length} 条待审 · {pendingCandidates.filter((candidate) => !candidate.source_valid).length} 条引用需核对</div>{detail.candidates.map((candidate) => <CandidateCard key={candidate.id} item={candidate} segments={detail.segments} projectId={project.id} documentId={documentId} reload={reloadCurrent} expanded={activeCandidateId === candidate.id} onToggle={() => setActiveCandidateId((old) => old === candidate.id ? null : candidate.id)} />)}</> : <div className="empty">暂无候选</div>}</section></>}</> : documents.length ? <div className="workspace-card empty">选择文件</div> : null}</div></div>
  </main>
}
