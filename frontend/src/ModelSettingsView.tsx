import { useCallback, useEffect, useState } from 'react'
import { Check, Plus, RefreshCw, Settings2, Trash2, X } from 'lucide-react'
import { api, post } from './api'
import './model-settings.css'

type Protocol = 'chat' | 'vision' | 'embedding' | 'image' | 'mineru'
type ModelRecord = { id: string; name: string; model: string; endpoint: string; protocol: Protocol; auth: 'bearer' | 'none';
  has_api_key: boolean; api_key_masked: string; timeout_seconds: number; last_test: { status: string; at: string; error: string } | null }
type ModelForm = { name: string; model: string; endpoint: string; protocol: Protocol; auth: 'bearer' | 'none'; api_key: string; timeout_seconds: number }
type Settings = { models: ModelRecord[]; routes: Record<string, string>; task_labels: Record<string, string>;
  protocol_labels: Record<string, string>; presets: Omit<ModelForm, 'api_key' | 'timeout_seconds'>[];
  legacy_environment: { configured: boolean; model: string } }

const emptyForm: ModelForm = { name: '', model: '', endpoint: '', protocol: 'chat', auth: 'bearer', api_key: '', timeout_seconds: 90 }
const allowed: Record<string, Protocol[]> = {
  extraction: ['chat'], writing: ['chat'], ocr: ['mineru', 'vision'],
}
const activeTasks = ['extraction', 'writing', 'ocr']

