import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Building2,
  SearchCheck,
  ShieldCheck,
  Users,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { adminApi } from "../lib/api";
import { CAMPAIGN_PRESETS } from "../lib/campaignPresets";
import type { PublicationProfile } from "./PublicationProfiles";

type FormState = {
  name: string;
  topic: string;
  search_intent: string;
  audience: string;
  language: string;
  niche: string;
  publication_profile_id: string;
  editorial_pipeline_version: string;
  start_immediately: boolean;
  content_objective: string;
  primary_keyword: string;
  research_subject: string;
  secondary_keywords: string;
  segment: string;
  reader_context: string;
  reader_age_min: string;
  reader_age_max: string;
  reader_life_stage: string;
  reader_knowledge_level: string;
  reader_goal: string;
  commercial_objective: string;
  offer: string;
  desired_action: string;
  additional_context: string;
  required_methods: string;
  required_approach_type: string;
  editorial_content_type: string;
  reader_start_state: string;
  reader_final_state: string;
  article_promise: string;
  scope_limit: string;
  requires_method_comparison: boolean;
  requires_external_reference_per_method: boolean;
};

const initialForm: FormState = {
  name: "",
  topic: "",
  search_intent: "informational",
  audience: "",
  language: "pt-BR",
  niche: "",
  publication_profile_id: "",
  editorial_pipeline_version: "v3",
  start_immediately: true,
  content_objective: "",
  primary_keyword: "",
  research_subject: "",
  secondary_keywords: "",
  segment: "",
  reader_context: "",
  reader_age_min: "",
  reader_age_max: "",
  reader_life_stage: "",
  reader_knowledge_level: "mixed",
  reader_goal: "",
  commercial_objective: "",
  offer: "",
  desired_action: "",
  additional_context: "",
  required_methods: "",
  required_approach_type: "method",
  editorial_content_type: "explanatory_guide",
  reader_start_state: "",
  reader_final_state: "",
  article_promise: "",
  scope_limit: "",
  requires_method_comparison: false,
  requires_external_reference_per_method: false,
};

const keywords = (value: string) =>
  value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
const age = (value: string) => (value === "" ? null : Number(value));
const ADDITIONAL_CONTEXT_MAX_LENGTH = 20_000;
const TOPIC_MAX_LENGTH = 380;
const READER_STATE_MAX_LENGTH = 1_000;
const ARTICLE_PROMISE_MAX_LENGTH = 3_000;
const SCOPE_LIMIT_MAX_LENGTH = 2_000;
const RESEARCH_SUBJECT_MAX_LENGTH = 1_000;

