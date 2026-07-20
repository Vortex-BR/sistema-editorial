// @vitest-environment jsdom
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {adminApi} from '../lib/api'
import {NewProject} from './NewProject'
import {PublicationProfiles} from './PublicationProfiles'

vi.mock('../lib/api',()=>({adminApi:vi.fn()}))
const mockAdminApi=vi.mocked(adminApi)

const profile={
  id:'profile-1',
  name:'Blog principal',
  brand_name:'Marca Exemplo',
  website_url:null,
  segment:'telecomunicações',
  brand_description:'Marca que explica serviços digitais para consumidores.',
  mission:null,
  value_proposition:null,
  products_services:['planos de internet'],
  audience_description:'Adultos iniciantes',
  audience_age_min:25,
  audience_age_max:45,
  audience_life_stage:'vida adulta',
  audience_knowledge_level:'beginner' as const,
  audience_goals:['aprender'],
  audience_pain_points:['insegurança'],
  tone_of_voice:'claro e próximo',
  brand_terms:[],
  forbidden_terms:[],
  primary_markets:['Brasil'],
  editorial_goals:['educar'],
  commercial_objective:'apresentar o catálogo',
  preferred_cta:'conhecer os produtos',
  research_summary:null,
  status:'active',
  version:1,
}

beforeEach(()=>{
  vi.clearAllMocks()
  mockAdminApi.mockImplementation(async path=>{
    if(path==='/publication-profiles') return [profile] as never
    if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
    throw new Error(`unexpected request: ${path}`)
  })
})

afterEach(cleanup)

