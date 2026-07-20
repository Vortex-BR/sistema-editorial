import {
  Archive,
  BrainCircuit,
  Check,
  Eye,
  FileSearch,
  Library,
  Palette,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import {FormEvent, ReactNode, useCallback, useEffect, useState} from 'react'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {Status} from '../components/Status'
import {adminApi, clearAdminToken} from '../lib/api'

type LearningStatus='quarantine'|'approved'|'rejected'|'archived'
type Decision='approved'|'rejected'|'archived'
type Project={id:string;name:string}
type Memory={
  id:string;agent_role:string;project_id:string|null;niche:string|null;kind:string;content:string
  confidence:number;status:LearningStatus;source_type:string;source_id:string|null
  origin_pipeline_run_id:string|null;created_at:string
}
type StyleSource={
  id:string;project_id:string|null;url:string;title:string|null;publisher:string|null;domain:string
  status:LearningStatus;excerpts:string[];origin_pipeline_run_id:string|null;created_at:string
}
type StylePattern={
  id:string;project_id:string|null;target_agent_role:string;niche:string|null;pattern_type:string
  description:string;source_ids:string[];independent_domain_count:number;validation_count:number
  status:LearningStatus;origin_pipeline_run_id:string|null;approved_at:string|null;created_at:string
}
type SuperiorSkill={
  id:string;skill_id:string;scope:string;agent_role:string|null;enabled:boolean;current_version:string
}
type SkillVersion={
  id:string;version:string;status:string;checksum:string;definition:Record<string,unknown>
  reviewed_by_human:boolean;approved_at:string|null;created_by:string;created_at:string
}
type Preview={
  mode:string;metadata:{versions?:Record<string,string>;memory_ids?:string[];style_pattern_ids?:string[];handoff_id?:string|null;pipeline_run_id?:string|null;status?:string}
  preview:string;compiled_context:string
}
type ProjectDetail={pipeline_runs:{id:string;status:string;current_stage:string}[]}
type Tab='memories'|'patterns'|'sources'|'skills'|'preview'

const roles=[
  ['planner','Planner'],['researcher','Researcher'],['research_gatekeeper','Research gatekeeper'],
  ['writer','Writer'],['editor','Editor'],['development_editor','Development editor'],
  ['fact_checker','Fact checker'],['language_editor','Language editor'],['skill_curator','Skill curator'],
] as const
const statuses:[LearningStatus,string][]=[
  ['quarantine','Pendente'],['approved','Aprovado'],['rejected','Rejeitado'],['archived','Arquivado'],
]
const tabs:{id:Tab;label:string;icon:typeof BrainCircuit}[]=[
  {id:'memories',label:'Memórias',icon:BrainCircuit},
  {id:'patterns',label:'Padrões de estilo',icon:Palette},
  {id:'sources',label:'Fontes de estilo',icon:Library},
  {id:'skills',label:'Super-skills',icon:Sparkles},
  {id:'preview',label:'Preview de contexto',icon:Eye},
]

function queryPath(path:string, values:Record<string,string>){
  const query=new URLSearchParams()
  Object.entries(values).forEach(([key,value])=>{if(value) query.set(key,value)})
  const encoded=query.toString()
  return encoded?`${path}?${encoded}`:path
}
function projectName(projects:Project[],id:string|null){
  if(!id) return 'Global'
  return projects.find(project=>project.id===id)?.name||id
}
function formatDate(value:string|null|undefined){
  return value?new Date(value).toLocaleString('pt-BR'):'—'
}

type PendingAction={id:string;decision:Decision;subject:string}
function decisionCopy(decision:Decision){
  if(decision==='approved') return {verb:'Aprovar',description:'O item poderá ser usado nas próximas execuções compatíveis.',danger:false}
  if(decision==='rejected') return {verb:'Rejeitar',description:'O item será marcado como rejeitado e não entrará no contexto aprovado.',danger:true}
  return {verb:'Arquivar',description:'O item será retirado da curadoria ativa e mantido apenas para auditoria.',danger:true}
}
function DecisionButtons({item,busy,onChoose}:{item:{id:string;status:LearningStatus};busy:boolean;onChoose:(action:PendingAction)=>void}){
  return <div className="curation-actions">
    {item.status!=='approved'&&<button className="button success compact" disabled={busy} onClick={()=>onChoose({id:item.id,decision:'approved',subject:'este item'})}><Check size={15}/>Aprovar</button>}
    {item.status!=='rejected'&&<button className="button secondary compact" disabled={busy} onClick={()=>onChoose({id:item.id,decision:'rejected',subject:'este item'})}><X size={15}/>Rejeitar</button>}
    {item.status!=='archived'&&<button className="button ghost compact" disabled={busy} onClick={()=>onChoose({id:item.id,decision:'archived',subject:'este item'})}><Archive size={15}/>Arquivar</button>}
  </div>
}
function Feedback({error,success}:{error:string;success:string}){
  return <>{error&&<div className="notice error" role="alert">{error}</div>}{success&&<div className="notice success" role="status">{success}</div>}</>
}
function Empty({children}:{children:ReactNode}){
  return <div className="empty small"><FileSearch size={24}/><h3>Nenhum item encontrado</h3><p>{children}</p></div>
}
function Filters({children,onSubmit}:{children:ReactNode;onSubmit:(event:FormEvent)=>void}){
  return <form className="curation-filters" onSubmit={onSubmit}>{children}<button className="button secondary compact"><RefreshCw size={15}/>Aplicar filtros</button></form>
}
function StatusFilter({value,onChange}:{value:string;onChange:(value:string)=>void}){
  return <label>Status<select value={value} onChange={event=>onChange(event.target.value)}><option value="">Todos</option>{statuses.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
}
function RoleFilter({value,onChange}:{value:string;onChange:(value:string)=>void}){
  return <label>Papel<select value={value} onChange={event=>onChange(event.target.value)}><option value="">Todos</option>{roles.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
}
function ProjectFilter({projects,value,onChange}:{projects:Project[];value:string;onChange:(value:string)=>void}){
  return <label>Projeto<select value={value} onChange={event=>onChange(event.target.value)}><option value="">Todos</option>{projects.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
}

function MemoriesTab({projects}:{projects:Project[]}){
  const [items,setItems]=useState<Memory[]>([])
  const [filters,setFilters]=useState({memory_status:'quarantine',agent_role:'',project_id:''})
  const [applied,setApplied]=useState(filters)
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState(false)
  const [pending,setPending]=useState<PendingAction|null>(null)
  const [error,setError]=useState('')
  const [success,setSuccess]=useState('')
  const load=useCallback(async()=>{
    setLoading(true);setError('')
    try{setItems(await adminApi<Memory[]>(queryPath('/admin/memories',applied)))}
    catch(reason){setError((reason as Error).message)}finally{setLoading(false)}
  },[applied])
  useEffect(()=>{void load()},[load])
  async function decide(){
    if(!pending||busy) return
    setBusy(true);setError('');setSuccess('')
    try{
      await adminApi(`/admin/memories/${pending.id}/decision`,{method:'POST',body:JSON.stringify({decision:pending.decision})})
      setSuccess(`Memória ${pending.decision==='approved'?'aprovada':pending.decision==='rejected'?'rejeitada':'arquivada'} com sucesso.`)
      setPending(null);await load()
    }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
  }
  const copy=pending?decisionCopy(pending.decision):null
  return <section id="curation-memories" role="tabpanel" aria-labelledby="tab-memories" className="panel curation-panel">
    <div className="panel-head"><div><h2>Memórias dos agentes</h2><p>Revise somente memórias aprováveis do papel e projeto corretos.</p></div></div>
    <Filters onSubmit={event=>{event.preventDefault();setApplied({...filters})}}>
      <StatusFilter value={filters.memory_status} onChange={value=>setFilters(current=>({...current,memory_status:value}))}/>
      <RoleFilter value={filters.agent_role} onChange={value=>setFilters(current=>({...current,agent_role:value}))}/>
      <ProjectFilter projects={projects} value={filters.project_id} onChange={value=>setFilters(current=>({...current,project_id:value}))}/>
    </Filters>
    <Feedback error={error} success={success}/>
    {loading?<div className="loading">Carregando memórias...</div>:items.length===0?<Empty>Não há memórias com os filtros selecionados.</Empty>:<div className="table-wrap"><table className="curation-table"><thead><tr><th>MEMÓRIA</th><th>ORIGEM</th><th>STATUS</th><th>AÇÕES</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><strong>{item.kind} · {item.agent_role.replaceAll('_',' ')}</strong><p className="curation-content">{item.content}</p><small>Confiança: {Math.round(item.confidence*100)}% · {formatDate(item.created_at)}</small></td><td><strong>{projectName(projects,item.project_id)}</strong><small>{item.source_type}{item.source_id?` · ${item.source_id}`:''}</small></td><td><Status value={item.status}/></td><td><DecisionButtons item={item} busy={busy} onChoose={setPending}/></td></tr>)}</tbody></table></div>}
    <ConfirmDialog open={Boolean(pending)} title={`${copy?.verb||'Confirmar'} memória`} description={copy?.description||''} confirmLabel={copy?.verb||'Confirmar'} danger={copy?.danger} busy={busy} onCancel={()=>setPending(null)} onConfirm={decide}/>
  </section>
}

function PatternsTab({projects}:{projects:Project[]}){
  const [items,setItems]=useState<StylePattern[]>([])
  const [filters,setFilters]=useState({pattern_status:'quarantine',target_agent_role:'',project_id:''})
  const [applied,setApplied]=useState(filters)
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState(false)
  const [pending,setPending]=useState<PendingAction|null>(null)
  const [error,setError]=useState('')
  const [success,setSuccess]=useState('')
  const load=useCallback(async()=>{
    setLoading(true);setError('')
    try{setItems(await adminApi<StylePattern[]>(queryPath('/admin/style-patterns',applied)))}
    catch(reason){setError((reason as Error).message)}finally{setLoading(false)}
  },[applied])
  useEffect(()=>{void load()},[load])
  async function decide(){
    if(!pending||busy) return
    setBusy(true);setError('');setSuccess('')
    try{
      await adminApi(`/admin/style-patterns/${pending.id}/decision`,{method:'POST',body:JSON.stringify({decision:pending.decision})})
      setSuccess('Decisão registrada no padrão de estilo.');setPending(null);await load()
    }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
  }
  const copy=pending?decisionCopy(pending.decision):null
  return <section id="curation-patterns" role="tabpanel" aria-labelledby="tab-patterns" className="panel curation-panel">
    <div className="panel-head"><div><h2>Padrões de estilo</h2><p>A aprovação exige a evidência mínima já validada pelo backend.</p></div></div>
    <Filters onSubmit={event=>{event.preventDefault();setApplied({...filters})}}>
      <StatusFilter value={filters.pattern_status} onChange={value=>setFilters(current=>({...current,pattern_status:value}))}/>
      <RoleFilter value={filters.target_agent_role} onChange={value=>setFilters(current=>({...current,target_agent_role:value}))}/>
      <ProjectFilter projects={projects} value={filters.project_id} onChange={value=>setFilters(current=>({...current,project_id:value}))}/>
    </Filters>
    <Feedback error={error} success={success}/>
    {loading?<div className="loading">Carregando padrões...</div>:items.length===0?<Empty>Não há padrões com os filtros selecionados.</Empty>:<div className="table-wrap"><table className="curation-table"><thead><tr><th>PADRÃO</th><th>EVIDÊNCIA</th><th>STATUS</th><th>AÇÕES</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><strong>{item.pattern_type} · {item.target_agent_role.replaceAll('_',' ')}</strong><p className="curation-content">{item.description}</p><small>{projectName(projects,item.project_id)} · {formatDate(item.created_at)}</small></td><td><strong>{item.independent_domain_count} domínios</strong><small>{item.validation_count} validações · {item.source_ids.length} fontes</small></td><td><Status value={item.status}/></td><td><DecisionButtons item={item} busy={busy} onChoose={setPending}/></td></tr>)}</tbody></table></div>}
    <ConfirmDialog open={Boolean(pending)} title={`${copy?.verb||'Confirmar'} padrão`} description={copy?.description||''} confirmLabel={copy?.verb||'Confirmar'} danger={copy?.danger} busy={busy} onCancel={()=>setPending(null)} onConfirm={decide}/>
  </section>
}

