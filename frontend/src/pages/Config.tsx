import {
  Check,
  KeyRound,
  LockKeyhole,
  RefreshCw,
  Route,
  Save,
  Shield,
  SlidersHorizontal,
} from 'lucide-react'
import {FormEvent, useEffect, useState} from 'react'
import {adminApi, clearAdminToken} from '../lib/api'

type Credential = {
  provider:string
  configured:boolean
  last_four:string|null
  verified_at:string|null
}
type CredentialVerification = {
  provider:string
  verified:boolean
  verified_at:string|null
  latency_ms:number
  model:string|null
  error_code:string|null
}
type RouteData = {
  agent_role:string
  primary_provider:string
  primary_model:string
  fallback_provider:string|null
  fallback_model:string|null
  parameters?:Record<string,string|number>
}
type ExecutionPreflight = {
  pipeline_version:string
  status:'ready'|'not_ready'
  dependencies:string[]
  repairs:string[]
}
type ConfigData = {
  routes:RouteData[]
  route_defaults?:Record<string,Record<string,RouteData>>
  skills:{skill_id:string;kind:string;version:string;enabled:boolean;stable:boolean;niche:string|null}[]
  policy:{learned_skill_stability_threshold:number;auto_inject_unstable_skills:boolean}
}

const providers = [
  ['openai', 'OpenAI'],
  ['anthropic', 'Anthropic'],
  ['gemini', 'Google Gemini'],
  ['tavily', 'Tavily Search'],
  ['serper', 'Serper Search'],
] as const
const verifiableProviders = new Set(['openai', 'anthropic', 'gemini', 'tavily', 'serper'])
const defaults:RouteData[] = [
  {agent_role:'planner',primary_provider:'openai',primary_model:'gpt-5-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'low',max_output_tokens:4096,timeout_seconds:120,max_retries:1,input_cost_per_million:0.25,output_cost_per_million:2}},
  {agent_role:'researcher',primary_provider:'openai',primary_model:'gpt-5-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'low',max_output_tokens:4096,timeout_seconds:120,max_retries:1,input_cost_per_million:0.25,output_cost_per_million:2}},
  {agent_role:'research_gatekeeper',primary_provider:'openai',primary_model:'gpt-5.4-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'medium',max_output_tokens:4096,timeout_seconds:150,max_retries:1,input_cost_per_million:0.75,output_cost_per_million:4.5}},
  {agent_role:'writer',primary_provider:'openai',primary_model:'gpt-5.4',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'low',max_output_tokens:24000,timeout_seconds:240,max_retries:1,input_cost_per_million:2.5,output_cost_per_million:15}},
  {agent_role:'editor',primary_provider:'openai',primary_model:'gpt-5.4-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'medium',max_output_tokens:8192,timeout_seconds:180,max_retries:1,input_cost_per_million:0.75,output_cost_per_million:4.5}},
  {agent_role:'development_editor',primary_provider:'openai',primary_model:'gpt-5.4-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'medium',max_output_tokens:12000,timeout_seconds:180,max_retries:1,input_cost_per_million:0.75,output_cost_per_million:4.5}},
  {agent_role:'fact_checker',primary_provider:'openai',primary_model:'gpt-5.4',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'high',max_output_tokens:12000,timeout_seconds:210,max_retries:1,input_cost_per_million:2.5,output_cost_per_million:15}},
  {agent_role:'language_editor',primary_provider:'openai',primary_model:'gpt-5.4-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'medium',max_output_tokens:12000,timeout_seconds:180,max_retries:1,input_cost_per_million:0.75,output_cost_per_million:4.5}},
  {agent_role:'skill_curator',primary_provider:'openai',primary_model:'gpt-5-mini',fallback_provider:null,fallback_model:null,parameters:{reasoning_effort:'low',max_output_tokens:2048,timeout_seconds:90,max_retries:0,input_cost_per_million:0.25,output_cost_per_million:2}},
]

function verifiedLabel(value:string|null):string{
  if(!value) return 'Ainda não verificada'
  return `Verificada em ${new Intl.DateTimeFormat('pt-BR',{
    dateStyle:'short',
    timeStyle:'short',
  }).format(new Date(value))}`
}

export function Config(){
  const [credentials,setCredentials]=useState<Credential[]>([])
  const [config,setConfig]=useState<ConfigData|null>(null)
  const [keys,setKeys]=useState<Record<string,string>>({})
  const [routes,setRoutes]=useState<Record<string,RouteData>>({})
  const [verifying,setVerifying]=useState('')
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const [executionPreflight,setExecutionPreflight]=useState<ExecutionPreflight|null>(null)
  const [repairingDependencies,setRepairingDependencies]=useState(false)
  const [preflightPipeline,setPreflightPipeline]=useState<'v2'|'v3'>('v3')

  async function load(){
    const [savedCredentials,savedConfig,preflight]=await Promise.all([
      adminApi<Credential[]>('/config/credentials'),
      adminApi<ConfigData>('/config'),
      adminApi<ExecutionPreflight>(`/config/execution-preflight?pipeline_version=${preflightPipeline}`),
    ])
    setCredentials(savedCredentials)
    setConfig(savedConfig)
    setExecutionPreflight(preflight)
    setRoutes(Object.fromEntries(defaults.map(route=>{
      const saved=savedConfig.routes.find(item=>item.agent_role===route.agent_role)
      const serverDefault=savedConfig.route_defaults?.openai?.[route.agent_role]
      return [route.agent_role,saved||serverDefault||route]
    })))
  }

  useEffect(()=>{
    load().catch(reason=>setError((reason as Error).message))
    return clearAdminToken
  },[])

  async function repairExecutionDependencies(){
    setError('')
    setNotice('')
    setRepairingDependencies(true)
    try{
      const result=await adminApi<ExecutionPreflight>(
        `/config/execution-preflight?pipeline_version=${preflightPipeline}&repair=true`,
      )
      setExecutionPreflight(result)
      await load()
      if(result.status==='ready'){
        const repaired=result.repairs.length?` Rotas reparadas: ${result.repairs.join(', ')}.`:''
        setNotice(`Dependências do Editorial ${preflightPipeline.toUpperCase()} prontas.${repaired}`)
      }else{
        setError(`Dependências ainda incompletas: ${result.dependencies.join(', ')}`)
      }
    }catch(reason){
      setError((reason as Error).message)
    }finally{
      setRepairingDependencies(false)
    }
  }

  async function saveKey(event:FormEvent,provider:string){
    event.preventDefault()
    if(!keys[provider]) return
    setError('')
    setNotice('')
    try{
      await adminApi(`/config/credentials/${provider}`,{
        method:'PUT',
        body:JSON.stringify({provider,value:keys[provider]}),
      })
      setKeys(current=>({...current,[provider]:''}))
      await load()
      setNotice('Credencial salva. Execute a verificação antes de iniciar um conteúdo.')
    }catch(reason){
      setError((reason as Error).message)
    }
  }

  async function verifyCredential(provider:string){
    setError('')
    setNotice('')
    setVerifying(provider)
    try{
      const result=await adminApi<CredentialVerification>(
        `/config/credentials/${provider}/verify`,
        {method:'POST'},
      )
      await load()
      if(!result.verified){
        setError(`A verificação falhou (${result.error_code||'erro desconhecido'}).`)
        return
      }
      const model=result.model?` usando ${result.model}`:''
      setNotice(`Credencial ${provider} verificada${model} em ${result.latency_ms} ms.`)
    }catch(reason){
      setError((reason as Error).message)
    }finally{
      setVerifying('')
    }
  }

  function updateRoute(role:string,field:keyof RouteData,value:string){
    setRoutes(current=>({...current,[role]:{...current[role],[field]:value}}))
  }

  function updateProvider(role:string,provider:string){
    const serverDefault=config?.route_defaults?.[provider]?.[role]
    setRoutes(current=>({
      ...current,
      [role]:serverDefault
        ? structuredClone(serverDefault)
        : {...current[role],primary_provider:provider},
    }))
  }

  async function saveRoute(role:string){
    setError('')
    setNotice('')
    try{
      const route=routes[role]
      const parameters=route.parameters??{}
      if(!Object.keys(parameters).length) throw new Error('A rota não possui parâmetros seguros de execução e custo.')
      const saved=await adminApi<RouteData>(`/config/routes/${role}`,{
        method:'PUT',
        body:JSON.stringify({...route,parameters}),
      })
      if(saved?.agent_role) setRoutes(current=>({...current,[role]:saved}))
      setNotice(`Rota de ${role.replaceAll('_',' ')} atualizada com preço e limites do modelo.`)
    }catch(reason){
      setError((reason as Error).message)
    }
  }

  return <div className="page">
    <div className="page-heading">
      <div>
        <span className="eyebrow">CONTROLE DO SISTEMA</span>
        <h1>Configuração</h1>
        <p>Credenciais, roteamento de modelos e regras aprendidas em um espaço isolado.</p>
      </div>
      <div className="secure-pill"><Shield size={17}/>Segredos criptografados</div>
    </div>
    {notice&&<div className="notice success"><Check size={16}/>{notice}</div>}
    {error&&<div className="notice error" role="alert">{error}</div>}
    <section className={`panel execution-preflight ${executionPreflight?.status==='ready'?'ready':'not-ready'}`}>
      <div>
        <span className="eyebrow">PRONTIDÃO DA EXECUÇÃO</span>
        <label className="preflight-pipeline-selector">Pipeline
          <select
            aria-label="Pipeline do pré-voo"
            value={preflightPipeline}
            onChange={async event=>{
              const version=event.target.value as 'v2'|'v3'
              setPreflightPipeline(version)
              setError('')
              try{
                setExecutionPreflight(await adminApi<ExecutionPreflight>(
                  `/config/execution-preflight?pipeline_version=${version}`,
                ))
              }catch(reason){
                setError((reason as Error).message)
              }
            }}
          >
            <option value="v2">Editorial V2</option>
            <option value="v3">Editorial V3</option>
          </select>
        </label>
        <h2>{executionPreflight?.status==='ready'?'Dependências prontas':'Dependências precisam de atenção'}</h2>
        <p>{executionPreflight?.status==='ready'
          ?'Rotas, credenciais e skills obrigatórias estão fixáveis antes de criar a execução.'
          :'O sistema não iniciará um conteúdo até corrigir as dependências abaixo.'}</p>
        {Boolean(executionPreflight?.dependencies.length)&&<ul className="dependency-list">
          {executionPreflight?.dependencies.map(item=><li key={item}>{item}</li>)}
        </ul>}
      </div>
      <button type="button" className="button secondary" disabled={repairingDependencies} onClick={repairExecutionDependencies}>
        <RefreshCw size={16} className={repairingDependencies?'spin':''}/>
        {repairingDependencies?'Verificando':'Verificar e corrigir'}
      </button>
    </section>
    <section className="config-grid">
      <article className="panel config-main">
        <div className="panel-head">
          <div className="title-icon"><LockKeyhole/><div><h2>Cofre de credenciais</h2><p>Para testar, configure uma LLM e Tavily ou Serper.</p></div></div>
        </div>
        <div className="credential-grid">
          {providers.map(([id,label])=>{
            const saved=credentials.find(item=>item.provider===id)
            const canVerify=Boolean(saved?.configured&&verifiableProviders.has(id))
            return <form key={id} className="credential" onSubmit={event=>saveKey(event,id)}>
              <div className="credential-head">
                <span>{label}</span>
                {saved?.configured
                  ? <span className="configured"><Check size={13}/>Configurada ····{saved.last_four}</span>
                  : <span className="missing">Não configurada</span>}
              </div>
              <div className="key-input">
                <KeyRound size={17}/>
                <input
                  aria-label={`Credencial ${label}`}
                  type="password"
                  placeholder={saved?.configured?'Substituir credencial':'Cole a chave de API'}
                  value={keys[id]||''}
                  onChange={event=>setKeys(current=>({...current,[id]:event.target.value}))}
                />
                <button className="icon" aria-label={`Salvar ${label}`}><Save size={17}/></button>
              </div>
              {canVerify&&<div className="credential-verification">
                <small>{verifiedLabel(saved?.verified_at||null)}</small>
                <button
                  type="button"
                  className="button secondary compact"
                  disabled={Boolean(verifying)}
                  onClick={()=>verifyCredential(id)}
                >
                  <RefreshCw size={14} className={verifying===id?'spin':''}/>
                  {verifying===id?'Verificando':'Verificar'}
                </button>
              </div>}
            </form>
          })}
        </div>
      </article>
      <aside className="panel policy-card">
        <SlidersHorizontal/>
        <span className="eyebrow">POLÍTICA DE APRENDIZADO</span>
        <strong>{config?.policy.learned_skill_stability_threshold||3} artigos</strong>
        <p>Validações mínimas no mesmo nicho antes de uma skill aprendida ser injetada automaticamente.</p>
        <div className="policy-lock"><LockKeyhole size={15}/>Skills instáveis exigem revisão humana</div>
      </aside>
    </section>
    <section className="panel route-panel">
      <div className="panel-head">
        <div className="title-icon"><Route/><div><h2>Roteamento por agente</h2><p>O botão Salvar usa exatamente o provedor e o modelo exibidos na linha.</p></div></div>
      </div>
      <div className="route-table">
        {defaults.map(base=>{
          const route=routes[base.agent_role]||base
          return <div className="route-row" key={base.agent_role}>
            <div><strong>{base.agent_role.replaceAll('_',' ')}</strong><small>{base.agent_role==='writer'?'Redação apenas com ledger aprovado':'Execução estruturada e auditada'}</small></div>
            <select value={route.primary_provider} onChange={event=>updateProvider(base.agent_role,event.target.value)} aria-label={`Provedor de ${base.agent_role}`}>
              <option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option>
            </select>
            <div className="route-model-field">
              <input value={route.primary_model} onChange={event=>updateRoute(base.agent_role,'primary_model',event.target.value)} aria-label={`Modelo de ${base.agent_role}`}/>
              <small>Saída máx.: {Number(route.parameters?.max_output_tokens||0).toLocaleString('pt-BR')} tokens · US$ {Number(route.parameters?.output_cost_per_million||0).toFixed(2)}/M saída</small>
            </div>
            <button type="button" className="button secondary compact" onClick={()=>saveRoute(base.agent_role)}>Salvar</button>
          </div>
        })}
      </div>
    </section>
    <section className="panel skills-panel">
      <div className="panel-head"><div><h2>Biblioteca de skills</h2><p>Skills default versionadas e padrões aprendidos com origem preservada.</p></div><span className="count">{config?.skills.length||11} ativas</span></div>
      <div className="skill-list">{config?.skills.length?config.skills.map(skill=><div className="skill" key={skill.skill_id}><div><strong>{skill.skill_id}</strong><small>v{skill.version} {skill.niche&&`· ${skill.niche}`}</small></div><span className={`kind ${skill.kind}`}>{skill.kind==='default'?'DEFAULT':skill.stable?'ESTÁVEL':'EM VALIDAÇÃO'}</span></div>):<p className="muted">As skills default serão registradas na inicialização.</p>}</div>
    </section>
  </div>
}