describe('editorial profiles and brief',()=>{
  it('loads a reusable profile and applies its reader context',async()=>{
    render(<MemoryRouter><NewProject/></MemoryRouter>)

    const select=await screen.findByLabelText('Perfil editorial')
    await userEvent.selectOptions(select,'profile-1')

    expect((screen.getByLabelText('Público-alvo') as HTMLInputElement).value).toBe('Adultos iniciantes')
    expect((screen.getByLabelText('Segmento específico') as HTMLInputElement).value).toBe('telecomunicações')
    expect((screen.getByLabelText('Idade mínima') as HTMLInputElement).value).toBe('25')
    expect(screen.getByText('Marca Exemplo')).toBeTruthy()
  })

  it('shows and enforces the long-form editorial context limit',async()=>{
    render(<MemoryRouter><NewProject/></MemoryRouter>)

    const context=await screen.findByLabelText(/Contexto adicional/)
    expect(context.getAttribute('maxlength')).toBe('20000')
    expect(screen.getByText('0 / 20.000 caracteres')).toBeTruthy()
  })

  it('submits the rich brief together with the fixed profile id',async()=>{
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path==='/publication-profiles') return [profile] as never
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v2',status:'ready',dependencies:[],repairs:[]} as never
      if(path==='/projects'){
        const body=JSON.parse(String(init?.body))
        expect(body.publication_profile_id).toBe('profile-1')
        expect(body.briefing.primary_keyword).toBe('cultivo em casa')
        expect(body.briefing.reader_goal).toBe('Começar com segurança')
        expect(body.briefing.secondary_keywords).toEqual(['mudas','substrato'])
        return {id:'project-1',pipeline_run_id:'run-v2',dispatch_status:'published'} as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<MemoryRouter><NewProject/></MemoryRouter>)
    await userEvent.selectOptions(await screen.findByLabelText('Perfil editorial'),'profile-1')
    await userEvent.selectOptions(screen.getByLabelText('Versão do pipeline editorial'),'v2')
    fireEvent.change(screen.getByLabelText('Nome do projeto'),{target:{value:'Guia de cultivo'}})
    fireEvent.change(screen.getByLabelText('Tópico principal'),{target:{value:'Como iniciar um cultivo doméstico'}})
    fireEvent.change(screen.getByLabelText('Objetivo do conteúdo'),{target:{value:'Ensinar o processo do começo ao fim'}})
    fireEvent.change(screen.getByLabelText('Palavra-chave principal'),{target:{value:'cultivo em casa'}})
    fireEvent.change(screen.getByLabelText(/Palavras-chave relacionadas/),{target:{value:'mudas, substrato'}})
    fireEvent.change(screen.getByLabelText('Contexto do leitor'),{target:{value:'Nunca cultivou e está escolhendo os materiais'}})
    fireEvent.change(screen.getByLabelText('O que o leitor busca'),{target:{value:'Começar com segurança'}})

    await userEvent.click(screen.getByRole('button',{name:/Criar e iniciar V2/}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects',
      expect.objectContaining({method:'POST'}),
    ))
  })

  it('creates and starts V3 with an ordered knowledge-contract brief',async()=>{
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path==='/publication-profiles') return [profile] as never
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
      if(path==='/projects'){
        const body=JSON.parse(String(init?.body))
        expect(body.editorial_pipeline_version).toBe('v3')
        expect(body.start_immediately).toBe(true)
        expect(body.briefing.editorial_content_type).toBe('procedural_decision_guide')
        expect(body.briefing.reader_start_state).toContain('óleo')
        expect(body.briefing.reader_final_state).toContain('nível')
        expect(body.briefing.required_methods).toEqual(['drenagem pelo cárter','extração por sucção'])
        expect(body.briefing.required_approach_type).toBe('method')
        expect(body.briefing.requires_method_comparison).toBe(true)
        expect(body.briefing.requires_external_reference_per_method).toBe(true)
        return {id:'project-v3',pipeline_run_id:'run-v3',dispatch_status:'published'} as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<MemoryRouter><NewProject/></MemoryRouter>)
    await userEvent.selectOptions(await screen.findByLabelText('Perfil editorial'),'profile-1')
    await userEvent.selectOptions(screen.getByLabelText('Versão do pipeline editorial'),'v3')
    fireEvent.change(screen.getByLabelText('Nome do projeto'),{target:{value:'Guia procedural V3'}})
    fireEvent.change(screen.getByLabelText('Tópico principal'),{target:{value:'Troca de óleo do carro passo a passo'}})
    fireEvent.change(screen.getByLabelText('Objetivo do conteúdo'),{target:{value:'Ensinar a sequência completa até o resultado final'}})
    fireEvent.change(screen.getByLabelText('Palavra-chave principal'),{target:{value:'como trocar o óleo do carro'}})
    fireEvent.change(screen.getByLabelText('Contexto do leitor'),{target:{value:'Precisa trocar o óleo e compreender o procedimento antes de começar'}})
    fireEvent.change(screen.getByLabelText('O que o leitor busca'),{target:{value:'Escolher a abordagem e verificar o resultado'}})
    await userEvent.selectOptions(screen.getByLabelText(/Arquitetura editorial/),'procedural_decision_guide')
    await userEvent.selectOptions(screen.getByLabelText(/Dimensão das abordagens/),'method')
    fireEvent.change(screen.getByLabelText(/Abordagens obrigatórias/),{target:{value:'drenagem pelo cárter, extração por sucção'}})
    fireEvent.change(screen.getByLabelText('Estado inicial do leitor'),{target:{value:'Leitor que precisa compreender o óleo antes de escolher a abordagem'}})
    fireEvent.change(screen.getByLabelText('Estado final observável'),{target:{value:'Leitor capaz de confirmar nível correto e ausência de vazamentos'}})
    fireEvent.change(screen.getByLabelText('Promessa editorial'),{target:{value:'Explicar a base, comparar os métodos, orientar a escolha e acompanhar até o resultado'}})
    fireEvent.change(screen.getByLabelText('Limite do escopo'),{target:{value:'Terminar na verificação do nível e não avançar para outros serviços'}})

    expect((screen.getByLabelText(/Iniciar Editorial V3 após criar/) as HTMLInputElement).checked).toBe(true)
    await userEvent.click(screen.getByRole('button',{name:/Criar e iniciar V3/}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/projects',
      expect.objectContaining({method:'POST'}),
    ))
  })

  it('applies the Maconha Seeds Bank campaign and selects its matching profile',async()=>{
    const msbProfile={
      ...profile,
      id:'profile-msb',
      name:'MSB Blog — Maconha Seeds Bank',
      brand_name:'Maconha Seeds Bank',
      segment:'semente de maconha',
    }
    mockAdminApi.mockImplementation(async path=>{
      if(path==='/publication-profiles') return [msbProfile] as never
      throw new Error(`unexpected request: ${path}`)
    })
    render(<MemoryRouter><NewProject/></MemoryRouter>)

    await screen.findByLabelText('Perfil editorial')
    await userEvent.click(screen.getByRole('button',{name:'Aplicar campanha'}))

    expect((screen.getByLabelText('Perfil editorial') as HTMLSelectElement).value).toBe('profile-msb')
    expect((screen.getByLabelText('Nome do projeto') as HTMLInputElement).value).toContain('germinação de sementes de cannabis')
    expect((screen.getByLabelText('Palavra-chave principal') as HTMLInputElement).value).toBe('como germinar semente de cannabis no papel-toalha')
    expect((screen.getByLabelText(/Contexto adicional/) as HTMLTextAreaElement).value).toContain('20. Quais são os erros que mais causam perda de sementes?')
    expect((screen.getByLabelText(/Iniciar Editorial V3 após criar/) as HTMLInputElement).checked).toBe(true)
  })

  it('blocks project creation with actionable dependencies before an orphan project is created',async()=>{
    const msbProfile={
      ...profile,
      id:'profile-msb',
      name:'MSB Blog — Maconha Seeds Bank',
      brand_name:'Maconha Seeds Bank',
    }
    mockAdminApi.mockImplementation(async path=>{
      if(path==='/publication-profiles') return [msbProfile] as never
      if(path.startsWith('/config/execution-preflight')) return {
        pipeline_version:'v3',
        status:'not_ready',
        dependencies:['model_route:fact_checker','credential:search:unverified'],
        repairs:[],
      } as never
      throw new Error(`unexpected request: ${path}`)
    })
    render(<MemoryRouter><NewProject/></MemoryRouter>)
    await screen.findByLabelText('Perfil editorial')
    await userEvent.click(screen.getByRole('button',{name:'Aplicar campanha'}))

    await userEvent.click(screen.getByRole('button',{name:/Criar e iniciar V3/}))

    expect(await screen.findByText(/model_route:fact_checker/)).toBeTruthy()
    expect(mockAdminApi).not.toHaveBeenCalledWith('/projects',expect.anything())
  })

  it('reuses the same idempotency key when a completed request response is lost',async()=>{
    const msbProfile={
      ...profile,
      id:'profile-msb',
      name:'MSB Blog — Maconha Seeds Bank',
      brand_name:'Maconha Seeds Bank',
    }
    const keys:string[]=[]
    let projectAttempts=0
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path==='/publication-profiles') return [msbProfile] as never
      if(path.startsWith('/config/execution-preflight')) return {pipeline_version:'v3',status:'ready',dependencies:[],repairs:[]} as never
      if(path==='/projects'){
        keys.push(new Headers(init?.headers).get('Idempotency-Key')||'')
        projectAttempts+=1
        if(projectAttempts===1) throw new Error('A resposta da primeira tentativa foi perdida')
        return {id:'project-v3',pipeline_run_id:'run-v3',dispatch_status:'sent'} as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<MemoryRouter><NewProject/></MemoryRouter>)
    await screen.findByLabelText('Perfil editorial')
    await userEvent.click(screen.getByRole('button',{name:'Aplicar campanha'}))

    await userEvent.click(screen.getByRole('button',{name:/Criar e iniciar V3/}))
    expect(await screen.findByText(/resposta da primeira tentativa foi perdida/i)).toBeTruthy()
    await userEvent.click(screen.getByRole('button',{name:/Criar e iniciar V3/}))

    await waitFor(()=>expect(projectAttempts).toBe(2))
    expect(keys[0]).not.toBe('')
    expect(keys[1]).toBe(keys[0])
  })

  it('creates a profile with structured list fields',async()=>{
    mockAdminApi.mockImplementation(async(path,init)=>{
      if(path==='/publication-profiles'&&!init) return [] as never
      if(path==='/publication-profiles'&&init){
        const body=JSON.parse(String(init.body))
        expect(body.products_services).toEqual(['consultoria','curso'])
        return {...profile,...body,id:'profile-2'} as never
      }
      throw new Error(`unexpected request: ${path}`)
    })
    render(<PublicationProfiles/>)
    await userEvent.click(await screen.findByRole('button',{name:'Criar primeiro perfil'}))
    fireEvent.change(screen.getByLabelText('Nome do perfil'),{target:{value:'Perfil institucional'}})
    fireEvent.change(screen.getByLabelText('Nome da marca'),{target:{value:'Marca Nova'}})
    fireEvent.change(screen.getByLabelText('Segmento'),{target:{value:'educação'}})
    fireEvent.change(screen.getByLabelText('Quem é a marca'),{target:{value:'Uma marca brasileira dedicada à educação prática.'}})
    fireEvent.change(screen.getByLabelText('Descrição do público'),{target:{value:'Adultos em formação profissional'}})
    fireEvent.change(screen.getByLabelText('Tom de voz'),{target:{value:'Didático, humano e direto'}})
    fireEvent.change(screen.getByLabelText(/Produtos ou serviços/),{target:{value:'consultoria, curso'}})

    await userEvent.click(screen.getByRole('button',{name:/Salvar perfil editorial/}))

    await waitFor(()=>expect(mockAdminApi).toHaveBeenCalledWith(
      '/publication-profiles',
      expect.objectContaining({method:'POST'}),
    ))
  })
})
