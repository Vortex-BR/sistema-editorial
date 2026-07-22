import {FormEvent, useEffect, useState} from 'react'
import {Building2, Pencil, Plus, Save, Users} from 'lucide-react'
import {adminApi} from '../lib/api'

export type PublicationProfile={
  id:string
  name:string
  brand_name:string
  website_url:string|null
  segment:string
  brand_description:string
  mission:string|null
  value_proposition:string|null
  products_services:string[]
  audience_description:string
  audience_age_min:number|null
  audience_age_max:number|null
  audience_life_stage:string|null
  audience_knowledge_level:'beginner'|'intermediate'|'advanced'|'mixed'
  audience_goals:string[]
  audience_pain_points:string[]
  tone_of_voice:string
  brand_terms:string[]
  forbidden_terms:string[]
  primary_markets:string[]
  editorial_goals:string[]
  commercial_objective:string|null
  preferred_cta:string|null
  research_summary:string|null
  status:string
  version:number
}

const initialForm={
  name:'',
  brand_name:'',
  website_url:'',
  segment:'',
  brand_description:'',
  mission:'',
  value_proposition:'',
  products_services:'',
  audience_description:'',
  audience_age_min:'',
  audience_age_max:'',
  audience_life_stage:'',
  audience_knowledge_level:'mixed',
  audience_goals:'',
  audience_pain_points:'',
  tone_of_voice:'',
  brand_terms:'',
  forbidden_terms:'',
  primary_markets:'Brasil',
  editorial_goals:'',
  commercial_objective:'',
  preferred_cta:'',
  research_summary:'',
}

const lines=(value:string)=>value.split(/[\n,]/).map(item=>item.trim()).filter(Boolean)
const numberOrNull=(value:string)=>value===''?null:Number(value)

