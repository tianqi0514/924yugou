import { useCallback, useEffect, useState } from 'react'
import { BookOpenCheck, ChevronDown, Database, FilePenLine, FileSearch, GitBranch, Layers3, Plus, Settings2, X, type LucideIcon } from 'lucide-react'
import CorpusView from './CorpusView'
import DocumentsView from './DocumentsView'
import ProjectMaterialsView from './ProjectMaterialsView'
import ProjectView from './ProjectView'
import WritingWorkspaceGateway from './WritingWorkspaceGateway'
import ModelSettingsView from './ModelSettingsView'
import { api, post, type Project } from './api'

type Section = 'facts' | 'rules' | 'corpus' | 'documents' | 'reports' | 'model'
type NavItem = { id: Section; label: string; icon: LucideIcon }

const projectNav: NavItem[] = [
  { id: 'documents', label: '项目文件', icon: FileSearch },
  { id: 'corpus', label: '项目资料', icon: Layers3 },
  { id: 'facts', label: '项目事实', icon: Database },
  { id: 'rules', label: '规则计算', icon: GitBranch },
  { id: 'reports', label: '报告写作', icon: FilePenLine },
]
const projectSections: Section[] = projectNav.map((item) => item.id)
function savedSection(project: Project): Section {
  const requested = new URLSearchParams(window.location.search).get('section') as Section | null
  if (new URLSearchParams(window.location.search).get('project') === project.id && requested && [...projectSections, 'model'].includes(requested)) return requested
  if (new URLSearchParams(window.location.search).get('report')) return 'reports'
  const stored = window.localStorage.getItem(`report-platform-section:${project.id}`) as Section | null
  return stored && projectSections.includes(stored) ? stored : project.has_corpus ? 'corpus' : 'facts'
}

