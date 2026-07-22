import {
  BarChart3,
  BookOpenCheck,
  BrainCircuit,
  Building2,
  Menu,
  Plus,
  Settings,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import {useEffect, useMemo, useState} from 'react'
import {NavLink, Outlet, useNavigate} from 'react-router-dom'
import {getReadiness, readinessBlockers, type ReadinessReport} from '../lib/api'

const nav=[
  {to:'/', label:'Visão geral', icon:BarChart3},
  {to:'/novo', label:'Novo conteúdo', icon:Plus},
  {to:'/perfis', label:'Perfis editoriais', icon:Building2},
  {to:'/config', label:'Configuração', icon:Settings},
  {to:'/admin/curadoria', label:'Curadoria', icon:Sparkles},
]

const READINESS_REFRESH_MS=30_000

type SystemTone='checking'|'ready'|'not-ready'|'unavailable'

function systemPresentation(report:ReadinessReport|null):{
  tone:SystemTone
  label:string
  title:string
  detail:string
}{
  if(!report){
    return {
      tone:'checking',
      label:'Verificando',
      title:'Consultando a prontidão operacional',
      detail:'Consultando API, worker e agendador',
    }
  }
  if(report.status==='ready'){
    return {
      tone:'ready',
      label:'Operacional',
      title:'Sistema pronto para iniciar novas execuções',
      detail:'API, worker e agendador prontos',
    }
  }
  const blockers=readinessBlockers(report)
  const detail=blockers.slice(0,2).join(' · ')||'Prontidão operacional incompleta'
  if(report.status==='unavailable'){
    return {
      tone:'unavailable',
      label:'Indisponível',
      title:detail,
      detail,
    }
  }
  return {
    tone:'not-ready',
    label:'Atenção',
    title:blockers.join(' · ')||detail,
    detail,
  }
}

export function Layout(){
  const [open,setOpen]=useState(false)
  const [readiness,setReadiness]=useState<ReadinessReport|null>(null)
  const navigate=useNavigate()

  useEffect(()=>{
    let active=true
    const refresh=async()=>{
      const report=await getReadiness()
      if(active) setReadiness(report)
    }
    void refresh()
    const interval=window.setInterval(()=>void refresh(),READINESS_REFRESH_MS)
    return ()=>{
      active=false
      window.clearInterval(interval)
    }
  },[])

  const system=useMemo(()=>systemPresentation(readiness),[readiness])

  return (
    <div className="shell">
      <aside className={`sidebar ${open?'open':''}`}>
        <div className="brand">
          <div className="brand-mark"><BookOpenCheck size={20}/></div>
          <div><strong>Evidence</strong><span>SEO Research Ledger</span></div>
          <button className="icon mobile-only" onClick={()=>setOpen(false)} aria-label="Fechar menu"><X/></button>
        </div>
        <nav>
          {nav.map(({to,label,icon:Icon})=>(
            <NavLink key={to} to={to} end={to==='/'}
              onClick={()=>setOpen(false)}>
              <Icon size={19}/>{label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className={`health health-${system.tone}`} title={system.title}>
            <span/>
            <div><strong>{system.label}</strong><small>{system.detail}</small></div>
          </div>
          <div className="principle"><ShieldCheck size={18}/><p><strong>Evidência antes da escrita</strong><br/>Cada frase possui uma origem auditável.</p></div>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <button className="icon mobile-only" onClick={()=>setOpen(true)} aria-label="Abrir menu"><Menu/></button>
          <div className="header-title"><BrainCircuit size={20}/><span>Orquestrador multi-agente</span></div>
          <div className="header-actions">
            <span
              className={`system-pill system-pill-${system.tone}`}
              title={system.title}
              aria-live="polite"
            ><i/>{system.label}</span>
            <button className="button primary compact" onClick={()=>navigate('/novo')}><Plus size={17}/>Novo projeto</button>
          </div>
        </header>
        <main><Outlet/></main>
      </div>
    </div>
  )
}
