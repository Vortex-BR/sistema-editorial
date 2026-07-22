// @vitest-environment jsdom
import {cleanup,render,screen,waitFor,within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter,Route,Routes} from 'react-router-dom'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {adminApi,adminDownload} from '../lib/api'
import {Pipeline} from './Pipeline'

vi.mock('../lib/api',()=>({
  adminDownload:vi.fn(),
  adminApi:vi.fn(),
  safePublicMessage:(value:unknown)=>String(value),
  wsUrl:(_projectId:string,pipelineRunId?:string)=>`ws://test/events${pipelineRunId?`?pipeline_run_id=${pipelineRunId}`:''}`,
}))

const mockAdminApi=vi.mocked(adminApi)
const mockAdminDownload=vi.mocked(adminDownload)
const runOne={id:'run-1',status:'running',current_stage:'researcher'}
const articleVersion={
  id:'version-1',article_id:'article-1',pipeline_run_id:'run-1',version:1,
  title:'Artigo final',outline:[],editorial_status:'human_approved',markdown:'# Artigo final',
  html:'<h1>Artigo final</h1>',seo_metadata:{},source_report:{},
}
const approvedReview={
  id:'review-1',project_id:'project-1',pipeline_run_id:'run-1',
  article_version_id:'version-1',reviewer:'Editora Ana',decision:'approved',
  observation:'Peça aprovada',reviewed_at:'2026-07-13T15:00:00Z',revision_run_id:null,
  review_package:{
    facts:[{id:'fact-1',claim:'Fato comprovado',approved:true}],
    sources:[{id:'source-1',title:'Fonte oficial',url:'https://example.com'}],
    coverage:{complete:true,questions:[{id:'question-1',question:'Pergunta prioritária',priority:1,coverage_status:'covered'}]},
    conflicts:[],seo:{title:'Artigo final'},changes:{previous_version:null},risks:[],
  },
}
const executionManifest={
  status:'ready',id:'manifest-1',checksum:'a'.repeat(64),mode:'enforced',
  build:{commit_sha:'abc123',build_version:'release-18'},
  default_skills:[{skill_id:'default.evidence',version:'1.0.0',checksum:'b'.repeat(64)}],
  learned_skills:{writer:[{skill_id:'learned.editorial',version:'2.0.0',checksum:'c'.repeat(64)}]},
  model_routes:{writer:{primary_provider:'openai',primary_model:'fixed-model'}},
  memory_ids:{writer:['memory-1']},style_pattern_ids:{writer:['pattern-1']},
  handoff_ids:['handoff-1'],source_snapshot_ids:['snapshot-1'],
}
const qualityEvaluation={
  id:'quality-1',rubric_version:'quality-rubric.v1',rubric_checksum:'d'.repeat(64),
  evaluator_kind:'deterministic',status:'passed',overall_score:.91,
  result_checksum:'e'.repeat(64),
  axes:{coverage_factual:{score:1,metrics:{}},citation_presence:{score:1,metrics:{}}},
  critical_blockers:[],warnings:[],automatic_publication:false as const,
  human_comparison:{human_decision:'approved',evaluator_recommendation:'passed',agreement:true},
}
const detail={
  project:{id:'project-1',name:'Projeto editorial',topic:'Tema',status:'completed',current_stage:'finalizer'},
  facts:{pipeline_run_id:'run-1',total:1,approved:1},pipeline_runs:[runOne],
  latest_pipeline_run:runOne,selected_pipeline_run:runOne,runs:[],
  article_version:articleVersion,article_pipeline_run_id:'run-1',
  article_matches_selected_pipeline_run:true,
  execution_manifest:executionManifest,
  quality_evaluation:qualityEvaluation,
  research_diagnostic:null,
  editorial_diagnostic:null,
  human_review:approvedReview,human_review_history:[approvedReview],
}
const eventTicket='abcdefghijklmnopqrstuvwxyzABCDEFGH123456789'
let createObjectURL:ReturnType<typeof vi.fn>
let revokeObjectURL:ReturnType<typeof vi.fn>
let anchorClick:ReturnType<typeof vi.spyOn>
let clickedDownload=''
let clickedHref=''
let websocketUrl=''
let websocketProtocols:string|string[]|undefined
let websockets:FakeWebSocket[]=[]

