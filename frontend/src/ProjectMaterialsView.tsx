import { useEffect, useRef, useState } from 'react'
import { FileText, Search, X } from 'lucide-react'
import { api, type Fact, type Project } from './api'
import './project-materials.css'

type DocumentItem = { id: string; filename: string; file_kind: string; pages: number; segments: number; status: string }
type FactSource = { kind: string; description: string; review_status: string; filename?: string; locator?: string;
  document_id?: string; page?: number; source_ref?: string;
  excerpt?: string | null; original_url?: string | null; rule?: { name: string; expression: string };
  input_facts?: { label: string; value: string | null; unit: string }[] }

export default function ProjectMaterialsView({ project, onOpenDocument, onOpenFacts }: {
  project: Project; onOpenDocument: (documentId?: string, page?: number, segment?: string) => void; onOpenFacts: () => void
}) {
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [facts, setFacts] = useState<Fact[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Fact | null>(null)
  const [source, setSource] = useState<FactSource | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const sourceRequest = useRef(0)

  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    Promise.all([
      api<DocumentItem[]>(`/projects/${project.id}/documents`),
      api<{ facts: Fact[] }>(`/projects/${project.id}/facts`),
    ]).then(([files, ledger]) => {
      if (active) { setDocuments(files); setFacts(ledger.facts); setLoading(false) }
    }).catch((cause: Error) => { if (active) { setError(cause.message); setLoading(false) } })
    return () => { active = false }
  }, [project.id])

  const openFact = async (fact: Fact) => {
    const request = ++sourceRequest.current
    setSelected(fact); setSource(null); setSourceError('')
    try {
      const detail = await api<FactSource>(`/projects/${project.id}/facts/${encodeURIComponent(fact.key)}/source`)
      if (request === sourceRequest.current) setSource(detail)
    } catch (cause) { if (request === sourceRequest.current) setSourceError((cause as Error).message) }
  }
  const closeFact = () => { ++sourceRequest.current; setSelected(null) }
  const needle = query.trim().toLocaleLowerCase()
  const visibleDocuments = documents.filter((item) => item.filename.toLocaleLowerCase().includes(needle))
  const visibleFacts = facts.filter((item) => `${item.label} ${item.value ?? ''} ${item.source}`.toLocaleLowerCase().includes(needle))

  return <main className="page project-materials">
    <div className="breadcrumb">项目 / {project.name} / 项目资料</div>
    <div className="page-header"><h1>项目资料</h1></div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {loading ? <div className="workspace-card empty">加载中…</div> : <>
      <div className="project-materials-summary"><span>原件 <strong>{documents.length}</strong></span><span>原文段落 <strong>{documents.reduce((total, item) => total + item.segments, 0)}</strong></span><span>项目事实 <strong>{facts.length}</strong></span></div>
      {(documents.length > 0 || facts.length > 0) && <label className="project-materials-search"><Search size={15} /><input aria-label="搜索项目资料" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件或事实" /></label>}
      <section className="workspace-card project-materials-section"><div className="project-materials-head"><h2>原件与原文</h2><button className="subtle-button" onClick={() => onOpenDocument()}>项目文件</button></div>
        {visibleDocuments.length ? <div className="project-materials-list">{visibleDocuments.map((item) => <div className="project-materials-row" key={item.id}><FileText size={17} /><div><strong>{item.filename}</strong><small>{item.file_kind.toUpperCase()} · {item.pages} 页 · {item.segments} 段原文{item.status === 'OCR_REQUIRED' ? ' · 部分页待识别' : ''}</small></div><button className="subtle-button" onClick={() => onOpenDocument(item.id)}>查看内容</button></div>)}</div>
          : <div className="empty">{needle ? '没有匹配的文件' : <button className="text-button" onClick={() => onOpenDocument()}>上传首份原件</button>}</div>}
      </section>
      <section className="workspace-card project-materials-section"><div className="project-materials-head"><h2>事实台账</h2><button className="subtle-button" onClick={onOpenFacts}>项目事实</button></div>
        {visibleFacts.length ? <div className="project-materials-list">{visibleFacts.map((item) => <div className="project-materials-row" key={item.id}><div><strong>{item.label}</strong><small>{item.status === 'COMPUTED' ? '计算结果' : item.value === null ? '未填写' : '已录入'} · v{item.revision}</small></div><span className="project-materials-value">{item.value === null ? '—' : `${item.value}${item.unit ? ` ${item.unit}` : ''}`}</span><button className="subtle-button" onClick={() => void openFact(item)}>查看来源</button></div>)}</div>
          : <div className="empty">{needle ? '没有匹配的事实' : <button className="text-button" onClick={onOpenFacts}>建立项目事实</button>}</div>}
      </section>
    </>}
    {selected && <div className="dialog-backdrop" onClick={closeFact}><div className="dialog project-materials-dialog" role="dialog" aria-modal="true" aria-label={`${selected.label}来源`} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === 'Escape') closeFact() }}><div className="dialog-head"><h2>{selected.label}</h2><button className="icon-button" aria-label="关闭" onClick={closeFact}><X size={18} /></button></div><div className="project-materials-detail"><strong>{selected.value === null ? '未填写' : `${selected.value}${selected.unit ? ` ${selected.unit}` : ''}`}</strong><small>修订 v{selected.revision}</small>{sourceError && <div className="notice error">{sourceError}</div>}{!source && !sourceError && <div className="empty">读取来源中…</div>}{source && <><p>{source.review_status === 'SOURCE_LOCATOR_REVIEWED' ? '原文位置已核对' : source.review_status === 'DERIVED_FROM_REVIEWED_INPUTS' ? '上游来源已核对' : '来源待核对'}</p><p>{source.filename || source.description}{source.locator ? ` · ${source.locator}` : ''}</p>{source.rule && <p>{source.rule.name}：{source.rule.expression}</p>}{source.input_facts?.map((item) => <p key={item.label}>{item.label}：{item.value ?? '未定义'} {item.unit}</p>)}{source.excerpt && <blockquote>{source.excerpt}</blockquote>}{source.document_id && source.page && source.source_ref && <button className="subtle-button" onClick={() => { closeFact(); onOpenDocument(source.document_id, source.page, source.source_ref) }}>查看原文位置</button>}{source.original_url && <a className="subtle-button" href={source.original_url} target="_blank" rel="noreferrer">打开原件</a>}</>}</div></div></div>}
  </main>
}
