import { useEffect, useState } from 'react'
import { api, type Project } from './api'
import ReportsView from './ReportsView'
import ScenarioWorkspace from './ScenarioWorkspace'

type Mode = 'analysis' | 'legacy'

export default function WritingWorkspaceGateway({ project, notify, onEditFacts, onOpenDocuments, onOpenProjectFacts, onOpenCorpus }: {
  project: Project
  notify: (message: string) => void
  onEditFacts: () => void
  onOpenDocuments: () => void
  onOpenProjectFacts: () => void
  onOpenCorpus: () => void
}) {
  const [mode, setMode] = useState<Mode>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('workspace') === 'analysis' || params.has('report') ? 'analysis' : 'legacy'
  })
  useEffect(() => {
    let cancelled = false
    const requested = new URLSearchParams(window.location.search).get('workspace')
    if (requested === 'legacy') { setMode('legacy'); return }
    void api<unknown[]>(`/projects/${project.id}/analysis/scenarios`)
      .then(() => { if (!cancelled) setMode('analysis') })
      .catch(() => { if (!cancelled) setMode(requested === 'analysis' ? 'analysis' : 'legacy') })
    return () => { cancelled = true }
  }, [project.id])

  const switchMode = (next: Mode) => {
    const url = new URL(window.location.href)
    url.searchParams.set('workspace', next)
    if (next === 'legacy') url.searchParams.delete('report')
    window.history.pushState({}, '', url)
    setMode(next)
  }

  if (mode === 'analysis') return <ScenarioWorkspace key={project.id} project={project} notify={notify}
    onOpenFacts={onEditFacts} onOpenCorpus={onOpenCorpus} onLegacy={() => switchMode('legacy')} />
  return <div className="writing-gateway"><div className="writing-gateway-switch">
    <button type="button" onClick={() => switchMode('analysis')}>方案推演写作</button>
  </div><ReportsView key={project.id} project={project} notify={notify} onEditFacts={onEditFacts}
    onOpenDocuments={onOpenDocuments} onOpenProjectFacts={onOpenProjectFacts} /></div>
}