export default function App() {
  const [section, setSection] = useState<Section>('facts')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  const loadProjects = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const items = await api<Project[]>('/projects')
      setProjects(items)
      const previous = window.localStorage.getItem('report-platform-project')
      const requested = new URLSearchParams(window.location.search).get('project')
      const chosen = items.find((item) => item.id === requested) || items.find((item) => item.id === previous) || items.find((item) => !item.has_corpus) || items[0]
      if (chosen) {
        setProjectId(chosen.id)
        setSection(savedSection(chosen))
      } else {
        setProjectId('')
      }
    } catch (cause) {
      setLoadError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadProjects() }, [loadProjects])
  useEffect(() => {
    const restore = () => {
      const requested = new URLSearchParams(window.location.search).get('project')
      const project = projects.find((item) => item.id === requested)
      if (!project) return
      setProjectId(project.id)
      setSection(savedSection(project))
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [projects])
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 3500)
    return () => window.clearTimeout(timer)
  }, [notice])

  const selected = projects.find((project) => project.id === projectId) || null
  const selectProject = (id: string) => {
    const project = projects.find((item) => item.id === id)
    if (!project) return
    const leaving = new Event('report-platform-before-navigate', { cancelable: true })
    window.dispatchEvent(leaving)
    if (leaving.defaultPrevented) return
    setProjectId(id)
    const url = new URL(window.location.href)
    url.searchParams.set('project', id)
    url.searchParams.delete('section')
    url.searchParams.delete('report')
    url.searchParams.delete('workspace')
    url.searchParams.delete('document')
    window.history.pushState({}, '', url)
    window.localStorage.setItem('report-platform-project', id)
    setSection(savedSection(project))
  }
  const navigate = (next: Section, documentId?: string) => {
    if (next !== section) {
      const leaving = new Event('report-platform-before-navigate', { cancelable: true })
      window.dispatchEvent(leaving)
      if (leaving.defaultPrevented) return
    }
    setSection(next)
    const url = new URL(window.location.href)
    url.searchParams.set('section', next)
    if (next === 'documents' && documentId) url.searchParams.set('document', documentId)
    else url.searchParams.delete('document')
    window.history.pushState({}, '', url)
    if (projectId && projectSections.includes(next)) window.localStorage.setItem(`report-platform-section:${projectId}`, next)
  }
  const onProjectChange = useCallback((project: Project) => setProjects((items) => items.map((item) => item.id === project.id ? project : item)), [])
  const closeCreate = () => { setCreateOpen(false); setFormError('') }
  const createProject = async () => {
    if (!projectName.trim() || creating) return
    const leaving = new Event('report-platform-before-navigate', { cancelable: true })
    window.dispatchEvent(leaving)
    if (leaving.defaultPrevented) { setCreateOpen(false); return }
    setCreating(true)
    setFormError('')
    try {
      const created = await post<Project>('/projects', { name: projectName.trim() })
      setProjects((items) => [created, ...items])
      setProjectId(created.id)
      const url = new URL(window.location.href)
      url.searchParams.set('project', created.id)
      url.searchParams.delete('section')
      url.searchParams.delete('report')
      url.searchParams.delete('workspace')
      url.searchParams.delete('document')
      window.history.pushState({}, '', url)
      window.localStorage.setItem('report-platform-project', created.id)
      setProjectName('')
      setCreateOpen(false)
      setSection('facts')
      window.localStorage.setItem(`report-platform-section:${created.id}`, 'facts')
      setNotice('项目已创建')
    } catch (cause) {
      setFormError((cause as Error).message)
    } finally {
      setCreating(false)
    }
  }
  const navButton = ({ id, label, icon: Icon }: NavItem) => <button
    key={id}
    type="button"
    className={section === id ? 'selected' : ''}
    aria-current={section === id ? 'page' : undefined}
    onClick={() => navigate(id)}
  ><Icon size={17} aria-hidden="true" /><span>{label}</span></button>

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><BookOpenCheck size={20} strokeWidth={2.3} /></span><strong>语构</strong></div>
      <div className="workspace-picker">
        <label htmlFor="project-picker">项目</label>
        <div className="picker-row"><select id="project-picker" aria-label="切换项目" value={projectId} disabled={loading || !projects.length} onChange={(event) => selectProject(event.target.value)}><option value="" disabled>选择项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}{project.is_builtin ? '（内置）' : ''}</option>)}</select><ChevronDown size={15} aria-hidden="true" /></div>
        <button type="button" onClick={() => { setCreateOpen(true); setFormError('') }}><Plus size={15} aria-hidden="true" />新建项目</button>
      </div>
      <nav aria-label="主导航">{selected ? projectNav.map(navButton) : null}</nav>
      <nav className="sidebar-secondary" aria-label="其他功能">{navButton({ id: 'model', label: '模型配置', icon: Settings2 })}</nav>
    </aside>
    <div className="main-area">
      {loadError && <div className="app-error notice error" role="alert"><span>{loadError}</span><button className="text-button" type="button" onClick={() => void loadProjects()}>重试</button></div>}
      {loading ? <main className="page"><div className="app-loading" role="status">加载中…</div></main>
        : section === 'model' ? <ModelSettingsView />
          : section === 'corpus' && selected ? selected.has_corpus
            ? <CorpusView key={selected.id} project={selected} />
            : <ProjectMaterialsView key={selected.id} project={selected} onOpenDocument={(id) => navigate('documents', id)} onOpenFacts={() => navigate('facts')} />
            : section === 'documents' && selected ? <DocumentsView key={selected.id} project={selected} notify={setNotice} />
              : section === 'reports' && selected ? <WritingWorkspaceGateway key={selected.id} project={selected} notify={setNotice} onEditFacts={() => navigate('facts')} onOpenDocuments={() => navigate('documents')} onOpenProjectFacts={() => navigate('facts')} onOpenCorpus={() => navigate('corpus')} />
                : <ProjectView project={selected} section={section === 'rules' ? 'rules' : 'facts'} onProjectChange={onProjectChange} notify={setNotice} onOpenDocuments={() => navigate('documents')} onOpenRules={() => navigate('rules')} />}
    </div>
    {notice && <div className="toast" role="status">{notice}</div>}
    {createOpen && <div className="dialog-backdrop" onClick={closeCreate}><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="create-project-title" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === 'Escape') closeCreate() }}><div className="dialog-head"><h2 id="create-project-title">新建项目</h2><button type="button" className="icon-button" onClick={closeCreate} aria-label="关闭"><X size={19} /></button></div><label className="form-field"><span>项目名称</span><input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void createProject() }} placeholder="输入项目名称" /></label>{formError && <div className="notice error" role="alert">{formError}</div>}<div className="form-actions"><button type="button" onClick={closeCreate}>取消</button><button type="button" className="primary-button" disabled={!projectName.trim() || creating} onClick={createProject}>{creating ? '创建中…' : '创建项目'}</button></div></div></div>}
  </div>
}