export function PublicationProfiles(){
  const [profiles,setProfiles]=useState<PublicationProfile[]>([])
  const [form,setForm]=useState(initialForm)
  const [creating,setCreating]=useState(false)
  const [editingId,setEditingId]=useState<string|null>(null)
  const [saving,setSaving]=useState(false)
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [success,setSuccess]=useState('')

  useEffect(()=>{
    adminApi<PublicationProfile[]>('/publication-profiles')
      .then(setProfiles)
      .catch(err=>setError((err as Error).message))
      .finally(()=>setLoading(false))
  },[])

  const update=(key:keyof typeof initialForm,value:string)=>setForm(current=>({...current,[key]:value}))

  function edit(profile:PublicationProfile){
    setEditingId(profile.id)
    setCreating(true)
    setSuccess('')
    setForm({
      name:profile.name,
      brand_name:profile.brand_name,
      website_url:profile.website_url||'',
      segment:profile.segment,
      brand_description:profile.brand_description,
      mission:profile.mission||'',
      value_proposition:profile.value_proposition||'',
      products_services:profile.products_services.join('\n'),
      audience_description:profile.audience_description,
      audience_age_min:profile.audience_age_min?.toString()||'',
      audience_age_max:profile.audience_age_max?.toString()||'',
      audience_life_stage:profile.audience_life_stage||'',
      audience_knowledge_level:profile.audience_knowledge_level,
      audience_goals:profile.audience_goals.join('\n'),
      audience_pain_points:profile.audience_pain_points.join('\n'),
      tone_of_voice:profile.tone_of_voice,
      brand_terms:profile.brand_terms.join('\n'),
      forbidden_terms:profile.forbidden_terms.join('\n'),
      primary_markets:profile.primary_markets.join('\n'),
      editorial_goals:profile.editorial_goals.join('\n'),
      commercial_objective:profile.commercial_objective||'',
      preferred_cta:profile.preferred_cta||'',
      research_summary:profile.research_summary||'',
    })
    window.scrollTo({top:0,behavior:'smooth'})
  }

  function cancel(){
    setCreating(false)
    setEditingId(null)
    setForm(initialForm)
  }

  async function submit(event:FormEvent){
    event.preventDefault()
    setSaving(true)
    setError('')
    setSuccess('')
    try{
      const payload={
        ...form,
        website_url:form.website_url||null,
        mission:form.mission||null,
        value_proposition:form.value_proposition||null,
        audience_age_min:numberOrNull(form.audience_age_min),
        audience_age_max:numberOrNull(form.audience_age_max),
        audience_life_stage:form.audience_life_stage||null,
        products_services:lines(form.products_services),
        audience_goals:lines(form.audience_goals),
        audience_pain_points:lines(form.audience_pain_points),
        brand_terms:lines(form.brand_terms),
        forbidden_terms:lines(form.forbidden_terms),
        primary_markets:lines(form.primary_markets),
        editorial_goals:lines(form.editorial_goals),
        commercial_objective:form.commercial_objective||null,
        preferred_cta:form.preferred_cta||null,
        research_summary:form.research_summary||null,
      }
      const saved=await adminApi<PublicationProfile>(
        editingId?`/publication-profiles/${editingId}`:'/publication-profiles',{
        method:editingId?'PUT':'POST',
        body:JSON.stringify(payload),
      })
      setProfiles(current=>editingId
        ?current.map(profile=>profile.id===saved.id?saved:profile)
        :[saved,...current])
      setForm(initialForm)
      setCreating(false)
      setEditingId(null)
      setSuccess(editingId
        ?'Perfil editorial atualizado. Novos runs usarão a nova versão; os antigos permanecem intactos.'
        :'Perfil editorial criado. Ele já pode orientar um novo conteúdo.')
    }catch(err){
      setError((err as Error).message)
    }finally{
      setSaving(false)
    }
  }

  return (
    <div className="page narrow">
      <div className="page-heading">
        <div>
          <span className="eyebrow">IDENTIDADE EDITORIAL</span>
          <h1>Perfis de marca e publicação</h1>
          <p>Cadastre uma vez quem publica, para quem escreve, como fala e qual papel comercial o conteúdo deve cumprir.</p>
        </div>
        {!creating&&<button className="button primary" onClick={()=>setCreating(true)}><Plus size={17}/>Criar perfil</button>}
      </div>

      {error&&<div className="notice error">{error}</div>}
      {success&&<div className="notice success">{success}</div>}

      {creating&&(
        <form className="project-form profile-form" onSubmit={submit}>
          <section className="panel form-section">
            <div className="section-number">01</div>
            <div className="section-copy"><h2>Marca e posicionamento</h2><p>Identidade, segmento e motivos para o leitor confiar na publicação.</p></div>
            <div className="form-fields">
              <div className="field-grid">
                <label>Nome do perfil<input required minLength={3} value={form.name} onChange={e=>update('name',e.target.value)} placeholder="Ex.: Blog principal da marca"/></label>
                <label>Nome da marca<input required minLength={2} value={form.brand_name} onChange={e=>update('brand_name',e.target.value)} placeholder="Ex.: Verde Vivo"/></label>
              </div>
              <div className="field-grid">
                <label>Site da marca<input type="url" value={form.website_url} onChange={e=>update('website_url',e.target.value)} placeholder="https://..."/></label>
                <label>Segmento<input required minLength={2} value={form.segment} onChange={e=>update('segment',e.target.value)} placeholder="Ex.: jardinagem e cultivo doméstico"/></label>
              </div>
              <label>Quem é a marca<textarea required minLength={10} rows={4} value={form.brand_description} onChange={e=>update('brand_description',e.target.value)} placeholder="História, especialidade, posicionamento e diferença real da marca."/></label>
              <div className="field-grid">
                <label>Missão<textarea rows={3} value={form.mission} onChange={e=>update('mission',e.target.value)}/></label>
                <label>Proposta de valor<textarea rows={3} value={form.value_proposition} onChange={e=>update('value_proposition',e.target.value)}/></label>
              </div>
              <label>Produtos ou serviços <small>Separe por vírgula ou linha</small><textarea rows={3} value={form.products_services} onChange={e=>update('products_services',e.target.value)}/></label>
            </div>
          </section>

          <section className="panel form-section">
            <div className="section-number">02</div>
            <div className="section-copy"><h2>Público real</h2><p>Ajuda a equipe a escolher profundidade, exemplos e vocabulário.</p></div>
            <div className="form-fields">
              <label>Descrição do público<textarea required minLength={3} rows={4} value={form.audience_description} onChange={e=>update('audience_description',e.target.value)} placeholder="Quem é, em que momento está e o que já sabe."/></label>
              <div className="field-grid age-grid">
                <label>Idade mínima<input type="number" min="0" max="120" value={form.audience_age_min} onChange={e=>update('audience_age_min',e.target.value)}/></label>
                <label>Idade máxima<input type="number" min="0" max="120" value={form.audience_age_max} onChange={e=>update('audience_age_max',e.target.value)}/></label>
              </div>
              <div className="field-grid">
                <label>Fase de vida<input value={form.audience_life_stage} onChange={e=>update('audience_life_stage',e.target.value)} placeholder="Ex.: jovem adulto, primeira casa"/></label>
                <label>Nível de conhecimento<select value={form.audience_knowledge_level} onChange={e=>update('audience_knowledge_level',e.target.value)}><option value="mixed">Misto</option><option value="beginner">Iniciante</option><option value="intermediate">Intermediário</option><option value="advanced">Avançado</option></select></label>
              </div>
              <div className="field-grid">
                <label>O que o público busca<textarea rows={3} value={form.audience_goals} onChange={e=>update('audience_goals',e.target.value)} placeholder="Um objetivo por linha"/></label>
                <label>Dores e objeções<textarea rows={3} value={form.audience_pain_points} onChange={e=>update('audience_pain_points',e.target.value)} placeholder="Uma dor por linha"/></label>
              </div>
            </div>
          </section>

          <section className="panel form-section">
            <div className="section-number">03</div>
            <div className="section-copy"><h2>Voz e estratégia</h2><p>Define como a marca ensina, vende e preserva sua identidade.</p></div>
            <div className="form-fields">
              <label>Tom de voz<textarea required minLength={3} rows={4} value={form.tone_of_voice} onChange={e=>update('tone_of_voice',e.target.value)} placeholder="Ex.: claro, próximo e experiente; explica termos sem infantilizar."/></label>
              <div className="field-grid">
                <label>Termos da marca<textarea rows={3} value={form.brand_terms} onChange={e=>update('brand_terms',e.target.value)} placeholder="Palavras preferidas"/></label>
                <label>Termos proibidos<textarea rows={3} value={form.forbidden_terms} onChange={e=>update('forbidden_terms',e.target.value)} placeholder="Clichês, promessas ou palavras a evitar"/></label>
              </div>
              <div className="field-grid">
                <label>Mercados principais<textarea rows={3} value={form.primary_markets} onChange={e=>update('primary_markets',e.target.value)}/></label>
                <label>Objetivos editoriais<textarea rows={3} value={form.editorial_goals} onChange={e=>update('editorial_goals',e.target.value)} placeholder="Educar, gerar confiança, captar demanda..."/></label>
              </div>
              <label>Objetivo comercial<textarea rows={3} value={form.commercial_objective} onChange={e=>update('commercial_objective',e.target.value)} placeholder="O que a publicação deve ajudar a vender, sem virar propaganda."/></label>
              <label>Chamada para ação preferida<textarea rows={2} value={form.preferred_cta} onChange={e=>update('preferred_cta',e.target.value)}/></label>
              <label>Resumo estratégico da marca<textarea rows={5} value={form.research_summary} onChange={e=>update('research_summary',e.target.value)} placeholder="Informações consolidadas e verificadas sobre posicionamento, mercado e diferenciais. Alegações factuais ainda serão pesquisadas por conteúdo."/></label>
            </div>
          </section>

          <div className="form-actions">
            <button type="button" className="button secondary" onClick={cancel}>Cancelar</button>
            <button disabled={saving} className="button primary large"><Save size={17}/>{saving?'Salvando...':editingId?'Salvar nova versão':'Salvar perfil editorial'}</button>
          </div>
        </form>
      )}

      {!creating&&(
        <section className="profile-list">
          {loading?<div className="loading">Carregando perfis...</div>:profiles.length===0?(
            <div className="panel empty">
              <div><Building2/></div>
              <h3>Nenhum perfil editorial criado</h3>
              <p>Crie o primeiro perfil para evitar repetir a identidade da marca em cada conteúdo.</p>
              <button className="button primary" onClick={()=>setCreating(true)}>Criar primeiro perfil</button>
            </div>
          ):profiles.map(profile=>(
            <article className="panel profile-card" key={profile.id}>
              <div className="profile-card-icon"><Building2/></div>
              <div>
                <span className="eyebrow">{profile.segment}</span>
                <h2>{profile.name}</h2>
                <strong>{profile.brand_name}</strong>
                <p>{profile.brand_description}</p>
                <div className="profile-meta"><span><Users size={14}/>{profile.audience_description}</span><span>Versão {profile.version}</span></div>
                <button className="button secondary compact profile-edit" onClick={()=>edit(profile)}><Pencil size={14}/>Editar perfil</button>
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  )
}
