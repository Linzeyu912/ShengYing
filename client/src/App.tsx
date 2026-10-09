import { useEffect, useMemo, useState } from 'react'
import './App.css'

type Sample = { file: string; emotion?: string; transcript?: string; url: string }
type Voice = {
  voice_id: string; name: string; collection: string; gender: string; age_group: string
  timbre_tags: string[]; description: string; review_status: string; review_notes: string
  emotions: string[]; auto_review?: { issues?: string[] }; samples: Sample[]
}
const collections = ['音色库新千问三', '音色库新VoxCPM2']
const genderLabel: Record<string, string> = { male: '男声', female: '女声', unknown: '待确认' }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `请求失败 (${response.status})`)
  return response.json()
}

function VoiceCard({ voice, onSaved }: { voice: Voice; onSaved: () => void }) {
  const [status, setStatus] = useState(voice.review_status || 'unreviewed')
  const [notes, setNotes] = useState(voice.review_notes || '')
  const [message, setMessage] = useState('')
  const badge = status === 'approved' ? '已通过' : status === 'needs_review' ? '待复核' : '未试听'

  async function saveReview() {
    setMessage('正在保存…')
    try {
      await request(`/api/voices/${encodeURIComponent(voice.voice_id)}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, notes }),
      })
      setMessage('审核已保存'); onSaved()
    } catch (error) { setMessage(error instanceof Error ? error.message : '保存失败') }
  }

  return <article className="voice-card">
    <h3>{voice.name}</h3>
    <p className="brief">{genderLabel[voice.gender] || voice.gender} · {voice.age_group || '年龄未标注'} · {(voice.timbre_tags || []).slice(0, 2).join(' · ') || '特点未标注'}</p>
    <span className={`badge ${status}`}>{badge}</span>
    {(voice.samples || []).map(sample => <div className="sample" key={sample.file}>
      <span>{sample.emotion || voice.emotions?.[0] || '参考'}</span>
      <audio controls preload="none" src={sample.url} />
    </div>)}
    <details>
      <summary>查看详情与审核</summary>
      <p className="description">{voice.description || '暂无详细描述'}</p>
      <div className="tags">{(voice.timbre_tags || []).map(tag => <span key={tag}>{tag}</span>)}</div>
      <p className="emotions">已有情绪：{(voice.emotions || []).join('、') || '未标注'}</p>
      {voice.auto_review?.issues?.length ? <p className="warning">机器初审提示：{voice.auto_review.issues.join('；')}</p> : null}
      <label>审核状态<select value={status} onChange={event => setStatus(event.target.value)}>
        <option value="unreviewed">未试听</option><option value="needs_review">待复核</option><option value="approved">已通过（人工试听）</option>
      </select></label>
      <label>审核说明<textarea value={notes} onChange={event => setNotes(event.target.value)} placeholder="问题说明：如发音错误、断句异常、音色不符、噪声等" /></label>
      <button onClick={saveReview}>保存审核</button><span className="save-message">{message}</span>
    </details>
  </article>
}

function App() {
  const [voices, setVoices] = useState<Voice[]>([])
  const [activeCollection, setActiveCollection] = useState(collections[0])
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState<'voices' | 'workspace'>('voices')

  async function loadVoices() {
    setError('')
    try { setVoices((await request<{ items: Voice[] }>('/api/voices')).items) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '无法读取音色库') }
    finally { setLoading(false) }
  }
  useEffect(() => { void loadVoices() }, [])
  const shown = useMemo(() => voices.filter(voice => voice.collection === activeCollection &&
    (!query || `${voice.name} ${voice.description} ${(voice.timbre_tags || []).join(' ')}`.toLowerCase().includes(query.toLowerCase()))), [voices, activeCollection, query])

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">声</span><div><strong>声影</strong><small>音频工作台</small></div></div>
      <div className="top-actions"><span className="connection"><i /> 本地工作台</span><button className="quiet" onClick={() => { setLoading(true); void loadVoices() }}>刷新</button></div>
    </header>
    <div className="layout">
      <aside className="sidebar"><p className="nav-caption">制作工作流</p>
        <button className={page === 'voices' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('voices')}><span>◈</span> 音色库</button>
        <button className={page === 'workspace' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('workspace')}><span>▤</span> 完整工作台</button>
        <div className="sidebar-note"><strong>逐步升级中</strong><p>音色浏览与审核已采用 React 界面。其他制作流程可从完整工作台继续使用。</p></div>
      </aside>
      <main className="main-content">
        {page === 'voices' ? <>
          <div className="page-heading"><div><p className="eyebrow">VOICE LIBRARY</p><h1>音色素材</h1><p>浏览、试听并审核可供短剧使用的角色音色。</p></div><span className="count">{shown.length} 个音色</span></div>
          <div className="collection-tabs">{collections.map(collection => <button key={collection} className={activeCollection === collection ? 'collection-tab selected' : 'collection-tab'} onClick={() => setActiveCollection(collection)}>{collection}<span>{voices.filter(voice => voice.collection === collection).length}</span></button>)}</div>
          <div className="toolbar"><label className="search"><span>⌕</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索音色名称或特点" /></label><span className="toolbar-hint">点开卡片可查看详细描述和审核项</span></div>
          {loading ? <div className="empty">正在读取音色库…</div> : error ? <div className="error">{error}</div> : shown.length ? <section className="voice-grid">{shown.map(voice => <VoiceCard key={voice.voice_id} voice={voice} onSaved={() => void loadVoices()} />)}</section> : <div className="empty">这个音色库里暂时没有匹配的音色。</div>}
        </> : <section className="legacy-panel"><div className="legacy-heading"><div><p className="eyebrow">WORKSPACE</p><h1>完整工作台</h1><p>项目、剧集、角色对白、音频生成与混音等流程。</p></div><a href="/legacy/" target="_blank" rel="noreferrer">单独打开 ↗</a></div><iframe title="原有完整工作台" src="/legacy/" /></section>}
      </main>
    </div>
  </div>
}

export default App