function SourcesTab({projects}:{projects:Project[]}){
  const [items,setItems]=useState<StyleSource[]>([])
  const [filters,setFilters]=useState({source_status:'quarantine',project_id:''})
  const [applied,setApplied]=useState(filters)
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState(false)
  const [pending,setPending]=useState<PendingAction|null>(null)
  const [error,setError]=useState('')
  const [success,setSuccess]=useState('')
  const load=useCallback(async()=>{
    setLoading(true);setError('')
    try{setItems(await adminApi<StyleSource[]>(queryPath('/admin/style-sources',applied)))}
    catch(reason){setError((reason as Error).message)}finally{setLoading(false)}
  },[applied])
  useEffect(()=>{void load()},[load])
  async function decide(){
    if(!pending||busy) return
    setBusy(true);setError('');setSuccess('')
    try{
      await adminApi(`/admin/style-sources/${pending.id}/decision`,{method:'POST',body:JSON.stringify({decision:pending.decision})})
      setSuccess('Decisão registrada na fonte de estilo.');setPending(null);await load()
    }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
  }
  const copy=pending?decisionCopy(pending.decision):null
  return <section id="curation-sources" role="tabpanel" aria-labelledby="tab-sources" className="panel curation-panel">
    <div className="panel-head"><div><h2>Fontes de estilo</h2><p>Somente trechos de evidência são exibidos; o conteúdo bruto não é retornado.</p></div></div>
    <Filters onSubmit={event=>{event.preventDefault();setApplied({...filters})}}>
      <StatusFilter value={filters.source_status} onChange={value=>setFilters(current=>({...current,source_status:value}))}/>
      <ProjectFilter projects={projects} value={filters.project_id} onChange={value=>setFilters(current=>({...current,project_id:value}))}/>
    </Filters>
    <Feedback error={error} success={success}/>
    {loading?<div className="loading">Carregando fontes...</div>:items.length===0?<Empty>Não há fontes com os filtros selecionados.</Empty>:<div className="table-wrap"><table className="curation-table"><thead><tr><th>FONTE</th><th>EVIDÊNCIA SEGURA</th><th>STATUS</th><th>AÇÕES</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><a className="text-link" href={item.url} target="_blank" rel="noreferrer">{item.title||item.domain}</a><small>{item.publisher||item.domain} · {projectName(projects,item.project_id)}<br/>{formatDate(item.created_at)}</small></td><td><details><summary>{item.excerpts.length} trecho(s)</summary><div className="excerpt-list">{item.excerpts.map((excerpt,index)=><p key={index}>{excerpt}</p>)}</div></details></td><td><Status value={item.status}/></td><td><DecisionButtons item={item} busy={busy} onChoose={setPending}/></td></tr>)}</tbody></table></div>}
    <ConfirmDialog open={Boolean(pending)} title={`${copy?.verb||'Confirmar'} fonte`} description={copy?.description||''} confirmLabel={copy?.verb||'Confirmar'} danger={copy?.danger} busy={busy} onCancel={()=>setPending(null)} onConfirm={decide}/>
  </section>
}