class FakeWebSocket{
  onopen:(()=>void)|null=null
  onmessage:((event:MessageEvent)=>void)|null=null
  onerror:(()=>void)|null=null
  onclose:(()=>void)|null=null
  send=vi.fn()
  close=vi.fn(()=>this.onclose?.())
  constructor(url:string,protocols?:string|string[]){
    websocketUrl=url
    websocketProtocols=protocols
    websockets.push(this)
  }
  open(){this.onopen?.()}
  message(payload:unknown){
    this.onmessage?.(new MessageEvent('message',{data:JSON.stringify(payload)}))
  }
  disconnect(){this.onclose?.()}
}

function streamEvent(sequence:number,pipelineRunId='run-1'){
  return {
    sequence,pipeline_run_id:pipelineRunId,stage:'planner',type:`event.${sequence}`,
    payload:{message:`Evento ${sequence}`},
  }
}

function eventBatch(events:ReturnType<typeof streamEvent>[],pipelineRunId='run-1'){
  return {
    type:'events.batch',pipeline_run_id:pipelineRunId,after_sequence:0,
    last_sequence:events.at(-1)?.sequence||0,events,
  }
}

function detailRequestCount(){
  return mockAdminApi.mock.calls.filter(([path])=>
    path==='/projects/project-1'||path.startsWith('/projects/project-1?'),
  ).length
}

function renderPipeline(){
  return render(<MemoryRouter initialEntries={['/projetos/project-1']}><Routes><Route path="/projetos/:id" element={<Pipeline/>}/></Routes></MemoryRouter>)
}

beforeEach(()=>{
  mockAdminApi.mockImplementation(async (path)=>{
    if(path==='/projects/project-1/events/ticket'){
      return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
    }
    return detail
  })
  mockAdminDownload.mockResolvedValue({
    blob:new Blob(['zip-bytes'],{type:'application/zip'}),
    filename:'projeto-editorial-v1-20260713.zip',
  })
  vi.stubGlobal('WebSocket',FakeWebSocket)
  vi.stubGlobal('crypto',{randomUUID:()=> 'review-request-idempotency-key'})
  createObjectURL=vi.fn().mockReturnValue('blob:editorial-package')
  revokeObjectURL=vi.fn()
  Object.defineProperty(URL,'createObjectURL',{configurable:true,value:createObjectURL})
  Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:revokeObjectURL})
  clickedDownload=''
  clickedHref=''
  websocketUrl=''
  websocketProtocols=undefined
  websockets=[]
  anchorClick=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(function(this:HTMLAnchorElement){
    clickedDownload=this.download
    clickedHref=this.href
  })
})