export default function ModelSettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [editing, setEditing] = useState<string | 'new' | null>(null)
  const [form, setForm] = useState<ModelForm>(emptyForm)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const reload = useCallback(async () => { setSettings(await api<Settings>('/models')) }, [])
  useEffect(() => { void reload().catch((cause: Error) => setError(cause.message)) }, [reload])

  const startNew = (preset?: Settings['presets'][number]) => {
    setEditing('new'); setError(''); setForm({ ...emptyForm, ...preset, api_key: '' })
  }
  const startEdit = (record: ModelRecord) => {
    setEditing(record.id); setError('')
    setForm({ name: record.name, model: record.model, endpoint: record.endpoint, protocol: record.protocol,
      auth: record.auth, api_key: '', timeout_seconds: record.timeout_seconds })
  }
  const save = async () => {
    if (!editing) return
    setBusy('save'); setError('')
    try {
      const body = { ...form, api_key: form.api_key || null }
      if (editing === 'new') await post('/models', body)
      else await api(`/models/${editing}`, { method: 'PUT', body: JSON.stringify(body) })
      setEditing(null); setForm(emptyForm); await reload(); setNotice('模型配置已保存')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy('') }
  }
  const setRoute = async (task: string, modelId: string) => {
    setBusy(task); setError(''); setNotice('')
    try {
      await api(`/models/routes/${task}`, { method: 'PUT', body: JSON.stringify({ task, model_id: modelId || null }) })
      await reload(); setNotice('任务模型已更新')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy('') }
  }
  const test = async (id: string) => {
    setBusy(id); setError(''); setNotice('')
    try {
      const result = await post<{ ok: boolean; error?: string }>(`/models/${id}/test`, {})
      await reload()
      if (result.ok) setNotice('连接测试通过')
      else setError(result.error || '连接测试失败')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy('') }
  }
  const importEnvironment = async () => {
    setBusy('import'); setError(''); setNotice('')
    try { await post('/models/import-environment', {}); await reload(); setNotice('运行环境模型已导入并绑定抽取、写作') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy('') }
  }
  const remove = async (id: string) => {
    if (!window.confirm('删除这个模型配置？相关任务绑定也会解除。')) return
    setBusy(id); setError('')
    try { await api(`/models/${id}`, { method: 'DELETE' }); await reload(); setNotice('模型配置已删除') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy('') }
  }

  return <main className="page model-settings-page">
    <div className="breadcrumb">系统 / 模型配置</div>
    <div className="page-header"><div><h1>模型配置</h1></div><button className="primary-button" onClick={() => startNew()}><Plus size={15} /> 添加模型</button></div>
    {error && !editing && <div className="notice error" role="alert">{error}</div>}
    {notice && <div className="notice success" role="status">{notice}</div>}
    {!settings ? <section className="workspace-card">正在读取配置…</section> : <>
      <section className="workspace-card"><div className="workspace-toolbar"><h2>任务模型</h2></div>
        <div className="model-route-list">{Object.entries(settings.task_labels).filter(([task]) => activeTasks.includes(task)).map(([task, label]) => {
          const options = settings.models.filter((item) => allowed[task]?.includes(item.protocol))
          const env = settings.legacy_environment.configured && (task === 'extraction' || task === 'writing')
          return <label key={task} className="model-route-row"><span>{label}</span><select aria-label={`${label}模型`} value={settings.routes[task] || ''} disabled={busy === task} onChange={(event) => void setRoute(task, event.target.value)}>
            <option value="">{env ? `运行环境：${settings.legacy_environment.model}` : '未指定'}</option>
            {options.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select></label>
        })}</div>
      </section>
      <section className="workspace-card"><div className="workspace-toolbar"><h2>模型列表</h2>{settings.legacy_environment.configured && <button className="subtle-button" disabled={!!busy} onClick={() => void importEnvironment()}>{busy === 'import' ? '导入中…' : '导入 DeepSeek'}</button>}</div>
        {settings.models.length ? <div className="model-list">{settings.models.map((item) => <div className="model-row" key={item.id}>
          <div className="model-identity"><strong>{item.name}</strong><small>{item.model} · {settings.protocol_labels[item.protocol]}</small></div>
          <div className="model-health">{item.last_test?.status === 'normal' ? <span className="status status-success"><Check size={12} /> 正常</span> : item.last_test?.status === 'error' ? <span className="status status-warn">连接失败</span> : <span className="status">未测试</span>}</div>
          <div className="model-actions"><button className="subtle-button" disabled={!!busy} onClick={() => void test(item.id)}><RefreshCw size={13} />{busy === item.id ? '测试中' : '测试'}</button><button className="subtle-button" disabled={!!busy} onClick={() => startEdit(item)}><Settings2 size={13} /> 编辑</button><button className="icon-button" aria-label={`删除 ${item.name}`} disabled={!!busy} onClick={() => void remove(item.id)}><Trash2 size={14} /></button></div>
        </div>)}</div> : <div className="empty">暂无模型</div>}
      </section>
    </>}
    {editing && <div className="drawer-backdrop" onClick={() => setEditing(null)}><aside className="drawer" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><h2>{editing === 'new' ? '添加模型' : '编辑模型'}</h2><button className="icon-button" aria-label="关闭" onClick={() => setEditing(null)}><X size={19} /></button></div><div className="drawer-content"><div className="form-stack">
      {editing === 'new' && settings && <div className="model-quick-picks">{settings.presets.filter((preset) => preset.protocol === 'chat' || preset.protocol === 'mineru').map((preset) => <button type="button" key={preset.name} onClick={() => setForm({ ...emptyForm, ...preset, api_key: '' })}>{preset.name}</button>)}</div>}
      <label className="form-field"><span>名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label className="form-field"><span>请求中的 model</span><input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} /></label>
      <label className="form-field"><span>访问地址</span><input value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} /></label>
      <label className="form-field"><span>接口类型</span><select value={form.protocol} onChange={(event) => setForm({ ...form, protocol: event.target.value as Protocol })}>{Object.entries(settings?.protocol_labels || {}).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label className="form-field"><span>鉴权</span><select value={form.auth} onChange={(event) => setForm({ ...form, auth: event.target.value as 'bearer' | 'none' })}><option value="bearer">Bearer API Key</option><option value="none">无鉴权</option></select></label>
      {form.auth === 'bearer' && <label className="form-field"><span>API Key</span><input type="password" autoComplete="new-password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder={editing === 'new' ? '填写密钥' : '留空保持原密钥'} /></label>}
      <details className="model-more"><summary>高级设置</summary><label className="form-field"><span>超时（秒）</span><input type="number" min={3} max={300} value={form.timeout_seconds} onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })} /></label></details>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="form-actions"><button onClick={() => setEditing(null)}>取消</button><button className="primary-button" disabled={!!busy || !form.name.trim() || !form.model.trim() || !form.endpoint.trim() || (editing === 'new' && form.auth === 'bearer' && !form.api_key.trim())} onClick={() => void save()}>{busy ? '保存中…' : '保存'}</button></div>
    </div></div></aside></div>}
  </main>
}
