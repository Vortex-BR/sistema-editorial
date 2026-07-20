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
import {useState} from 'react'
import {NavLink, Outlet, useNavigate} from 'react-router-dom'

const nav=[
  {to:'/', label:'Visão geral', icon:BarChart3},
  {to:'/novo', label:'Novo conteúdo', icon:Plus},
  {to:'/perfis', label:'Perfis editoriais', icon:Building2},
  {to:'/config', label:'Configuração', icon:Settings},
  {to:'/admin/curadoria', label:'Curadoria', icon:Sparkles},
]

export function Layout(){
  const [open,setOpen]=useState(false)
  const navigate=useNavigate()
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
          <div className="health"><span/><div><strong>Sistema operacional</strong><small>API e workers ativos</small></div></div>
          <div className="principle"><ShieldCheck size={18}/><p><strong>Evidência antes da escrita</strong><br/>Cada frase possui uma origem auditável.</p></div>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <button className="icon mobile-only" onClick={()=>setOpen(true)} aria-label="Abrir menu"><Menu/></button>
          <div className="header-title"><BrainCircuit size={20}/><span>Orquestrador multi-agente</span></div>
          <div className="header-actions">
            <span className="system-pill"><i/>Saudável</span>
            <button className="button primary compact" onClick={()=>navigate('/novo')}><Plus size={17}/>Novo projeto</button>
          </div>
        </header>
        <main><Outlet/></main>
      </div>
    </div>
  )
}