afterEach(()=>{
  cleanup()
  anchorClick.mockRestore()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('Pipeline editorial export',()=>{
  it('opens the event stream with a run-scoped one-time ticket as a subprotocol',async()=>{
    renderPipeline()

    await waitFor(
      ()=>expect(websocketProtocols).toEqual(['seo-events',eventTicket]),
      {timeout:5000},
    )
    expect(websocketUrl).toBe('ws://test/events?pipeline_run_id=run-1')
    expect(websocketUrl).not.toContain(eventTicket)
    expect(websocketUrl).not.toContain('admin')
    expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects/project-1/events/ticket',
      {method:'POST',body:JSON.stringify({pipeline_run_id:'run-1'})},
    )
    websockets[0].open()
    expect(websockets[0].send).toHaveBeenCalledWith(
      JSON.stringify({type:'subscribe',after_sequence:0}),
    )
  })

  it('coalesces one hundred events into one detail refresh',async()=>{
    renderPipeline()
    await waitFor(()=>expect(websockets).toHaveLength(1))
    websockets[0].open()
    expect(detailRequestCount()).toBe(1)

    for(let sequence=1;sequence<=100;sequence+=1){
      websockets[0].message(eventBatch([streamEvent(sequence)]))
    }

    expect(detailRequestCount()).toBe(1)
    await waitFor(()=>expect(detailRequestCount()).toBe(2),{timeout:1500})
    expect(screen.getByText('Evento 100')).toBeTruthy()
  })

  it('reconnects from the last cursor and ignores duplicate sequences',async()=>{
    renderPipeline()
    await waitFor(()=>expect(websockets).toHaveLength(1))
    websockets[0].open()
    websockets[0].message(eventBatch([streamEvent(1),streamEvent(2)]))
    expect(await screen.findByText('Evento 2')).toBeTruthy()

    websockets[0].disconnect()
    await waitFor(()=>expect(websockets).toHaveLength(2),{timeout:1800})
    websockets[1].open()
    expect(websockets[1].send).toHaveBeenCalledWith(
      JSON.stringify({type:'subscribe',after_sequence:2}),
    )
    websockets[1].message(eventBatch([streamEvent(2),streamEvent(3)]))

    expect(await screen.findByText('Evento 3')).toBeTruthy()
    expect(screen.getAllByText('Evento 2')).toHaveLength(1)
  })

  it('closes the previous connection and clears the cursor when switching runs',async()=>{
    const user=userEvent.setup()
    const twoRunDetail={
      ...detail,
      pipeline_runs:[runOne,{id:'run-2',status:'running',current_stage:'writer'}],
    }
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      if(path==='/projects/project-1?pipeline_run_id=run-2'){
        const runTwo=twoRunDetail.pipeline_runs[1]
        return {
          ...twoRunDetail,
          facts:{pipeline_run_id:'run-2',total:0,approved:0},
          selected_pipeline_run:runTwo,
          article_matches_selected_pipeline_run:false,
        } as never
      }
      return twoRunDetail
    })
    renderPipeline()
    await waitFor(()=>expect(websockets).toHaveLength(1))
    websockets[0].open()
    websockets[0].message(eventBatch([streamEvent(8)]))

    await user.selectOptions(
      await screen.findByRole('combobox',{name:'Execução do pipeline'}),
      'run-2',
    )

    expect(websockets[0].close).toHaveBeenCalledTimes(1)
    await waitFor(()=>expect(websockets).toHaveLength(2))
    expect(websocketUrl).toBe('ws://test/events?pipeline_run_id=run-2')
    websockets[1].open()
    expect(websockets[1].send).toHaveBeenCalledWith(
      JSON.stringify({type:'subscribe',after_sequence:0}),
    )
    expect(mockAdminApi).toHaveBeenCalledWith('/projects/project-1?pipeline_run_id=run-2')
    expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects/project-1/events/ticket',
      {method:'POST',body:JSON.stringify({pipeline_run_id:'run-2'})},
    )
  })

  it('keeps the button disabled without an exportable article',async()=>{
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      return {
        ...detail,article_version:null,article_pipeline_run_id:null,
        article_matches_selected_pipeline_run:null,
      }
    })
    renderPipeline()

    const button=await screen.findByRole('button',{name:'Exportar pacote'})
    expect(button.hasAttribute('disabled')).toBe(true)
    expect(mockAdminDownload).not.toHaveBeenCalled()
  })

  it('enables the button when persisted markdown is available',async()=>{
    renderPipeline()

    const button=await screen.findByRole('button',{name:'Exportar pacote'})
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(mockAdminApi).toHaveBeenCalledWith('/projects/project-1')
  })

  it('shows the complete editor-in-chief review package and labels draft export',async()=>{
    const user=userEvent.setup()
    const pendingReview={
      ...approvedReview,reviewer:null,decision:'pending',observation:null,reviewed_at:null,
      review_package:{
        ...approvedReview.review_package,
        conflicts:[{group:'dates',claims:[{id:'fact-1',claim:'Fato comprovado'}]}],
        risks:[{code:'human_approval_pending',message:'Aguardando decisão humana'}],
      },
    }
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      return {
        ...detail,
        project:{...detail.project,status:'needs_human_approval',current_stage:'human_approval'},
        pipeline_runs:[{...runOne,status:'needs_human_approval',current_stage:'human_approval'}],
        latest_pipeline_run:{...runOne,status:'needs_human_approval',current_stage:'human_approval'},
        selected_pipeline_run:{...runOne,status:'needs_human_approval',current_stage:'human_approval'},
        article_version:{...articleVersion,editorial_status:'needs_human_approval'},
        human_review:pendingReview,human_review_history:[pendingReview],
      } as never
    })
    renderPipeline()

    expect(await screen.findByText('Pacote de revisão final')).toBeTruthy()
    expect(screen.getByText('Fontes e fatos')).toBeTruthy()
    expect(screen.getByText('Cobertura')).toBeTruthy()
    expect(screen.getByText('Conflitos')).toBeTruthy()
    expect(screen.getByText('SEO')).toBeTruthy()
    expect(screen.getByText('Mudanças da última versão')).toBeTruthy()
    expect(screen.getByText('Riscos')).toBeTruthy()
    expect((screen.getByLabelText('Identidade do revisor humano') as HTMLInputElement).value).toBe('')

    await user.click(screen.getByRole('button',{name:'Exportar rascunho'}))
    await waitFor(()=>expect(mockAdminDownload).toHaveBeenCalledWith(
      '/projects/project-1/export?draft=true','pacote-editorial.zip',
    ))
  })

  it('shows the reproducible execution manifest for the selected run',async()=>{
    renderPipeline()

    expect(await screen.findByText('Dependências fixadas para este run')).toBeTruthy()
    expect(screen.getByText('abc123')).toBeTruthy()
    expect(screen.getByText('release-18')).toBeTruthy()
    expect(screen.getByText('1 skills padrão · 1 aprendidas')).toBeTruthy()
    expect(screen.getByText('1 handoffs · 1 snapshots')).toBeTruthy()
  })

  it('shows independent quality scores and human calibration',async()=>{
    renderPipeline()

    expect(await screen.findByText('Rubrica determinística de qualidade')).toBeTruthy()
    expect(screen.getByText('quality-rubric.v1')).toBeTruthy()
    expect(screen.getByText('91%')).toBeTruthy()
    expect(screen.getByText('Concordante')).toBeTruthy()
    expect(screen.getByText('O score nunca publica conteúdo automaticamente.')).toBeTruthy()
  })

  it('records an explicit human identity when approving',async()=>{
    const user=userEvent.setup()
    const pendingReview={
      ...approvedReview,reviewer:null,decision:'pending',observation:null,reviewed_at:null,
    }
    const pendingDetail={
      ...detail,
      project:{...detail.project,status:'needs_human_approval',current_stage:'human_approval'},
      pipeline_runs:[{...runOne,status:'needs_human_approval',current_stage:'human_approval'}],
      latest_pipeline_run:{...runOne,status:'needs_human_approval',current_stage:'human_approval'},
      selected_pipeline_run:{...runOne,status:'needs_human_approval',current_stage:'human_approval'},
      article_version:{...articleVersion,editorial_status:'needs_human_approval'},
      human_review:pendingReview,human_review_history:[pendingReview],
    }
    let approved=false
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/pipeline-runs/run-1/human-review'){
        approved=true
        return {
          review:approvedReview,pipeline_run_status:'completed',revision_run_id:null,
          revision_created:false,duplicate:false,
        } as never
      }
      return (approved?detail:pendingDetail) as never
    })
    renderPipeline()

    await user.type(await screen.findByLabelText('Identidade do revisor humano'),'Editora Ana')
    await user.type(screen.getByLabelText('Observação ou instruções de revisão'),'Peça conferida')
    await user.click(screen.getByRole('button',{name:'Aprovar para publicação'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/pipeline-runs/run-1/human-review',
      expect.objectContaining({
        method:'POST',headers:{'Idempotency-Key':'review-request-idempotency-key'},
      }),
    ))
    const reviewCall=mockAdminApi.mock.calls.find(([path])=>path==='/pipeline-runs/run-1/human-review')
    expect(JSON.parse(String(reviewCall?.[1]?.body))).toEqual({
      decision:'approve',reviewer:'Editora Ana',observation:'Peça conferida',
    })
  })

  it('warns when the displayed article was produced by another run',async()=>{
    const failedRun={id:'run-failed',status:'failed',current_stage:'researcher'}
    mockAdminApi.mockResolvedValue({
      ...detail,
      facts:{pipeline_run_id:failedRun.id,total:2,approved:0},
      pipeline_runs:[failedRun,runOne],
      latest_pipeline_run:failedRun,
      selected_pipeline_run:failedRun,
      article_version:{...articleVersion,pipeline_run_id:runOne.id},
      article_pipeline_run_id:runOne.id,
      article_matches_selected_pipeline_run:false,
    } as never)

    renderPipeline()

    expect(await screen.findByText('O artigo exibido foi produzido por outro run')).toBeTruthy()
    expect(screen.getByText(/Run selecionado: run-failed/)).toBeTruthy()
    expect(screen.getByText(/Run do artigo: run-1/)).toBeTruthy()
    expect(screen.getByText('# Artigo final')).toBeTruthy()
  })

  it('downloads the blob once, uses the server filename and revokes the URL',async()=>{
    const user=userEvent.setup()
    renderPipeline()
    const button=await screen.findByRole('button',{name:'Exportar pacote'})

    await user.click(button)

    await waitFor(()=>expect(mockAdminDownload).toHaveBeenCalledWith('/projects/project-1/export','pacote-editorial.zip'))
    expect(mockAdminDownload).toHaveBeenCalledTimes(1)
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
    expect(anchorClick).toHaveBeenCalledTimes(1)
    expect(clickedDownload).toBe('projeto-editorial-v1-20260713.zip')
    expect(clickedHref).toBe('blob:editorial-package')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:editorial-package')
    expect(document.querySelector('a[download]')).toBeNull()
    expect(mockAdminDownload.mock.calls[0][0]).not.toContain('token')
  })

  it('shows loading and prevents repeated clicks while generation is pending',async()=>{
    const user=userEvent.setup()
    let resolveDownload:(value:{blob:Blob;filename:string})=>void=()=>{}
    mockAdminDownload.mockImplementation(()=>new Promise(resolve=>{resolveDownload=resolve}))
    renderPipeline()
    const button=await screen.findByRole('button',{name:'Exportar pacote'})

    await user.click(button)
    expect(await screen.findByRole('button',{name:'Gerando pacote…'})).toBeTruthy()
    await user.click(screen.getByRole('button',{name:'Gerando pacote…'}))
    expect(mockAdminDownload).toHaveBeenCalledTimes(1)
    resolveDownload({blob:new Blob(['zip']),filename:'pacote.zip'})

    await waitFor(()=>expect(screen.getByRole('button',{name:'Exportar pacote'})).toBeTruthy())
  })

  it('shows a safe public error and restores the button',async()=>{
    const user=userEvent.setup()
    mockAdminDownload.mockRejectedValue(new Error('Não foi possível gerar o pacote.'))
    renderPipeline()

    await user.click(await screen.findByRole('button',{name:'Exportar pacote'}))

    expect((await screen.findByRole('alert')).textContent).toContain('Não foi possível gerar o pacote.')
    expect(screen.getByRole('button',{name:'Exportar pacote'}).hasAttribute('disabled')).toBe(false)
  })

  it('confirms cancellation for only the selected running execution',async()=>{
    const user=userEvent.setup()
    const requestedAt='2026-07-13T16:00:00Z'
    let cancellationRequested=false
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      if(path==='/pipeline-runs/run-1/cancel'){
        cancellationRequested=true
        return {
          pipeline_run_id:'run-1',status:'running',
          cancellation_requested_at:requestedAt,cancellation_pending:true,
        } as never
      }
      return cancellationRequested
        ? {
            ...detail,
            pipeline_runs:[{...runOne,cancellation_requested_at:requestedAt}],
            selected_pipeline_run:{...runOne,cancellation_requested_at:requestedAt},
          }
        : detail
    })
    renderPipeline()

    await user.click(await screen.findByRole('button',{name:/Cancelar execu/}))
    expect(mockAdminApi).not.toHaveBeenCalledWith(
      '/pipeline-runs/run-1/cancel',expect.anything(),
    )
    const dialog=screen.getByRole('alertdialog')
    await user.click(within(dialog).getByRole('button',{name:/Cancelar execu/}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/pipeline-runs/run-1/cancel',{method:'POST'},
    ))
    expect(await screen.findByText('Cancelamento solicitado')).toBeTruthy()
    expect(screen.queryByRole('button',{name:/Cancelar execu/})).toBeNull()
  })

  it('does not offer cancellation for a terminal execution',async()=>{
    mockAdminApi.mockResolvedValue({
      ...detail,
      pipeline_runs:[{...runOne,status:'completed'}],
      latest_pipeline_run:{...runOne,status:'completed'},
      selected_pipeline_run:{...runOne,status:'completed'},
    } as never)

    renderPipeline()

    await screen.findByText('Projeto editorial')
    expect(screen.queryByRole('button',{name:/Cancelar execu/})).toBeNull()
    expect(screen.queryByText('AO VIVO')).toBeNull()
    expect(websockets).toHaveLength(0)
  })

  it('shows the run-level diagnosis when no agent was created',async()=>{
    const failedRun={
      ...runOne,status:'failed',current_stage:'content_contract',
      error_code:'V3_CONTENT_CONTRACT_INVALID',
      error_message:'O briefing não pôde ser convertido no contrato editorial.',
    }
    mockAdminApi.mockResolvedValue({
      ...detail,
      project:{...detail.project,status:'failed',current_stage:'content_contract'},
      pipeline_runs:[failedRun],latest_pipeline_run:failedRun,
      selected_pipeline_run:failedRun,runs:[],
    } as never)

    renderPipeline()

    expect(await screen.findByText('A execução encontrou uma falha técnica')).toBeTruthy()
    expect(screen.getByText('O briefing não pôde ser convertido no contrato editorial.')).toBeTruthy()
    expect(screen.getByText('Diagnóstico: V3_CONTENT_CONTRACT_INVALID')).toBeTruthy()
    expect(screen.queryByText('AO VIVO')).toBeNull()
  })

  it('labels a cancelled follow-up without degrading the approved project',async()=>{
    const cancelledRun={...runOne,status:'cancelled',current_stage:'writer'}
    mockAdminApi.mockResolvedValue({
      ...detail,
      project:{...detail.project,status:'completed',last_run_status:'cancelled'},
      pipeline_runs:[cancelledRun],
      latest_pipeline_run:cancelledRun,
      selected_pipeline_run:cancelledRun,
      article_matches_selected_pipeline_run:false,
    } as never)

    renderPipeline()

    expect(await screen.findByText('Concluído')).toBeTruthy()
    expect(screen.getByRole('option',{name:/Cancelado pelo operador/})).toBeTruthy()
    expect(screen.queryByText('Falha técnica')).toBeNull()
  })

  it('shows an actionable diagnostic for an immutable legacy research failure',async()=>{
    const failedRun={
      id:'dd6c9292-0000-0000-0000-000000000000',status:'failed',
      current_stage:'blocked',outcome_code:'research_insufficient',
    }
    mockAdminApi.mockResolvedValue({
      ...detail,
      project:{...detail.project,status:'failed',current_stage:'blocked'},
      facts:{pipeline_run_id:failedRun.id,total:40,approved:0},
      pipeline_runs:[failedRun],latest_pipeline_run:failedRun,
      selected_pipeline_run:failedRun,
      article_version:null,article_pipeline_run_id:null,
      article_matches_selected_pipeline_run:null,
      quality_evaluation:null,human_review:null,human_review_history:[],
      research_diagnostic:{
        pipeline_run_id:failedRun.id,outcome_code:'research_insufficient',
        decision:'insufficient',coverage_complete:false,
        covered_question_count:4,total_question_count:6,
        recommended_fact_count:7,distinct_source_count:5,
        minimum_distinct_sources:5,source_diversity_score:1,
        missing_questions:['Qual velocidade atende ao cenário?','Quais limitações contratuais existem?'],
        unresolved_conflicts:['minimum_speed_requirement'],
        rejection_reason_counts:{off_topic:12},
        instructions:['Pesquisar as perguntas ausentes no contexto de internet residencial'],
      },
    } as never)

    renderPipeline()

    expect(await screen.findByText('4 de 6 perguntas cobertas')).toBeTruthy()
    expect(screen.getByText('Qual velocidade atende ao cenário?')).toBeTruthy()
    expect(screen.getByText('minimum speed requirement')).toBeTruthy()
    expect(screen.getByText('off topic: 12')).toBeTruthy()
    expect(screen.getAllByText('Pesquisa insuficiente').length).toBeGreaterThan(0)
    expect(screen.getByRole('option',{name:/Pesquisa insuficiente/})).toBeTruthy()
  })

  it('shows why the editor intervened and that an evidence-only article was delivered',async()=>{
    const editorialRun={
      ...runOne,status:'needs_human_approval',current_stage:'human_approval',
    }
    mockAdminApi.mockResolvedValue({
      ...detail,
      pipeline_runs:[editorialRun],latest_pipeline_run:editorialRun,
      selected_pipeline_run:editorialRun,
      editorial_diagnostic:{
        pipeline_run_id:'run-1',decision:'approved',model_decision:'rewrite',
        resolution:'evidence_only_summary',blocking_finding_count:1,
        findings:[{
          category:'fidelity',severity:'major',
          issue:'A afirmação era mais forte do que a fonte.',
          suggested_action:'Manter somente o fato verificado.',
        }],
      },
    } as never)

    renderPipeline()

    expect(await screen.findByText('Síntese segura entregue')).toBeTruthy()
    expect(screen.getByText('A afirmação era mais forte do que a fonte.')).toBeTruthy()
    expect(screen.getByText('Manter somente o fato verificado.')).toBeTruthy()
  })

  it('starts a legacy project that was created without any pipeline run',async()=>{
    const user=userEvent.setup()
    const noRunDetail={
      ...detail,
      project:{...detail.project,status:'draft',current_stage:'planner'},
      facts:{pipeline_run_id:null,total:0,approved:0},
      pipeline_runs:[],latest_pipeline_run:null,selected_pipeline_run:null,runs:[],
      article_version:null,article_pipeline_run_id:null,article_matches_selected_pipeline_run:null,
      execution_manifest:null,quality_evaluation:null,research_diagnostic:null,
      editorial_diagnostic:null,human_review:null,human_review_history:[],
    }
    const queuedRun={id:'run-first',status:'queued',current_stage:'planner'}
    mockAdminApi.mockImplementation(async path=>{
      if(path==='/projects/project-1/run') return {
        project_id:'project-1',pipeline_run_id:'run-first',status:'queued',duplicate:false,
      } as never
      if(path==='/projects/project-1?pipeline_run_id=run-first') return {
        ...noRunDetail,
        project:{...noRunDetail.project,status:'queued'},
        pipeline_runs:[queuedRun],latest_pipeline_run:queuedRun,selected_pipeline_run:queuedRun,
      } as never
      if(path==='/projects/project-1/events/ticket') return {
        ticket:eventTicket,expires_in:60,protocol:'seo-events',
      } as never
      return noRunDetail as never
    })

    renderPipeline()
    await user.click(await screen.findByRole('button',{name:'Iniciar execução'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects/project-1/run',
      {method:'POST',headers:{'Idempotency-Key':'review-request-idempotency-key'}},
    ))
    await waitFor(()=>expect(
      (screen.getByRole('combobox',{name:'Execução do pipeline'}) as HTMLSelectElement).value,
    ).toBe('run-first'))
  })

  it('starts one clean rerun with an idempotency key and selects it',async()=>{
    const user=userEvent.setup()
    const failedRun={id:'run-failed',status:'blocked',current_stage:'blocked'}
    const failedDetail={
      ...detail,
      project:{...detail.project,status:'blocked',current_stage:'blocked'},
      facts:{pipeline_run_id:failedRun.id,total:40,approved:0},
      pipeline_runs:[failedRun],latest_pipeline_run:failedRun,
      selected_pipeline_run:failedRun,research_diagnostic:null,
    }
    const queuedRun={id:'run-clean',status:'queued',current_stage:'planner'}
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/run'){
        return {
          project_id:'project-1',pipeline_run_id:'run-clean',status:'queued',duplicate:false,
        } as never
      }
      if(path==='/projects/project-1?pipeline_run_id=run-clean'){
        return {
          ...detail,
          project:{...detail.project,status:'queued',current_stage:'planner'},
          facts:{pipeline_run_id:'run-clean',total:0,approved:0},
          pipeline_runs:[queuedRun,failedRun],latest_pipeline_run:queuedRun,
          selected_pipeline_run:queuedRun,research_diagnostic:null,
        } as never
      }
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      return failedDetail as never
    })

    renderPipeline()
    await user.click(await screen.findByRole('button',{name:'Executar nova pesquisa'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects/project-1/run',
      {method:'POST',headers:{'Idempotency-Key':'review-request-idempotency-key'}},
    ))
    expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects/project-1?pipeline_run_id=run-clean',
    )
    await waitFor(()=>expect(
      (screen.getByRole('combobox',{name:'Execução do pipeline'}) as HTMLSelectElement).value,
    ).toBe('run-clean'))
    expect(screen.queryByRole('button',{name:'Executar nova pesquisa'})).toBeNull()
  })
})