function SkillsTab(){
  const [skills,setSkills]=useState<SuperiorSkill[]>([])
  const [selected,setSelected]=useState('')
  const [versions,setVersions]=useState<SkillVersion[]>([])
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState(false)
  const [pending,setPending]=useState<SkillVersion|null>(null)
  const [error,setError]=useState('')
  const [success,setSuccess]=useState('')
  const loadSkills=useCallback(async()=>{
    setLoading(true);setError('')
    try{
      const rows=await adminApi<SuperiorSkill[]>('/admin/superior-skills')
      setSkills(rows);setSelected(current=>current||rows[0]?.skill_id||'')
    }catch(reason){setError((reason as Error).message)}finally{setLoading(false)}
  },[])
  const loadVersions=useCallback(async()=>{
    if(!selected){setVersions([]);return}
    setLoading(true);setError('')
    try{setVersions(await adminApi<SkillVersion[]>(`/admin/superior-skills/${encodeURIComponent(selected)}/versions`))}
    catch(reason){setError((reason as Error).message)}finally{setLoading(false)}
  },[selected])
  useEffect(()=>{void loadSkills()},[loadSkills])
  useEffect(()=>{void loadVersions()},[loadVersions])
  async function activate(){
    if(!pending||busy) return
    setBusy(true);setError('');setSuccess('')
    try{
      await adminApi(`/admin/superior-skills/${encodeURIComponent(selected)}/versions/${encodeURIComponent(pending.version)}/activate`,{method:'POST'})
      setSuccess(`Versão ${pending.version} ativada. Ela será usada nas próximas execuções.`)
      setPending(null);await loadSkills();await loadVersions()
    }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
  }
  const current=skills.find(skill=>skill.skill_id===selected)
  return <section id="curation-skills" role="tabpanel" aria-labelledby="tab-skills" className="panel curation-panel">
    <div className="panel-head"><div><h2>Versões de super-skills</h2><p>Conteúdo somente leitura. Ativar uma versão muda o contexto das próximas execuções.</p></div><button className="button secondary compact" onClick={()=>void loadVersions()}><RefreshCw size={15}/>Atualizar</button></div>
    <div className="skill-selector"><label>Skill<select value={selected} onChange={event=>setSelected(event.target.value)}>{skills.map(skill=><option key={skill.id} value={skill.skill_id}>{skill.skill_id} · {skill.agent_role||'global'}</option>)}</select></label>{current&&<div><span>Versão ativa</span><strong>{current.current_version}</strong><Status value={current.enabled?'active':'archived'}/></div>}</div>
    <Feedback error={error} success={success}/>
    {loading?<div className="loading">Carregando versões...</div>:versions.length===0?<Empty>Nenhuma versão está disponível para esta skill.</Empty>:<div className="version-grid">{versions.map(version=><article className="version-card" key={version.id}><div className="version-head"><div><strong>Versão {version.version}</strong><small>Criada por {version.created_by} · {formatDate(version.created_at)}</small></div><Status value={version.status}/></div><dl><div><dt>Revisão humana</dt><dd>{version.reviewed_by_human?'Sim':'Não'}</dd></div><div><dt>Checksum</dt><dd title={version.checksum}>{version.checksum.slice(0,12)}…</dd></div></dl><details><summary>Abrir conteúdo somente leitura</summary><pre>{JSON.stringify(version.definition,null,2)}</pre></details>{version.status!=='active'&&<button className="button primary compact" disabled={busy} onClick={()=>setPending(version)}><ShieldCheck size={15}/>Ativar versão</button>}</article>)}</div>}
    <ConfirmDialog open={Boolean(pending)} title={`Ativar versão ${pending?.version||''}?`} description="A versão selecionada passará a compor o contexto das próximas execuções deste papel. A versão ativa anterior será marcada como substituída." confirmLabel="Ativar versão" busy={busy} onCancel={()=>setPending(null)} onConfirm={activate}/>
  </section>
}

