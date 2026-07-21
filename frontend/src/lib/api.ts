const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'
const PUBLIC_ERROR = 'Não foi possível concluir esta etapa. Os detalhes técnicos foram registrados internamente.'
const TECHNICAL_ERROR = /(Traceback|sqlalchemy|asyncpg|INSERT\s+INTO|SELECT\s+.+\s+FROM|parameters\s*:|File\s+["']?\/app\/|UntranslatableCharacterError)/i
const ADMIN_REQUIRED = 'Autorização administrativa necessária.'
const FIELD_LABELS:Record<string,string>={
  additional_context:'Contexto adicional',
  article_promise:'Promessa editorial',
  audience:'Público-alvo',
  content_objective:'Objetivo do conteúdo',
  name:'Nome do projeto',
  primary_keyword:'Palavra-chave principal',
  research_subject:'Assunto factual da pesquisa',
  reader_context:'Contexto do leitor',
  reader_final_state:'Estado final observável',
  reader_goal:'O que o leitor busca',
  reader_start_state:'Estado inicial do leitor',
  scope_limit:'Ponto de encerramento do conteúdo',
  topic:'Tópico principal',
}

const READINESS_COMPONENT_LABELS:Record<string,string>={
  api:'API',
  postgresql:'PostgreSQL',
  migrations:'migrações',
  vector:'pgvector',
  redis:'Redis',
  broker:'broker',
  worker:'worker',
  beat:'agendador',
  configuration:'pré-voo da aplicação',
  execution_dependencies:'dependências de execução',
  skills_mode:'modo das skills superiores',
}
const READINESS_STATE_LABELS:Record<string,string>={
  unavailable:'indisponível',
  unknown:'estado desconhecido',
  outdated:'desatualizadas',
  missing:'ausente',
  stale:'heartbeat expirado',
  duplicate:'mais de uma instância ativa',
  invalid:'inválido',
  not_ready:'incompletas',
  not_enforced:'não está em modo enforced',
}

export type ReadinessStatus='ready'|'not_ready'|'unavailable'
export type ReadinessReport={
  status:ReadinessStatus
  components:Record<string,{status:string}>
}

type AdminTokenRequester = () => Promise<string|null>
let adminToken:string|null = null
let adminTokenRequester:AdminTokenRequester|null = null
let pendingAdminTokenRequest:Promise<string|null>|null = null
let adminTokenRequesterReady:Promise<void>
let resolveAdminTokenRequesterReady:()=>void

function resetAdminTokenRequesterBarrier():void{
  adminTokenRequesterReady=new Promise<void>(resolve=>{
    resolveAdminTokenRequesterReady=resolve
  })
}

resetAdminTokenRequesterBarrier()

function isRecord(value:unknown):value is Record<string,unknown>{
  return typeof value==='object'&&value!==null&&!Array.isArray(value)
}

function readinessComponents(value:unknown):Record<string,{status:string}>{
  if(!isRecord(value)) return {}
  const result:Record<string,{status:string}>={}
  for(const [name,component] of Object.entries(value)){
    if(isRecord(component)&&typeof component.status==='string'){
      result[name]={status:component.status}
    }
  }
  return result
}

function isReadinessReport(value:unknown):value is ReadinessReport{
  if(!isRecord(value)||!['ready','not_ready'].includes(String(value.status))) return false
  return isRecord(value.components)
}

export function readinessBlockers(report:Pick<ReadinessReport,'components'>):string[]{
  return Object.entries(report.components)
    .filter(([,component])=>component.status!=='ready')
    .map(([name,component])=>{
      const componentLabel=READINESS_COMPONENT_LABELS[name]||name.replaceAll('_',' ')
      const stateLabel=READINESS_STATE_LABELS[component.status]||component.status.replaceAll('_',' ')
      return `${componentLabel}: ${stateLabel}`
    })
}

export function readinessMessage(
  report:ReadinessReport,
  fallback='O sistema ainda não está pronto para iniciar uma execução.',
):string{
  if(report.status==='ready') return 'Sistema pronto para iniciar novas execuções.'
  const blockers=readinessBlockers(report)
  return blockers.length?`${fallback} Bloqueios: ${blockers.join(', ')}.`:fallback
}

function publicString(value:unknown,fallback:string):string{
  if(typeof value!=='string'||!value.trim()) return fallback
  return TECHNICAL_ERROR.test(value)?PUBLIC_ERROR:value.slice(0,1000)
}

function validationIssueMessage(value:unknown):string|null{
  if(!isRecord(value)) return null
  const location:unknown[]=Array.isArray(value.loc)?value.loc:[]
  let field:string|undefined
  for(let index=location.length-1;index>=0;index-=1){
    const item=location[index]
    if(typeof item==='string'&&item!=='body'){
      field=item
      break
    }
  }
  const label=typeof field==='string'?(FIELD_LABELS[field]||field.replaceAll('_',' ')):'Dados enviados'
  const context=isRecord(value.ctx)?value.ctx:{}

  if(value.type==='string_too_long'&&typeof context.max_length==='number'){
    return `${label}: use no máximo ${context.max_length.toLocaleString('pt-BR')} caracteres.`
  }
  if(value.type==='string_too_short'&&typeof context.min_length==='number'){
    return `${label}: use pelo menos ${context.min_length.toLocaleString('pt-BR')} caracteres.`
  }
  if(value.type==='missing') return `${label}: preenchimento obrigatório.`

  const message=publicString(value.msg,'')
  return message?`${label}: ${message}`:null
}

export function safePublicMessage(value:unknown, fallback='Não foi possível concluir a operação'):string{
  if(Array.isArray(value)){
    const issues=value.map(validationIssueMessage).filter((message):message is string=>Boolean(message))
    return issues.length?issues.slice(0,3).join(' '):fallback
  }
  if(isRecord(value)){
    if('detail' in value) return safePublicMessage(value.detail,fallback)
    const base='message' in value?publicString(value.message,fallback):fallback
    const dependencies=Array.isArray(value.dependencies)
      ?value.dependencies.filter(item=>typeof item==='string').slice(0,12)
      :[]
    const componentBlockers=readinessBlockers({components:readinessComponents(value.components)}).slice(0,12)
    const errorCode=typeof value.error_code==='string'?value.error_code:''
    const diagnostics=[
      dependencies.length?`Dependências: ${dependencies.join(', ')}.`:'',
      componentBlockers.length?`Bloqueios: ${componentBlockers.join(', ')}.`:'',
      errorCode?`Código: ${errorCode}.`:'',
    ].filter(Boolean).join(' ')
    return diagnostics?`${base} ${diagnostics}`:base
  }
  return publicString(value,fallback)
}

async function request<T>(path:string, init?:RequestInit, token?:string):Promise<T>{
  const headers = new Headers(init?.headers)
  headers.set('Content-Type','application/json')
  if(token) headers.set('X-Admin-Token',token)
  const response=await fetch(`${API_URL}${path}`,{...init,headers})
  if(!response.ok) throw new Error(safePublicMessage((await response.json().catch(()=>null))?.detail))
  return response.json()
}

export async function api<T>(path:string, init?:RequestInit):Promise<T>{
  return request<T>(path,init)
}

export async function getReadiness(pipelineVersion?:'v2'|'v3'):Promise<ReadinessReport>{
  const query=pipelineVersion?`?pipeline_version=${encodeURIComponent(pipelineVersion)}`:''
  try{
    const response=await fetch(`${API_URL}/readiness${query}`,{
      method:'GET',
      headers:{Accept:'application/json'},
    })
    const payload=await response.json().catch(()=>null)
    if(isReadinessReport(payload)&&(response.ok||response.status===503)) return payload
    if(!response.ok) throw new Error(safePublicMessage(isRecord(payload)?payload.detail:payload))
  }catch{
    return {status:'unavailable',components:{api:{status:'unavailable'}}}
  }
  return {status:'unavailable',components:{api:{status:'unavailable'}}}
}

export function clearAdminToken():void{
  adminToken=null
}

export function setAdminTokenRequester(requester:AdminTokenRequester|null):void{
  adminTokenRequester=requester
  resolveAdminTokenRequesterReady()
  if(!requester) resetAdminTokenRequesterBarrier()
}

async function requestAdminToken():Promise<string|null>{
  if(!adminTokenRequester) await adminTokenRequesterReady
  return adminTokenRequester?.()??null
}

async function requireAdminToken():Promise<string>{
  if(adminToken) return adminToken
  if(!pendingAdminTokenRequest){
    pendingAdminTokenRequest=requestAdminToken()
  }
  const request=pendingAdminTokenRequest
  try{
    const provided=await request
    if(!provided||!provided.trim()) throw new Error(ADMIN_REQUIRED)
    adminToken=provided
    return provided
  }finally{
    if(pendingAdminTokenRequest===request) pendingAdminTokenRequest=null
  }
}

function rejectAdminToken(token:string):void{
  if(adminToken===token) clearAdminToken()
}

export async function adminApi<T>(path:string, init?:RequestInit):Promise<T>{
  for(let attempt=0;attempt<2;attempt+=1){
    const token=await requireAdminToken()
    const headers = new Headers(init?.headers)
    headers.set('Content-Type','application/json')
    headers.set('X-Admin-Token',token)
    const response=await fetch(`${API_URL}${path}`,{...init,headers})
    if(response.status===401||response.status===403){
      rejectAdminToken(token)
      if(attempt===0) continue
    }
    if(!response.ok) throw new Error(safePublicMessage((await response.json().catch(()=>null))?.detail))
    return response.json()
  }
  throw new Error(ADMIN_REQUIRED)
}

export type AdminDownload={blob:Blob;filename:string}

export function safeDownloadFilename(disposition:string|null,fallback='pacote-editorial.zip'):string{
  let candidate:string
  const encoded=disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain=disposition?.match(/filename="?([^";]+)"?/i)?.[1]
  try{candidate=encoded?decodeURIComponent(encoded):plain||''}catch{candidate=''}
  candidate=(candidate.split(/[\\/]/).pop()||'')
    .replace(/[\u0000-\u001f\u007f<>:"|?*]/g,'-')
    .replace(/\.{2,}/g,'.')
    .trim()
  if(!candidate.toLowerCase().endsWith('.zip')) candidate=fallback
  const stem=candidate.slice(0,-4).slice(0,140).replace(/[. ]+$/g,'')||'pacote-editorial'
  return `${stem}.zip`
}

export async function adminDownload(path:string,fallbackFilename='pacote-editorial.zip'):Promise<AdminDownload>{
  for(let attempt=0;attempt<2;attempt+=1){
    const token=await requireAdminToken()
    const headers=new Headers()
    headers.set('X-Admin-Token',token)
    const response=await fetch(`${API_URL}${path}`,{method:'GET',headers})
    if(response.status===401||response.status===403){
      rejectAdminToken(token)
      if(attempt===0) continue
    }
    if(!response.ok) throw new Error(safePublicMessage((await response.json().catch(()=>null))?.detail))
    return {
      blob:await response.blob(),
      filename:safeDownloadFilename(response.headers.get('Content-Disposition'),fallbackFilename),
    }
  }
  throw new Error(ADMIN_REQUIRED)
}

export const wsUrl=(projectId:string,pipelineRunId:string)=>{
  const base = API_URL.startsWith('http')
    ? API_URL.replace(/^http/, 'ws')
    : `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${API_URL}`
  const url=`${base}/projects/${projectId}/events`
  return `${url}?pipeline_run_id=${encodeURIComponent(pipelineRunId)}`
}