describe('V3 information coverage diagnostics',()=>{
  it('shows requirement-level coverage and the exact unsupported information',async()=>{
    const blockedRun={id:'run-1',status:'blocked',current_stage:'blocked'}
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      return {
        ...detail,
        outcome_code:'V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE',
        project:{...detail.project,status:'blocked',current_stage:'blocked'},
        pipeline_runs:[blockedRun],latest_pipeline_run:blockedRun,
        selected_pipeline_run:blockedRun,
        v3_research_runtime:{
          version:'v3',stage:'knowledge_synthesizer',
          blocking_code:'V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE',
          blocking_reason:'Faltou suporte para uma informação crítica.',
          research_intent:{canonical_subject:'germinação de sementes',project_locale:'pt-BR'},
          search_budget:{logical_queries:8,maximum_logical_queries:24,provider_requests:8,provider_retries:1},
          source_fetch_count:6,structured_source_count:4,source_recovery_round:1,
          source_coverage:{status:'passed',deficient_task_ids:[],reason_codes:[]},
          information_recovery_round:2,information_recovery_task_count:3,
          information_recovery_exhausted:false,
          information_coverage:{
            status:'incomplete',overall_coverage_ratio:.75,critical_coverage_ratio:.5,
            covered_requirement_ids:['req-1','req-2','req-3'],
            partial_requirement_ids:['req-4'],uncovered_requirement_ids:[],
            critical_missing_requirement_ids:['req-4'],supporting_missing_requirement_ids:[],
            requirement_reports:[{
              requirement_id:'req-4',task_id:'task-1',knowledge_node_id:'temperatura_e_umidade',
              requirement_type:'knowledge',
              description:'Qual faixa de temperatura possui suporte técnico para a germinação?',
              critical:true,status:'partial',approved_claim_count:1,raw_claim_count:2,
              independent_source_count:1,authoritative_source_count:1,
              required_evidence_roles:['technical'],evidence_roles_found:['technical'],
              supporting_claim_ids:['claim-1'],reason_codes:['independent_support_insufficient'],
            }],
            reason_codes:['independent_support_insufficient'],
            suggested_blocking_code:'V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE',
          },
        },
      } as never
    })

    renderPipeline()

    expect(await screen.findByText('COBERTURA POR INFORMAÇÃO')).toBeTruthy()
    expect(screen.getByText('75%')).toBeTruthy()
    expect(screen.getByText('50%')).toBeTruthy()
    expect(screen.getByText('Qual faixa de temperatura possui suporte técnico para a germinação?')).toBeTruthy()
    expect(screen.getByText('1 fonte independente')).toBeTruthy()
    expect(screen.getByText('faltam fontes independentes')).toBeTruthy()
    expect(screen.getByText(/rodada 2 · 3 informações na fila/)).toBeTruthy()
  })
})