function PreviewTab({projects}:{projects:Project[]}){
  const [role,setRole]=useState('researcher')
  const [projectId,setProjectId]=useState('')
  const [pipelineRunId,setPipelineRunId]=useState('')
  const [runs,setRuns]=useState<ProjectDetail['pipeline_runs']>([])
  const [task,setTask]=useState('Revise o contexto que seria usado na próxima execução.')
  const [result,setResult]=useState<Preview|null>(null)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  useEffect(()=>{
    if(!projectId){setRuns([]);setPipelineRunId('');return}
    adminApi<ProjectDetail>(`/projects/${projectId}`).then(data=>setRuns(data.pipeline_runs)).catch(reason=>setError((reason as Error).message))
  },[projectId])
  async function preview(event:FormEvent){
    event.preventDefault()
    if(!projectId||!task.trim()) return
    setLoading(true);setError('');setResult(null)
    try{
      setResult(await adminApi<Preview>('/admin/agent-context/preview',{
        method:'POST',
        body:JSON.stringify({
          agent_role:role,
          project_id:projectId,
          pipeline_run_id:pipelineRunId||null,
          task,
        }),
      }))
    }catch(reason){setError((reason as Error).message)}finally{setLoading(false)}
  }
  return <section id="curation-preview" role="tabpanel" aria-labelledby="tab-preview" className="panel curation-panel">
    <div className="panel-head"><div><h2>Preview de contexto</h2><p>Compõe o contexto sem chamar LLM, busca ou embeddings externos.</p></div></div>
    <form className="preview-form" onSubmit={preview}>
      <div className="preview-fields"><label>Papel<select value={role} onChange={event=>setRole(event.target.value)}>{roles.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label>Projeto<select required value={projectId} onChange={event=>setProjectId(event.target.value)}><option value="">Selecione</option>{projects.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label>Pipeline run (opcional)<select value={pipelineRunId} onChange={event=>setPipelineRunId(event.target.value)}><option value="">Sem handoff específico</option>{runs.map(run=><option key={run.id} value={run.id}>{run.current_stage} · {run.status} · {run.id.slice(0,8)}</option>)}</select></label></div>
      <label>Instrução da tarefa<textarea required rows={4} value={task} onChange={event=>setTask(event.target.value)}/></label>
      <button className="button primary" disabled={loading||!projectId||!task.trim()}><Eye size={16}/>{loading?'Compondo...':'Visualizar contexto'}</button>
    </form>
    <Feedback error={error} success=""/>
    {result&&<div className="preview-result"><div className="preview-metrics"><div><span>Modo efetivo</span><Status value={result.mode}/></div><div><span>Tamanho aproximado</span><strong>{result.preview.length.toLocaleString('pt-BR')} caracteres · ~{Math.ceil(result.preview.length/4).toLocaleString('pt-BR')} tokens</strong></div><div><span>Memórias incluídas</span><strong>{result.metadata.memory_ids?.length||0}</strong></div><div><span>Padrões incluídos</span><strong>{result.metadata.style_pattern_ids?.length||0}</strong></div></div><dl className="preview-metadata"><div><dt>Versões</dt><dd>{JSON.stringify(result.metadata.versions||{})}</dd></div><div><dt>Handoff</dt><dd>{result.metadata.handoff_id||'Nenhum'}</dd></div><div><dt>Pipeline run</dt><dd>{result.metadata.pipeline_run_id||'Não selecionado'}</dd></div></dl><label>Prompt que seria enviado ao provider<textarea readOnly rows={18} value={result.preview}/></label>{result.compiled_context!==result.preview&&<details><summary>Contexto superior compilado para auditoria (shadow)</summary><pre>{result.compiled_context}</pre></details>}</div>}
  </section>
}

export function AdminCuration(){
  const [tab,setTab]=useState<Tab>('memories')
  const [projects,setProjects]=useState<Project[]>([])
  const [projectError,setProjectError]=useState('')
  useEffect(()=>{
    adminApi<Project[]>('/projects').then(setProjects).catch(reason=>setProjectError((reason as Error).message))
    return clearAdminToken
  },[])
  return <div className="page admin-curation">
    <div className="page-heading"><div><span className="eyebrow">CURADORIA ADMINISTRATIVA</span><h1>Conhecimento sob revisão humana.</h1><p>Aprove memórias, evidências de estilo e versões que poderão orientar as próximas execuções.</p></div><span className="secure-pill"><ShieldCheck size={15}/>Token somente em memória</span></div>
    {projectError&&<div className="notice error" role="alert">{projectError}</div>}
    <div className="curation-tabs" role="tablist" aria-label="Áreas de curadoria">{tabs.map(({id,label,icon:Icon})=><button key={id} id={`tab-${id}`} role="tab" aria-selected={tab===id} aria-controls={`curation-${id}`} className={tab===id?'active':''} onClick={()=>setTab(id)}><Icon size={17}/>{label}</button>)}</div>
    {tab==='memories'&&<MemoriesTab projects={projects}/>}
    {tab==='patterns'&&<PatternsTab projects={projects}/>}
    {tab==='sources'&&<SourcesTab projects={projects}/>}
    {tab==='skills'&&<SkillsTab/>}
    {tab==='preview'&&<PreviewTab projects={projects}/>}
  </div>
}
