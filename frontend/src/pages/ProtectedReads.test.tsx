// @vitest-environment jsdom
import {cleanup,render,screen,waitFor,within} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {adminApi} from '../lib/api'
import {Config} from './Config'
import {Dashboard} from './Dashboard'

vi.mock('../lib/api',()=>({
  adminApi:vi.fn(),
  clearAdminToken:vi.fn(),
}))

const mockAdminApi=vi.mocked(adminApi)

beforeEach(()=>{
  vi.clearAllMocks()
  mockAdminApi.mockImplementation(async path=>{
    if(path==='/dashboard'){
      return {
        stats:{total_projects:0,completed:0,failed_runs:0,cancelled_runs:0,approved_facts:0,distinct_sources:0,total_cost_usd:0},
        recent_projects:[],
      } as never
    }
    if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    if(path==='/config/credentials') return [] as never
    if(path==='/config'){
      return {
        routes:[],
        skills:[],
        policy:{learned_skill_stability_threshold:3,auto_inject_unstable_skills:false},
      } as never
    }
    throw new Error(`unexpected request: ${path}`)
  })
})

afterEach(cleanup)

describe('protected business reads',()=>{
  it('loads the dashboard through adminApi',async()=>{
    render(<MemoryRouter><Dashboard/></MemoryRouter>)

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith('/dashboard'))
  })

  it('loads credentials and configuration through adminApi',async()=>{
    render(<Config/>)

    await waitFor(()=>{
      expect(mockAdminApi).toHaveBeenCalledWith('/config/credentials')
      expect(mockAdminApi).toHaveBeenCalledWith('/config')
    })
  })

  it('checks and repairs the pipeline version selected by the administrator',async()=>{
    const user=userEvent.setup()
    render(<Config/>)

    const selector=await screen.findByLabelText('Pipeline do pré-voo')
    await user.selectOptions(selector,'v2')

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/execution-preflight?pipeline_version=v2',
    ))
    await user.click(screen.getByRole('button',{name:'Verificar e corrigir'}))
    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/execution-preflight?pipeline_version=v2&repair=true',
    ))
  })

  it('verifies a configured Gemini credential explicitly',async()=>{
    const user=userEvent.setup()
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    if(path==='/config/credentials') return [{
        provider:'gemini',
        configured:true,
        last_four:'1234',
        verified_at:null,
      }] as never
      if(path==='/config') return {
        routes:[{
          agent_role:'planner',
          primary_provider:'gemini',
          primary_model:'gemini-3.5-flash',
          fallback_provider:null,
          fallback_model:null,
        }],
        skills:[],
        policy:{learned_skill_stability_threshold:3,auto_inject_unstable_skills:false},
      } as never
      if(path==='/config/credentials/gemini/verify'){
        expect(init).toEqual({method:'POST'})
        return {
          provider:'gemini',
          verified:true,
          verified_at:'2026-07-15T12:00:00Z',
          latency_ms:12,
          model:'gemini-3.5-flash',
          error_code:null,
        } as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<Config/>)

    const geminiInput=await screen.findByLabelText('Credencial Google Gemini')
    const geminiForm=geminiInput.closest('form')
    if(!geminiForm) throw new Error('Gemini credential form missing')
    await user.click(within(geminiForm).getByRole('button',{name:'Verificar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/credentials/gemini/verify',
      {method:'POST'},
    ))
  })

  it('verifies a configured OpenAI credential with gpt-4o-mini',async()=>{
    const user=userEvent.setup()
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    if(path==='/config/credentials') return [{
        provider:'openai',
        configured:true,
        last_four:'5678',
        verified_at:null,
      }] as never
      if(path==='/config') return {
        routes:[{
          agent_role:'planner',
          primary_provider:'openai',
          primary_model:'gpt-4o-mini',
          fallback_provider:null,
          fallback_model:null,
        }],
        skills:[],
        policy:{learned_skill_stability_threshold:3,auto_inject_unstable_skills:false},
      } as never
      if(path==='/config/credentials/openai/verify'){
        expect(init).toEqual({method:'POST'})
        return {
          provider:'openai',
          verified:true,
          verified_at:'2026-07-15T12:00:00Z',
          latency_ms:10,
          model:'gpt-4o-mini',
          error_code:null,
        } as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<Config/>)

    const openaiInput=await screen.findByLabelText('Credencial OpenAI')
    const openaiForm=openaiInput.closest('form')
    if(!openaiForm) throw new Error('OpenAI credential form missing')
    await user.click(within(openaiForm).getByRole('button',{name:'Verificar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/credentials/openai/verify',
      {method:'POST'},
    ))
  })

  it('verifies a configured Anthropic credential explicitly',async()=>{
    const user=userEvent.setup()
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    if(path==='/config/credentials') return [{
        provider:'anthropic',
        configured:true,
        last_four:'9012',
        verified_at:null,
      }] as never
      if(path==='/config') return {
        routes:[],
        route_defaults:{},
        skills:[],
        policy:{learned_skill_stability_threshold:3,auto_inject_unstable_skills:false},
      } as never
      if(path==='/config/credentials/anthropic/verify'){
        expect(init).toEqual({method:'POST'})
        return {
          provider:'anthropic',
          verified:true,
          verified_at:'2026-07-17T12:00:00Z',
          latency_ms:15,
          model:'claude-sonnet-5',
          error_code:null,
        } as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<Config/>)

    const input=await screen.findByLabelText('Credencial Anthropic')
    const form=input.closest('form')
    if(!form) throw new Error('Anthropic credential form missing')
    await user.click(within(form).getByRole('button',{name:'Verificar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/credentials/anthropic/verify',
      {method:'POST'},
    ))
  })

  it('preserves persisted provider parameters when saving an unchanged route',async()=>{
    const user=userEvent.setup()
    const parameters={
      max_output_tokens:4096,
      timeout_seconds:180,
      max_retries:2,
      input_cost_per_million:3,
      output_cost_per_million:15,
    }
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    if(path==='/config/credentials') return [] as never
      if(path==='/config') return {
        routes:[{
          agent_role:'planner',
          primary_provider:'anthropic',
          primary_model:'claude-sonnet-5',
          fallback_provider:null,
          fallback_model:null,
          parameters,
        }],
        route_defaults:{},
        skills:[],
        policy:{learned_skill_stability_threshold:3,auto_inject_unstable_skills:false},
      } as never
      if(path==='/config/routes/planner'){
        const body=JSON.parse(String(init?.body))
        expect(body.parameters).toEqual(parameters)
        expect(body.primary_provider).toBe('anthropic')
        return {saved:true} as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<Config/>)

    const select=await screen.findByLabelText('Provedor de planner')
    const row=select.closest('.route-row')
    if(!row) throw new Error('Planner route row missing')
    await user.click(within(row as HTMLElement).getByRole('button',{name:'Salvar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/routes/planner',
      expect.objectContaining({method:'PUT'}),
    ))
  })

  it('loads provider-safe server defaults when switching a route provider',async()=>{
    const user=userEvent.setup()
    const anthropicDefault={
      agent_role:'planner',
      primary_provider:'anthropic',
      primary_model:'claude-sonnet-5',
      fallback_provider:null,
      fallback_model:null,
      parameters:{
        max_output_tokens:4096,
        timeout_seconds:90,
        max_retries:2,
        input_cost_per_million:3,
        output_cost_per_million:15,
      },
    }
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    if(path==='/config/credentials') return [] as never
      if(path==='/config') return {
        routes:[],
        route_defaults:{anthropic:{planner:anthropicDefault}},
        skills:[],
        policy:{learned_skill_stability_threshold:3,auto_inject_unstable_skills:false},
      } as never
      if(path==='/config/routes/planner'){
        const body=JSON.parse(String(init?.body))
        expect(body).toMatchObject(anthropicDefault)
        expect(body.parameters).not.toHaveProperty('reasoning_effort')
        expect(body.parameters.input_cost_per_million).toBe(3)
        return {saved:true} as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<Config/>)

    const select=await screen.findByLabelText('Provedor de planner')
    await user.selectOptions(select,'anthropic')
    expect((screen.getByLabelText('Modelo de planner') as HTMLInputElement).value).toBe('claude-sonnet-5')
    const row=select.closest('.route-row')
    if(!row) throw new Error('Planner route row missing')
    await user.click(within(row as HTMLElement).getByRole('button',{name:'Salvar'}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/config/routes/planner',
      expect.objectContaining({method:'PUT'}),
    ))
  })

})