export function NewProject() {
  const nav = useNavigate();
  const [form, setForm] = useState<FormState>(initialForm);
  const [profiles, setProfiles] = useState<PublicationProfile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState(
    CAMPAIGN_PRESETS[0]?.id || "",
  );
  const submissionIdentity = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const selectedProfile = useMemo(
    () =>
      profiles.find((profile) => profile.id === form.publication_profile_id),
    [profiles, form.publication_profile_id],
  );

  useEffect(() => {
    adminApi<PublicationProfile[]>("/publication-profiles")
      .then(setProfiles)
      .catch((err) => setError((err as Error).message))
      .finally(() => setProfilesLoading(false));
  }, []);

  const update = (key: keyof FormState, value: string | boolean) => {
    submissionIdentity.current = null;
    setForm((current) => ({ ...current, [key]: value }));
  };

  function selectProfile(profileId: string) {
    submissionIdentity.current = null;
    const profile = profiles.find((item) => item.id === profileId);
    setForm((current) => ({
      ...current,
      publication_profile_id: profileId,
      audience: current.audience || profile?.audience_description || "",
      niche: current.niche || profile?.segment || "",
      segment: current.segment || profile?.segment || "",
      reader_age_min:
        current.reader_age_min || profile?.audience_age_min?.toString() || "",
      reader_age_max:
        current.reader_age_max || profile?.audience_age_max?.toString() || "",
      reader_life_stage:
        current.reader_life_stage || profile?.audience_life_stage || "",
      reader_knowledge_level:
        current.reader_knowledge_level === "mixed"
          ? profile?.audience_knowledge_level || "mixed"
          : current.reader_knowledge_level,
      commercial_objective:
        current.commercial_objective || profile?.commercial_objective || "",
      desired_action: current.desired_action || profile?.preferred_cta || "",
    }));
  }

  function applyCampaignPreset() {
    submissionIdentity.current = null;
    const preset = CAMPAIGN_PRESETS.find(
      (item) => item.id === selectedPresetId,
    );
    if (!preset) return;
    const normalizedBrand = preset.preferredBrand.toLocaleLowerCase("pt-BR");
    const preferredProfile = profiles.find(
      (profile) =>
        profile.brand_name.toLocaleLowerCase("pt-BR") === normalizedBrand ||
        profile.name.toLocaleLowerCase("pt-BR").includes(normalizedBrand),
    );
    setForm((current) => ({
      ...current,
      ...(preset.values as Partial<FormState>),
      publication_profile_id:
        preferredProfile?.id || current.publication_profile_id,
    }));
    setError(
      preferredProfile
        ? ""
        : `A campanha foi aplicada. Selecione manualmente o perfil ${preset.preferredBrand}.`,
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (form.start_immediately) {
        const preflight = await adminApi<{
          status: string;
          dependencies: string[];
        }>(
          `/config/execution-preflight?pipeline_version=${encodeURIComponent(form.editorial_pipeline_version)}&repair=true`,
        );
        if (preflight.status !== "ready") {
          throw new Error(
            `Dependências da execução incompletas: ${preflight.dependencies.join(", ")}`,
          );
        }
      }
      const payload = {
        name: form.name,
        topic: form.topic,
        search_intent: form.search_intent,
        audience: form.audience,
        language: form.language,
        niche: form.niche || null,
        publication_profile_id: form.publication_profile_id || null,
        editorial_pipeline_version: form.editorial_pipeline_version,
        start_immediately: form.start_immediately,
        briefing: {
          content_objective: form.content_objective,
          primary_keyword: form.primary_keyword,
          research_subject: form.research_subject,
          secondary_keywords: keywords(form.secondary_keywords),
          segment: form.segment,
          reader_context: form.reader_context,
          reader_age_min: age(form.reader_age_min),
          reader_age_max: age(form.reader_age_max),
          reader_life_stage: form.reader_life_stage,
          reader_knowledge_level: form.reader_knowledge_level,
          reader_goal: form.reader_goal,
          commercial_objective: form.commercial_objective,
          offer: form.offer,
          desired_action: form.desired_action,
          additional_context: form.additional_context,
          required_methods: keywords(form.required_methods),
          required_approach_type: form.required_approach_type,
          editorial_content_type: form.editorial_content_type,
          reader_start_state: form.reader_start_state,
          reader_final_state: form.reader_final_state,
          article_promise: form.article_promise,
          scope_limit: form.scope_limit,
          requires_method_comparison: form.requires_method_comparison,
          requires_external_reference_per_method:
            form.requires_external_reference_per_method,
        },
      };
      const fingerprint = JSON.stringify(payload);
      if (submissionIdentity.current?.fingerprint !== fingerprint) {
        submissionIdentity.current = {
          fingerprint,
          key:
            globalThis.crypto?.randomUUID?.() ||
            `project-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        };
      }
      const project = await adminApi<{
        id: string;
        pipeline_run_id?: string | null;
        dispatch_status?: string | null;
        start_requested?: boolean;
      }>("/projects", {
        method: "POST",
        headers: { "Idempotency-Key": submissionIdentity.current.key },
        body: fingerprint,
      });
      if (form.start_immediately && !project.pipeline_run_id) {
        throw new Error(
          "O projeto foi criado, mas nenhuma execução foi registrada. Abra o projeto e inicie o pipeline novamente.",
        );
      }
      nav(`/projetos/${project.id}`, {
        state: { dispatchStatus: project.dispatch_status || null },
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page narrow">
      <Link to="/" className="back">
        <ArrowLeft size={16} />
        Voltar ao painel
      </Link>
      <div className="page-heading">
        <div>
          <span className="eyebrow">NOVO CONTEÚDO</span>
          <h1>Dê à equipe um briefing de verdade.</h1>
          <p>
            Quanto melhor o contexto editorial, mais precisa será a pesquisa e
            mais natural, útil e coerente será a redação.
          </p>
        </div>
      </div>
      <form className="project-form" onSubmit={submit}>
        <section className="panel campaign-preset-panel">
          <div>
            <span className="eyebrow">CAMPANHA PRÉ-CONFIGURADA</span>
            <h2>Preencha o briefing completo em um clique.</h2>
            <p>Os campos continuam editáveis antes da criação.</p>
          </div>
          <div className="campaign-preset-actions">
            <label>
              Campanha
              <select
                aria-label="Campanha pré-configurada"
                value={selectedPresetId}
                onChange={(event) => setSelectedPresetId(event.target.value)}
              >
                {CAMPAIGN_PRESETS.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="button secondary"
              onClick={applyCampaignPreset}
            >
              Aplicar campanha
            </button>
          </div>
          <small>
            {
              CAMPAIGN_PRESETS.find((item) => item.id === selectedPresetId)
                ?.description
            }
          </small>
        </section>
        <section className="panel form-section">
          <div className="section-number">01</div>
          <div className="section-copy">
            <h2>Marca responsável</h2>
            <p>A identidade será reutilizada e fixada nesta execução.</p>
          </div>
          <div className="form-fields">
            <label>
              Perfil editorial
              <select
                required
                disabled={profilesLoading}
                value={form.publication_profile_id}
                onChange={(e) => selectProfile(e.target.value)}
              >
                <option value="">
                  {profilesLoading
                    ? "Carregando perfis..."
                    : "Selecione a marca que publicará"}
                </option>
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} — {profile.brand_name}
                  </option>
                ))}
              </select>
            </label>
            {selectedProfile && (
              <div className="profile-selection">
                <Building2 size={18} />
                <div>
                  <strong>{selectedProfile.brand_name}</strong>
                  <span>
                    {selectedProfile.segment} · {selectedProfile.tone_of_voice}
                  </span>
                </div>
              </div>
            )}
            {!profilesLoading && profiles.length === 0 && (
              <div className="notice error">
                Nenhum perfil disponível.{" "}
                <Link to="/perfis">
                  Crie o perfil editorial da marca primeiro.
                </Link>
              </div>
            )}
            <Link className="text-link" to="/perfis">
              <Building2 size={15} />
              Criar ou consultar perfis editoriais
            </Link>
          </div>
        </section>

        <section className="panel form-section">
          <div className="section-number">02</div>
          <div className="section-copy">
            <h2>Escopo do conteúdo</h2>
            <p>
              Defina a entrega e a transformação esperada, não apenas um assunto
              solto.
            </p>
          </div>
          <div className="form-fields">
            <label>
              Versão do pipeline editorial
              <select
                value={form.editorial_pipeline_version}
                onChange={(e) =>
                  update("editorial_pipeline_version", e.target.value)
                }
              >
                <option value="v2">V2 — produção atual</option>
                <option value="v3">
                  V3 — pesquisa estruturada e arquitetura universal
                </option>
              </select>
            </label>
            {form.editorial_pipeline_version === "v3" && (
              <div className="notice">
                <strong>Editorial Intelligence V3.</strong> A execução usa
                arquitetura hierárquica antes da pesquisa, triangulação, dossiês
                por nó e três revisões antes da aprovação. Regras procedimentais
                só são ativadas quando o tipo escolhido realmente exige métodos.
              </div>
            )}
            <label>
              Nome do projeto
              <input
                required
                minLength={3}
                placeholder="Ex.: Como escolher um plano de internet"
                value={form.name}
                onChange={(e) => update("name", e.target.value)}
              />
            </label>
            <label>
              Tópico principal
              <textarea
                aria-label="Tópico principal"
                required
                minLength={3}
                maxLength={TOPIC_MAX_LENGTH}
                rows={4}
                placeholder="Qual problema central o artigo precisa resolver?"
                value={form.topic}
                onChange={(e) => update("topic", e.target.value)}
              />
              <small aria-hidden="true">
                {form.topic.length.toLocaleString("pt-BR")} /{" "}
                {TOPIC_MAX_LENGTH.toLocaleString("pt-BR")} caracteres
              </small>
            </label>
            <label>
              Objetivo do conteúdo
              <textarea
                required
                minLength={10}
                rows={3}
                placeholder="O que o leitor deve compreender ou conseguir decidir ao terminar?"
                value={form.content_objective}
                onChange={(e) => update("content_objective", e.target.value)}
              />
            </label>
            <div className="field-grid">
              <label>
                Intenção de busca
                <select
                  value={form.search_intent}
                  onChange={(e) => update("search_intent", e.target.value)}
                >
                  <option value="informational">Informacional</option>
                  <option value="commercial">Investigação comercial</option>
                  <option value="transactional">Transacional</option>
                  <option value="navigational">Navegacional</option>
                </select>
              </label>
              <label>
                Segmento específico
                <input
                  required
                  minLength={2}
                  placeholder="Ex.: jardinagem e botânica"
                  value={form.segment}
                  onChange={(e) => update("segment", e.target.value)}
                />
              </label>
            </div>
            <label>
              Arquitetura editorial
              <select
                value={form.editorial_content_type}
                onChange={(e) => {
                  const value = e.target.value;
                  setForm((current) => ({
                    ...current,
                    editorial_content_type: value,
                    requires_method_comparison:
                      value === "procedural_decision_guide",
                    requires_external_reference_per_method:
                      value === "procedural_decision_guide",
                  }));
                }}
              >
                <option value="explanatory_guide">Guia explicativo</option>
                <option value="procedural_decision_guide">
                  Guia procedural com decisão
                </option>
                <option value="procedural_how_to">
                  Guia passo a passo (caminho único)
                </option>
                <option value="comparison">Comparação</option>
                <option value="troubleshooting">Solução de problemas</option>
                <option value="commercial_education">Educação comercial</option>
              </select>
              <small>
                Define a progressão lógica antes da pesquisa nos pipelines V2 e
                V3.
              </small>
            </label>
            {form.editorial_pipeline_version === "v3" && (
              <div className="v3-contract-fields">
                {form.editorial_content_type ===
                  "procedural_decision_guide" && (
                  <>
                    <label>
                      Dimensão das abordagens
                      <select
                        value={form.required_approach_type}
                        onChange={(e) =>
                          update("required_approach_type", e.target.value)
                        }
                      >
                        <option value="method">Métodos</option>
                        <option value="environment">Ambientes</option>
                        <option value="system">Sistemas</option>
                        <option value="strategy">Estratégias</option>
                        <option value="technique">Técnicas</option>
                        <option value="material">Materiais</option>
                        <option value="channel">Canais</option>
                        <option value="format">Formatos</option>
                        <option value="option">Opções equivalentes</option>
                        <option value="other">Outra dimensão</option>
                      </select>
                      <small>
                        Todos os itens precisam ser alternativas comparáveis no
                        mesmo nível. O Knowledge Architect bloqueia misturas
                        como ambiente + técnica + etapa.
                      </small>
                    </label>
                    <label>
                      Abordagens obrigatórias{" "}
                      <small>Uma por linha ou separadas por vírgula</small>
                      <textarea
                        required
                        minLength={3}
                        rows={4}
                        value={form.required_methods}
                        onChange={(e) =>
                          update("required_methods", e.target.value)
                        }
                        placeholder={
                          "Ex.:\nabordagem A\nabordagem B\nabordagem C"
                        }
                      />
                      <small>
                        O gate exige taxonomia coerente, dossiê, evidências e
                        referência externa para cada abordagem. Com{" "}
                        {keywords(form.required_methods).length || 0}{" "}
                        abordagens, o mínimo estrutural estimado é{" "}
                        {850 +
                          keywords(form.required_methods).length * 320 +
                          13 * 45}{" "}
                        palavras.
                      </small>
                    </label>
                  </>
                )}
                <label>
                  Estado inicial do leitor
                  <textarea
                    aria-label="Estado inicial do leitor"
                    required
                    minLength={10}
                    maxLength={READER_STATE_MAX_LENGTH}
                    rows={3}
                    value={form.reader_start_state}
                    onChange={(e) =>
                      update("reader_start_state", e.target.value)
                    }
                    placeholder="O que o leitor sabe, possui ou precisa compreender antes do primeiro método?"
                  />
                  <small aria-hidden="true">
                    {form.reader_start_state.length.toLocaleString("pt-BR")} /{" "}
                    {READER_STATE_MAX_LENGTH.toLocaleString("pt-BR")} caracteres
                  </small>
                </label>
                <label>
                  Estado final observável
                  <textarea
                    aria-label="Estado final observável"
                    required
                    minLength={10}
                    maxLength={READER_STATE_MAX_LENGTH}
                    rows={3}
                    value={form.reader_final_state}
                    onChange={(e) =>
                      update("reader_final_state", e.target.value)
                    }
                    placeholder="Qual resultado concreto permite encerrar o guia?"
                  />
                  <small aria-hidden="true">
                    {form.reader_final_state.length.toLocaleString("pt-BR")} /{" "}
                    {READER_STATE_MAX_LENGTH.toLocaleString("pt-BR")} caracteres
                  </small>
                </label>
                <label>
                  Promessa editorial
                  <textarea
                    aria-label="Promessa editorial"
                    required
                    minLength={20}
                    maxLength={ARTICLE_PROMISE_MAX_LENGTH}
                    rows={4}
                    value={form.article_promise}
                    onChange={(e) => update("article_promise", e.target.value)}
                    placeholder="Explique a transformação completa: fundamento, alternativas, escolha, execução, transição e resultado."
                  />
                  <small aria-hidden="true">
                    {form.article_promise.length.toLocaleString("pt-BR")} /{" "}
                    {ARTICLE_PROMISE_MAX_LENGTH.toLocaleString("pt-BR")}{" "}
                    caracteres
                  </small>
                </label>
                <label>
                  Ponto de encerramento do conteúdo
                  <textarea
                    aria-label="Ponto de encerramento do conteúdo"
                    required
                    minLength={10}
                    maxLength={SCOPE_LIMIT_MAX_LENGTH}
                    rows={3}
                    value={form.scope_limit}
                    onChange={(e) => update("scope_limit", e.target.value)}
                    placeholder="Em qual resultado concreto o conteúdo deve terminar?"
                  />
                  <small aria-hidden="true">
                    {form.scope_limit.length.toLocaleString("pt-BR")} /{" "}
                    {SCOPE_LIMIT_MAX_LENGTH.toLocaleString("pt-BR")} caracteres
                  </small>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.requires_method_comparison}
                    onChange={(e) =>
                      update("requires_method_comparison", e.target.checked)
                    }
                  />
                  <span>
                    <b>Comparar métodos antes da escolha</b>
                    <small>
                      Cria inventário, comparação e matriz de decisão.
                    </small>
                  </span>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.requires_external_reference_per_method}
                    onChange={(e) =>
                      update(
                        "requires_external_reference_per_method",
                        e.target.checked,
                      )
                    }
                  />
                  <span>
                    <b>Exigir referência externa por método</b>
                    <small>
                      O link precisa corresponder ao método e apresentar
                      profundidade procedural.
                    </small>
                  </span>
                </label>
              </div>
            )}
          </div>
        </section>

        <section className="panel form-section">
          <div className="section-number">03</div>
          <div className="section-copy">
            <h2>Estratégia de busca</h2>
            <p>
              A palavra principal orienta o artigo; os termos relacionados
              ampliam a cobertura.
            </p>
          </div>
          <div className="form-fields">
            <label>
              Palavra-chave principal
              <input
                required
                minLength={2}
                value={form.primary_keyword}
                onChange={(e) => update("primary_keyword", e.target.value)}
                placeholder="Ex.: como escolher um plano de internet"
              />
            </label>
            <label>
              Assunto factual da pesquisa{" "}
              <small>
                Opcional; use quando a palavra-chave SEO for curta ou ambígua
              </small>
              <input
                maxLength={RESEARCH_SUBJECT_MAX_LENGTH}
                value={form.research_subject}
                onChange={(e) => update("research_subject", e.target.value)}
                placeholder="Ex.: comparação técnica de planos residenciais de fibra, cobertura e franquia"
              />
              <small>
                Quando vazio, o sistema monta este assunto automaticamente com o
                tópico, segmento, métodos e contexto. {form.research_subject.length.toLocaleString("pt-BR")} / {RESEARCH_SUBJECT_MAX_LENGTH.toLocaleString("pt-BR")} caracteres.
              </small>
            </label>
            <label>
              Palavras-chave relacionadas{" "}
              <small>Separe por vírgula ou linha</small>
              <textarea
                rows={4}
                value={form.secondary_keywords}
                onChange={(e) => update("secondary_keywords", e.target.value)}
                placeholder="velocidade, franquia, cobertura, fidelidade..."
              />
            </label>
            <label>
              Nicho para pesquisa
              <input
                value={form.niche}
                onChange={(e) => update("niche", e.target.value)}
                placeholder="Ex.: telecomunicações e serviços digitais"
              />
            </label>
          </div>
        </section>

        <section className="panel form-section">
          <div className="section-number">04</div>
          <div className="section-copy">
            <h2>Leitor e momento</h2>
            <p>
              Contexto, maturidade e necessidade definem profundidade, exemplos
              e sequência.
            </p>
          </div>
          <div className="form-fields">
            <label>
              Público-alvo
              <div className="input-icon">
                <Users size={18} />
                <input
                  required
                  minLength={3}
                  placeholder="Quem lerá este conteúdo?"
                  value={form.audience}
                  onChange={(e) => update("audience", e.target.value)}
                />
              </div>
            </label>
            <div className="field-grid">
              <label>
                Idade mínima
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={form.reader_age_min}
                  onChange={(e) => update("reader_age_min", e.target.value)}
                />
              </label>
              <label>
                Idade máxima
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={form.reader_age_max}
                  onChange={(e) => update("reader_age_max", e.target.value)}
                />
              </label>
            </div>
            <div className="field-grid">
              <label>
                Fase de vida
                <input
                  value={form.reader_life_stage}
                  onChange={(e) => update("reader_life_stage", e.target.value)}
                  placeholder="Ex.: jovem adulto, primeira experiência"
                />
              </label>
              <label>
                Conhecimento
                <select
                  value={form.reader_knowledge_level}
                  onChange={(e) =>
                    update("reader_knowledge_level", e.target.value)
                  }
                >
                  <option value="mixed">Misto</option>
                  <option value="beginner">Iniciante</option>
                  <option value="intermediate">Intermediário</option>
                  <option value="advanced">Avançado</option>
                </select>
              </label>
            </div>
            <label>
              Contexto do leitor
              <textarea
                required
                minLength={10}
                rows={3}
                value={form.reader_context}
                onChange={(e) => update("reader_context", e.target.value)}
                placeholder="O que aconteceu antes da busca? Que dúvidas, limitações ou receios ele tem?"
              />
            </label>
            <label>
              O que o leitor busca
              <textarea
                required
                minLength={5}
                rows={3}
                value={form.reader_goal}
                onChange={(e) => update("reader_goal", e.target.value)}
                placeholder="Qual resultado concreto ele espera obter com a leitura?"
              />
            </label>
            <label>
              Idioma
              <select
                value={form.language}
                onChange={(e) => update("language", e.target.value)}
              >
                <option value="pt-BR">Português (Brasil)</option>
                <option value="en-US">English (US)</option>
                <option value="es-ES">Español</option>
              </select>
            </label>
          </div>
        </section>

        <section className="panel form-section">
          <div className="section-number">05</div>
          <div className="section-copy">
            <h2>Oferta e conversão</h2>
            <p>
              Conecte a solução com naturalidade. O artigo continua informativo,
              não publicitário.
            </p>
          </div>
          <div className="form-fields">
            <label>
              O que será oferecido junto
              <textarea
                rows={3}
                value={form.offer}
                onChange={(e) => update("offer", e.target.value)}
                placeholder="Produto, serviço, ferramenta, material ou próxima etapa."
              />
            </label>
            <label>
              Objetivo comercial
              <textarea
                rows={3}
                value={form.commercial_objective}
                onChange={(e) => update("commercial_objective", e.target.value)}
                placeholder="Como este conteúdo apoia o negócio sem forçar uma venda?"
              />
            </label>
            <label>
              Ação desejada
              <textarea
                rows={2}
                value={form.desired_action}
                onChange={(e) => update("desired_action", e.target.value)}
                placeholder="Ex.: conhecer o catálogo, solicitar orçamento, assinar a newsletter."
              />
            </label>
            <label>
              Contexto adicional
              <textarea
                rows={8}
                maxLength={ADDITIONAL_CONTEXT_MAX_LENGTH}
                value={form.additional_context}
                onChange={(e) => update("additional_context", e.target.value)}
                placeholder="Restrições, exemplos, abordagem desejada ou pontos que não podem faltar."
              />
              <small aria-hidden="true">
                {form.additional_context.length.toLocaleString("pt-BR")} /{" "}
                {ADDITIONAL_CONTEXT_MAX_LENGTH.toLocaleString("pt-BR")}{" "}
                caracteres
              </small>
            </label>
          </div>
        </section>

        <section className="pipeline-preview">
          <div>
            <SearchCheck />
            <strong>Pesquisa real</strong>
            <span>Busca internacional guiada pelo briefing</span>
          </div>
          <i />
          <div>
            <ShieldCheck />
            <strong>Gate de evidência</strong>
            <span>Cobertura e conflitos auditados</span>
          </div>
          <i />
          <div>
            <BookOpen />
            <strong>Redação contextual</strong>
            <span>Marca, leitor e objetivo em um só texto</span>
          </div>
        </section>
        {error && <div className="notice error">{error}</div>}
        <div className="form-actions">
          <label className="check">
            <input
              type="checkbox"
              checked={form.start_immediately}
              onChange={(e) => update("start_immediately", e.target.checked)}
            />
            <span>
              <b>
                {form.editorial_pipeline_version === "v3"
                  ? "Iniciar Editorial V3 após criar"
                  : "Iniciar pipeline após criar"}
              </b>
              <small>
                {form.editorial_pipeline_version === "v3"
                  ? "A redação só começa depois dos gates de pesquisa, síntese e completude."
                  : "O perfil e o briefing ficarão congelados neste run."}
              </small>
            </span>
          </label>
          <button
            disabled={saving || profiles.length === 0}
            className="button primary large"
          >
            {saving
              ? "Criando..."
              : form.editorial_pipeline_version === "v3"
                ? form.start_immediately
                  ? "Criar e iniciar V3"
                  : "Criar projeto V3"
                : form.start_immediately
                  ? "Criar e iniciar V2"
                  : "Criar projeto V2"}
            <ArrowRight size={18} />
          </button>
        </div>
      </form>
    </div>
  );
}
