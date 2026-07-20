import {
  ArrowRight,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Database,
  FileCheck2,
  FolderKanban,
  RefreshCw,
  Search,
  WalletCards,
  XCircle,
} from 'lucide-react'
import {useEffect, useState} from 'react'
import {Link} from 'react-router-dom'
import {Status} from '../components/Status'
import {adminApi} from '../lib/api'

type Project = {
  id: string
  name: string
  topic: string
  status: string
  last_run_status: string | null
  current_stage: string
  created_at: string
}

type DashboardData = {
  stats: {
    total_projects: number
    completed: number
    blocked_runs: number
    failed_runs: number
    cancelled_runs: number
    approved_facts: number
    distinct_sources: number
    total_cost_usd: number
  }
  recent_projects: Project[]
}

const empty: DashboardData = {
  stats: {
    total_projects: 0,
    completed: 0,
    blocked_runs: 0,
    failed_runs: 0,
    cancelled_runs: 0,
    approved_facts: 0,
    distinct_sources: 0,
    total_cost_usd: 0,
  },
  recent_projects: [],
}

export function Dashboard() {
  const [data, setData] = useState(empty)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [runFilter, setRunFilter] = useState('all')
  const load = () => {
    setLoading(true)
    setError('')
    adminApi<DashboardData>('/dashboard')
      .then(setData)
      .catch(reason => setError(reason.message))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const cards = [
    ['Projetos', data.stats.total_projects, FolderKanban],
    ['Artigos aprovados', data.stats.completed, FileCheck2],
    ['Bloqueios editoriais', data.stats.blocked_runs ?? 0, AlertTriangle],
    ['Falhas técnicas', data.stats.failed_runs, XCircle],
    ['Cancelados', data.stats.cancelled_runs, Ban],
    ['Fatos validados', data.stats.approved_facts, CheckCircle2],
    ['Fontes distintas', data.stats.distinct_sources, Database],
  ] as const
  const projects = data.recent_projects.filter(
    project => runFilter === 'all' || project.last_run_status === runFilter,
  )

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">CENTRAL DE CONTEÚDO</span>
          <h1>Pesquisa que você pode provar.</h1>
          <p>Acompanhe cada etapa, da pergunta inicial à sentença publicada.</p>
        </div>
        <button className="button secondary" onClick={load}>
          <RefreshCw size={16} />Atualizar
        </button>
      </div>
      <section className="metrics">
        {cards.map(([label, value, Icon]) => (
          <article className="metric" key={label}>
            <div>
              <span>{label}</span>
              <strong>{loading ? '—' : value.toLocaleString('pt-BR')}</strong>
            </div>
            <div className="metric-icon"><Icon size={21} /></div>
          </article>
        ))}
      </section>
      <section className="content-grid">
        <article className="panel projects-panel">
          <div className="panel-head">
            <div>
              <h2>Projetos recentes</h2>
              <p>Estado editorial e última execução são exibidos separadamente</p>
            </div>
            <div className="dashboard-panel-actions">
              <label>
                Último run
                <select
                  aria-label="Filtrar por último run"
                  value={runFilter}
                  onChange={event => setRunFilter(event.target.value)}
                >
                  <option value="all">Todos</option>
                  <option value="running">Em execução</option>
                  <option value="completed">Concluídos</option>
                  <option value="cancelled">Cancelados</option>
                  <option value="blocked">Bloqueios editoriais</option>
                  <option value="failed">Falhas técnicas</option>
                </select>
              </label>
              <Link to="/novo" className="text-link">
                Criar conteúdo <ArrowRight size={15} />
              </Link>
            </div>
          </div>
          {error && <div className="notice error">{error}</div>}
          {!loading && projects.length === 0 ? (
            <div className="empty">
              <div><Search size={25} /></div>
              <h3>{data.recent_projects.length ? 'Nenhum projeto neste filtro' : 'Nenhuma pesquisa iniciada'}</h3>
              <p>
                {data.recent_projects.length
                  ? 'Selecione outro estado da última execução.'
                  : 'Crie o primeiro projeto para montar um ledger de fatos rastreável.'}
              </p>
              {!data.recent_projects.length && (
                <Link className="button primary" to="/novo">Começar pesquisa</Link>
              )}
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>PROJETO</th>
                    <th>ETAPA ATUAL</th>
                    <th>ESTADO EDITORIAL</th>
                    <th>ÚLTIMO RUN</th>
                    <th>CRIADO EM</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {projects.map(project => (
                    <tr key={project.id}>
                      <td><strong>{project.name}</strong><small>{project.topic}</small></td>
                      <td className="capitalize">{project.current_stage.replaceAll('_', ' ')}</td>
                      <td><Status value={project.status} /></td>
                      <td>{project.last_run_status ? <Status value={project.last_run_status} /> : <span className="muted">Não iniciada</span>}</td>
                      <td>{new Date(project.created_at).toLocaleDateString('pt-BR')}</td>
                      <td>
                        <Link
                          to={`/projetos/${project.id}`}
                          className="row-action"
                          aria-label={`Abrir ${project.name}`}
                        >
                          <ArrowRight size={17} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
        <aside className="panel guardrail">
          <div className="guard-icon"><WalletCards size={22} /></div>
          <span className="eyebrow">CUSTO ACUMULADO</span>
          <strong>US$ {data.stats.total_cost_usd.toFixed(2)}</strong>
          <p>O gateway registra modelo, tokens, latência e fallback em cada execução.</p>
          <hr />
          <div className="rule">
            <ShieldRule />
            <span><b>Regra ativa</b>Zero afirmações sem suporte no artigo final.</span>
          </div>
        </aside>
      </section>
    </div>
  )
}

function ShieldRule() {
  return <div className="mini-shield">✓</div>
}
