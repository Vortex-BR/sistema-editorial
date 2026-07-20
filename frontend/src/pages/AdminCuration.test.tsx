// @vitest-environment jsdom
import {cleanup,render,screen,waitFor,within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {adminApi,clearAdminToken} from '../lib/api'
import {AdminCuration} from './AdminCuration'

vi.mock('../lib/api',()=>({
  adminApi:vi.fn(),
  clearAdminToken:vi.fn(),
}))

const mockAdminApi=vi.mocked(adminApi)
const project={id:'project-1',name:'Projeto seguro'}
const memory={
  id:'memory-1',agent_role:'researcher',project_id:project.id,niche:'testes',kind:'fact',
  content:'Memória antes da aprovação',confidence:.91,status:'quarantine',source_type:'human',
  source_id:'source-1',origin_pipeline_run_id:'run-1',created_at:'2026-07-13T12:00:00Z',
}
const pattern={
  id:'pattern-1',project_id:project.id,target_agent_role:'writer',niche:'testes',pattern_type:'structure',
  description:'Abrir com uma resposta direta',source_ids:['source-1','source-2','source-3'],
  independent_domain_count:3,validation_count:1,status:'quarantine',origin_pipeline_run_id:'run-1',
  approved_at:null,created_at:'2026-07-13T12:00:00Z',
}

function defaultResponses(){
  mockAdminApi.mockImplementation(async (path,init)=>{
    if(path==='/admin/agent-context/preview'&&init?.method==='POST') return {mode:'enforced',metadata:{versions:{'researcher.core':'1.0.0'},memory_ids:['memory-1'],style_pattern_ids:['pattern-1'],handoff_id:'handoff-1',pipeline_run_id:'run-1',credential:'provider-test-secret'},preview:'<superior_context>Contexto seguro</superior_context>',compiled_context:'<superior_context>Contexto seguro</superior_context>'} as never
    if(init?.method==='POST') return {} as never
    if(path==='/projects') return [project] as never
    if(path===`/projects/${project.id}`) return {pipeline_runs:[{id:'run-1',status:'running',current_stage:'researcher'}]} as never
    if(path.startsWith('/admin/memories')) return [memory] as never
    if(path.startsWith('/admin/style-patterns')) return [pattern] as never
    if(path.startsWith('/admin/style-sources')) return [] as never
    if(path==='/admin/superior-skills') return [{id:'skill-1',skill_id:'researcher.core',scope:'agent',agent_role:'researcher',enabled:true,current_version:'1.0.0'}] as never
    if(path==='/admin/superior-skills/researcher.core/versions') return [{id:'version-1',version:'1.1.0',status:'draft',checksum:'abcdef1234567890',definition:{mission:'Pesquisar com evidência'},reviewed_by_human:false,approved_at:null,created_by:'curator',created_at:'2026-07-13T12:00:00Z'}] as never
    throw new Error(`unexpected admin request: ${path}`)
  })
}

beforeEach(()=>{
  vi.clearAllMocks()
  defaultResponses()
})
afterEach(cleanup)

describe('AdminCuration',()=>{
  it('loads memories, applies supported filters and renders safe fields',async()=>{
    const user=userEvent.setup()
    render(<AdminCuration/>)

    expect(await screen.findByText('Memória antes da aprovação')).toBeTruthy()
    await screen.findByRole('option',{name:'Projeto seguro'})
    await user.selectOptions(screen.getByLabelText('Status'),'rejected')
    await user.selectOptions(screen.getByLabelText('Papel'),'writer')
    await user.selectOptions(screen.getByLabelText('Projeto'),project.id)
    await user.click(screen.getByRole('button',{name:/Aplicar filtros/i}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(expect.stringMatching(/\/admin\/memories\?.*memory_status=rejected.*agent_role=writer.*project_id=project-1/)))
    expect(screen.queryByText('provider-test-secret')).toBeNull()
  })

  it('shows empty and safe error states',async()=>{
    mockAdminApi.mockResolvedValueOnce([])
    const view=render(<AdminCuration/>)
    expect(await screen.findByText('Nenhum item encontrado')).toBeTruthy()
    view.unmount()

    mockAdminApi.mockRejectedValueOnce(new Error('Falha pública segura'))
    render(<AdminCuration/>)
    expect((await screen.findByRole('alert')).textContent).toContain('Falha pública segura')
  })

  it('requires confirmation before approving a memory and refreshes the list',async()=>{
    const user=userEvent.setup()
    render(<AdminCuration/>)
    await screen.findByText('Memória antes da aprovação')

    await user.click(screen.getByRole('button',{name:'Aprovar'}))
    const dialog=screen.getByRole('alertdialog')
    expect(within(dialog).getByText('Aprovar memória')).toBeTruthy()
    expect(mockAdminApi).not.toHaveBeenCalledWith('/admin/memories/memory-1/decision',expect.anything())
    await user.click(within(dialog).getByRole('button',{name:'Aprovar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith('/admin/memories/memory-1/decision',{method:'POST',body:JSON.stringify({decision:'approved'})}))
    expect(await screen.findByText('Memória aprovada com sucesso.')).toBeTruthy()
  })

  it('supports rejection of a style pattern through the same confirmed flow',async()=>{
    const user=userEvent.setup()
    render(<AdminCuration/>)
    await screen.findByText('Memória antes da aprovação')
    await user.click(screen.getByRole('tab',{name:'Padrões de estilo'}))
    expect(await screen.findByText('Abrir com uma resposta direta')).toBeTruthy()

    await user.click(screen.getByRole('button',{name:'Rejeitar'}))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button',{name:'Rejeitar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith('/admin/style-patterns/pattern-1/decision',{method:'POST',body:JSON.stringify({decision:'rejected'})}))
  })

  it('shows versions read-only and confirms activation',async()=>{
    const user=userEvent.setup()
    render(<AdminCuration/>)
    await screen.findByText('Memória antes da aprovação')
    await user.click(screen.getByRole('tab',{name:'Super-skills'}))
    expect(await screen.findByText('Versão 1.1.0')).toBeTruthy()

    await user.click(screen.getByRole('button',{name:'Ativar versão'}))
    const dialog=screen.getByRole('alertdialog')
    expect(within(dialog).getByText('Ativar versão 1.1.0?')).toBeTruthy()
    await user.click(within(dialog).getByRole('button',{name:'Ativar versão'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith('/admin/superior-skills/researcher.core/versions/1.1.0/activate',{method:'POST'}))
  })

  it('previews the actual context without rendering unrelated secret metadata',async()=>{
    const user=userEvent.setup()
    render(<AdminCuration/>)
    await screen.findByText('Memória antes da aprovação')
    await user.click(screen.getByRole('tab',{name:'Preview de contexto'}))
    await user.selectOptions(screen.getByLabelText('Projeto'),project.id)
    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(`/projects/${project.id}`))
    await user.selectOptions(screen.getByLabelText('Pipeline run (opcional)'),'run-1')
    await user.click(screen.getByRole('button',{name:'Visualizar contexto'}))

    expect(await screen.findByDisplayValue('<superior_context>Contexto seguro</superior_context>')).toBeTruthy()
    expect(screen.getByText('Enforced')).toBeTruthy()
    expect(screen.queryByText('provider-test-secret')).toBeNull()
    const previewCall=mockAdminApi.mock.calls.find(([path])=>path==='/admin/agent-context/preview')
    expect(previewCall?.[0]).toBe('/admin/agent-context/preview')
    expect(previewCall?.[0]).not.toContain('Revise')
    expect(previewCall?.[1]).toEqual({
      method:'POST',
      body:JSON.stringify({
        agent_role:'researcher',project_id:'project-1',pipeline_run_id:'run-1',
        task:'Revise o contexto que seria usado na próxima execução.',
      }),
    })
  })

  it('clears the in-memory admin token when leaving the curation page',()=>{
    const view=render(<AdminCuration/>)
    view.unmount()
    expect(clearAdminToken).toHaveBeenCalledTimes(1)
  })
})