describe('Project error logs tab',()=>{
  it('loads run-scoped technical logs and exposes diagnostic details',async()=>{
    const user=userEvent.setup()
    const failedRun={
      id:'run-1',status:'failed',current_stage:'source_reader',
      error_code:'sqlalchemy.exc.IntegrityError',
      error_message:'Falha técnica registrada',
    }
    const errorLogs={
      project_id:'project-1',pipeline_run_id:'run-1',generated_at:'2026-07-21T20:00:00Z',total:1,
      summary:{critical:1,error:0,warning:0,retryable:0,recovered:0},
      logs:[{
        id:'log-1',source:'internal',severity:'critical',timestamp:'2026-07-21T19:57:33Z',
        stage:'source_reader',title:'sqlalchemy.exc.IntegrityError',message:'duplicate key value',
        error_code:'sqlalchemy.exc.IntegrityError',error_category:'persistence',
        correlation_id:'correlation-1',retryable:false,recovered:false,http_status:null,
        provider:null,model:null,attempt:1,operation:'INSERT',exception_type:'sqlalchemy.exc.IntegrityError',
        sql_template:'INSERT INTO v3_source_documents (...)',traceback:'Traceback sanitizado',metadata:{run_attempt:1},
      }],
    }
    mockAdminApi.mockImplementation(async (path)=>{
      if(path==='/projects/project-1/error-logs?pipeline_run_id=run-1&limit=200') return errorLogs as never
      if(path==='/projects/project-1/events/ticket'){
        return {ticket:eventTicket,expires_in:60,protocol:'seo-events'} as never
      }
      return {
        ...detail,project:{...detail.project,status:'failed',current_stage:'source_reader'},
        pipeline_runs:[failedRun],latest_pipeline_run:failedRun,selected_pipeline_run:failedRun,
        article_version:null,article_pipeline_run_id:null,article_matches_selected_pipeline_run:null,
      } as never
    })

    renderPipeline()
    await user.click(await screen.findByRole('button',{name:/Logs de erros/}))

    expect(await screen.findByText('duplicate key value')).toBeTruthy()
    expect(screen.getAllByText('sqlalchemy.exc.IntegrityError').length).toBeGreaterThan(0)
    expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects/project-1/error-logs?pipeline_run_id=run-1&limit=200',
    )
    await user.click(screen.getAllByText('sqlalchemy.exc.IntegrityError')[0])
    expect(await screen.findByText('Referência')).toBeTruthy()
    expect(screen.getByText('correlation-1')).toBeTruthy()
  })
})
