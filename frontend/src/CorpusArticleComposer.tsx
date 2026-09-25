import { useEffect, useState } from 'react'
import { Check, Sparkles } from 'lucide-react'
import { api, post, type Project } from './api'

type Section = { id: string; name: string }
type Source = { id: string; record_id: string; text: string; page: number | null }
type Block = { type: string; children: { text: string }[]; source_refs?: { semantic_id: string }[] }
type Candidate = {
  title: string; section_id: string; content: Block[]; corpus_version: string; input_sha256: string;
  model_call: Record<string, unknown>; expires_at: number; preview_token: string; sources: Source[]
}

export default function CorpusArticleComposer({ project, onSaved }: {
  project: Project; onSaved: (reportId: string) => void
}) {
  const [sections, setSections] = useState<Section[]>([])
  const [sectionId, setSectionId] = useState('S4')
  const [title, setTitle] = useState('')
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    void api<Section[]>(`/projects/${project.id}/corpus/articles/sections`)
      .then(setSections).catch((cause: Error) => setError(cause.message))
  }, [project.id])
  const generate = async () => {
    setBusy(true); setError(''); setCandidate(null)
    try {
      setCandidate(await post<Candidate>(`/projects/${project.id}/corpus/articles/preview`, {
        section_id: sectionId, title: title.trim(),
      }))
    } catch (cause) { setError((cause as Error).message) }
    finally { setBusy(false) }
  }
  const save = async () => {
    if (!candidate) return
    setBusy(true); setError('')
    try {
      const { sources: _sources, ...payload } = candidate
      const result = await post<{ report: { id: string } }>(
        `/projects/${project.id}/corpus/articles/commit`, payload)
      setCandidate(null)
      setTitle('')
      onSaved(result.report.id)
    } catch (cause) { setError((cause as Error).message); setCandidate(null) }
    finally { setBusy(false) }
  }
  return <section className="workspace-card corpus-article-composer" aria-label="模型写作">
    <div className="workspace-toolbar"><h2>模型写作</h2><span className="status status-neutral">历史语料 · 待核对</span></div>
    <div className="corpus-article-fields">
      <label className="form-field"><span>选择章节</span><select value={sectionId} disabled={busy}
        onChange={(event) => { setSectionId(event.target.value); setCandidate(null) }}>
        {sections.map((section) => <option key={section.id} value={section.id}>{section.name}</option>)}
      </select></label>
      <label className="form-field"><span>文章标题</span><input value={title} disabled={busy} maxLength={160}
        onChange={(event) => { setTitle(event.target.value); setCandidate(null) }} placeholder="输入新文章标题" /></label>
      <button className="primary-button" disabled={busy || !title.trim() || !sections.length}
        onClick={() => void generate()}><Sparkles size={15} />{busy ? '生成中…' : '生成候选'}</button>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {candidate && <div className="report-preview corpus-article-candidate">
      <strong>{candidate.title} · 待确认</strong>
      {candidate.content.slice(1).map((block, index) => <div key={index}>
        <small>第 {index + 1} 段 · 来源 {(block.source_refs || []).map((ref) => ref.semantic_id).join('、')}</small>
        <p>{block.children.map((part) => part.text).join('')}</p>
      </div>)}
      <details><summary>查看本次输入 · {candidate.sources.length} 段</summary>
        {candidate.sources.map((source) => <div key={source.id} className="corpus-article-source">
          <b>{source.id} · 第 {source.page ?? '—'} 页</b><p>{source.text}</p>
        </div>)}
      </details>
      <div className="inline-actions"><button type="button" disabled={busy} onClick={() => setCandidate(null)}>取消</button>
        <button type="button" className="primary-button" disabled={busy} onClick={() => void save()}>
          <Check size={15} />确认并保存文章</button></div>
    </div>}
  </section>
}
