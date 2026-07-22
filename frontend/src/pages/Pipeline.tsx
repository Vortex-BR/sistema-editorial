import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  Clock3,
  Database,
  Download,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorLogsPanel } from "../components/ErrorLogsPanel";
import { Status, statusLabel } from "../components/Status";
import { adminApi, adminDownload, safePublicMessage, wsUrl } from "../lib/api";
type ReviewRisk = { code?: string; severity?: string; message?: string } | string;
type QualityEvaluationSummary = {
  id: string;
  rubric_version: string;
  rubric_checksum: string;
  evaluator_kind: string;
  status: string;
  overall_score: number;
  result_checksum: string;
  axes: Record<string, { score: number; metrics: Record<string, unknown> }>;
  critical_blockers: { code: string; critical: boolean; details: Record<string, unknown> }[];
  warnings: { code: string; [key: string]: unknown }[];
  automatic_publication: false;
  human_comparison: null | {
    human_decision: string;
    evaluator_recommendation: string;
    agreement: boolean;
  };
};
type HumanReviewPackage = {
  facts?: { id: string; claim: string; approved: boolean }[];
  sources?: { id: string; title: string; url: string; publisher?: string | null }[];
  coverage?: {
    complete: boolean;
    questions: {
      id: string;
      question: string;
      priority: number;
      coverage_status: string;
    }[];
  };
  conflicts?: { group: string; claims: { id: string; claim: string }[] }[];
  seo?: Record<string, unknown>;
  changes?: Record<string, unknown>;
  risks?: ReviewRisk[];
  quality_evaluation?: QualityEvaluationSummary | null;
};
type HumanReview = {
  id: string;
  project_id: string;
  pipeline_run_id: string;
  article_version_id: string;
  reviewer: string | null;
  decision: string;
  observation: string | null;
  reviewed_at: string | null;
  revision_run_id: string | null;
  review_package: HumanReviewPackage;
};
type ExecutionManifestSummary = {
  status: string;
  id?: string;
  checksum?: string;
  build?: { commit_sha?: string; build_version?: string };
  mode?: string;
  feature_flags?: { editorial_pipeline_version?: string; [key: string]: unknown };
  super_skills?: Record<
    string,
    { skill_id: string; version: string; checksum: string }[]
  >;
  default_skills?: { skill_id: string; version: string; checksum: string }[];
  learned_skills?: Record<
    string,
    { skill_id: string; version: string; checksum: string }[]
  >;
  model_routes?: Record<string, { primary_provider: string; primary_model: string }>;
  search_route?: {
    provider?: string;
    policy?: {
      policy_version?: string;
      available_markets?: string[];
      international_markets?: string[];
      market_selection?: string;
      local_market_is_searched_first?: boolean;
      queries_are_localized_per_market?: boolean;
      brazil_market_requires_explicit_context?: boolean;
      exclude_brazilian_domains_by_default?: boolean;
    };
  };
  memory_ids?: Record<string, string[]>;
  style_pattern_ids?: Record<string, string[]>;
  handoff_ids?: string[];
  source_snapshot_ids?: string[];
  [key: string]: unknown;
};


type InformationRequirementCoverage = {
  requirement_id: string;
  task_id: string;
  knowledge_node_id: string;
  requirement_type: string;
  description: string;
  critical: boolean;
  status: "covered" | "partial" | "uncovered" | string;
  approved_claim_count: number;
  raw_claim_count: number;
  independent_source_count: number;
  authoritative_source_count: number;
  required_evidence_roles: string[];
  evidence_roles_found: string[];
  supporting_claim_ids: string[];
  reason_codes: string[];
};
type InformationCoverageSummary = {
  status?: string;
  overall_coverage_ratio?: number;
  critical_coverage_ratio?: number;
  covered_requirement_ids?: string[];
  partial_requirement_ids?: string[];
  uncovered_requirement_ids?: string[];
  critical_missing_requirement_ids?: string[];
  supporting_missing_requirement_ids?: string[];
  requirement_reports?: InformationRequirementCoverage[];
  reason_codes?: string[];
  suggested_blocking_code?: string | null;
};

const INFORMATION_REASON_LABELS: Record<string, string> = {
  no_claim_extracted_for_requirement: "nenhum fato extraído para esta informação",
  claims_not_approved_for_requirement: "os fatos encontrados não foram aprovados",
  evidence_policy_blocked_requirement: "a política de evidência rejeitou o suporte",
  approved_claim_count_insufficient: "faltam fatos aprovados",
  independent_support_insufficient: "faltam fontes independentes",
  required_evidence_role_missing: "falta o tipo de evidência exigido",
};

function informationReasonLabel(value: string): string {
  return INFORMATION_REASON_LABELS[value] || value.replaceAll("_", " ");
}

type V3ResearchRuntime = {
  version?: string;
  stage?: string;
  blocking_code?: string | null;
  blocking_reason?: string | null;
  research_intent?: {
    canonical_subject?: string;
    project_locale?: string;
    project_language?: string;
  };
  search_budget?: {
    logical_queries?: number;
    maximum_logical_queries?: number;
    provider_requests?: number;
    maximum_provider_requests?: number;
    provider_retries?: number;
    maximum_provider_retries?: number;
    elapsed_seconds?: number;
    timeout_seconds?: number;
    exhausted_by?: string | null;
  };
  provider_circuits?: Record<string, { status?: string; opened_reason?: string | null }>;
  providers_used?: string[];
  markets_by_task?: Record<string, string[]>;
  languages_by_task?: Record<string, string[]>;
  search_diagnostic_totals?: Record<string, number>;
  source_fetch_count?: number;
  structured_source_count?: number;
  source_recovery_round?: number;
  source_recovery_exhausted?: boolean;
  source_coverage?: {
    status?: string;
    deficient_task_ids?: string[];
    reason_codes?: string[];
    suggested_blocking_code?: string | null;
  } | null;
  information_coverage?: InformationCoverageSummary | null;
  information_recovery_round?: number;
  information_recovery_exhausted?: boolean;
  information_recovery_task_count?: number;
};
type Detail = {
  project: {
    id: string;
    name: string;
    topic: string;
    status: string;
    current_stage: string;
  };
  outcome_code: string | null;
  facts: { pipeline_run_id: string | null; total: number; approved: number };
  pipeline_runs: PipelineRunSummary[];
  latest_pipeline_run: PipelineRunSummary | null;
  selected_pipeline_run: PipelineRunSummary | null;
  runs: {
    id: string;
    pipeline_run_id: string;
    role: string;
    purpose?: string | null;
    status: string;
    decision: string | null;
    latency_ms: number;
    cost: number;
    error: string | null;
    error_code: string | null;
    error_category: string | null;
    http_status: number | null;
    retryable: boolean | null;
    correlation_id: string | null;
    recovered: boolean;
    recovery_code: string | null;
    recovered_by_agent_run_id: string | null;
  }[];
  article_version: null | {
    id: string;
    article_id: string;
    pipeline_run_id: string | null;
    version: number;
    title: string;
    outline: unknown[];
    editorial_status: string;
    markdown: string | null;
    html: string | null;
    seo_metadata: Record<string, unknown>;
    source_report: Record<string, unknown>;
  };
  article_pipeline_run_id: string | null;
  article_matches_selected_pipeline_run: boolean | null;
  execution_manifest: ExecutionManifestSummary | null;
  quality_evaluation: QualityEvaluationSummary | null;
  research_diagnostic: ResearchDiagnostic | null;
  v3_research_runtime: V3ResearchRuntime | null;
  editorial_diagnostic: EditorialDiagnostic | null;
  human_review: HumanReview | null;
  human_review_history: Omit<HumanReview, "review_package">[];
};
type PipelineRunSummary = {
  id: string;
  status: string;
  current_stage: string;
  cancellation_requested_at?: string | null;
  outcome_code?: string | null;
  error_code?: string | null;
  error_message?: string | null;
};
type ResearchDiagnostic = {
  pipeline_run_id: string;
  outcome_code: string | null;
  decision: string | null;
  coverage_complete: boolean;
  covered_question_count: number;
  total_question_count: number;
  recommended_fact_count: number;
  distinct_source_count: number;
  minimum_distinct_sources: number;
  source_diversity_score: number;
  missing_questions: string[];
  unresolved_conflicts: string[];
  rejection_reason_counts: Record<string, number>;
  instructions: string[];
};
type EditorialDiagnostic = {
  pipeline_run_id: string;
  decision: string | null;
  model_decision: string | null;
  resolution: string | null;
  blocking_finding_count: number;
  findings: {
    category: string;
    severity: string;
    issue: string;
    suggested_action: string;
  }[];
};
type StartRunResult = {
  project_id: string;
  pipeline_run_id: string;
  status: string;
  duplicate: boolean;
};
type EventTicket = { ticket: string; expires_in: number; protocol: string };
type CancellationResult = {
  pipeline_run_id: string;
  status: string;
  cancellation_requested_at: string;
  cancellation_pending: boolean;
};
type HumanReviewResult = {
  review: HumanReview;
  pipeline_run_status: string;
  revision_run_id: string | null;
  revision_created: boolean;
  duplicate: boolean;
};
type PipelineStreamEvent = {
  sequence: number;
  pipeline_run_id: string;
  stage: string;
  type: string;
  payload: Record<string, unknown>;
};
type EventBatch = {
  type: "events.batch";
  pipeline_run_id: string;
  after_sequence: number;
  last_sequence: number;
  events: PipelineStreamEvent[];
};
const DETAIL_REFRESH_WINDOW_MS = 500;
const RECONNECT_DELAY_MS = 1000;
const TIMELINE_EVENT_LIMIT = 200;
const CANCELLABLE_RUN_STATUSES = new Set(["queued", "running", "waiting_retry"]);
const TIMELINE_NOISE_EVENTS = new Set([
  "stage.started",
  "stage.completed",
  "agent.started",
  "agent.completed",
  "checkpoint.created",
]);
const TIMELINE_EVENT_LABELS: Record<string, string> = {
  "dispatch.claimed": "Execução reservada para envio ao worker.",
  "dispatch.sent": "Execução enviada ao worker.",
  "worker.lease_acquired": "Worker assumiu a execução com exclusividade.",
  "v3.sources.discovered": "Descoberta de fontes concluída.",
  "pipeline.blocked": "A execução foi interrompida por um gate editorial.",
  "pipeline.failed": "A execução foi interrompida por uma falha técnica.",
};
const agentRoleLabel = (role: string, purpose?: string | null) =>
  purpose === "style-discovery"
    ? "Curadoria de estilo (auxiliar)"
    :
  role === "editorial_repair"
    ? "Síntese baseada em evidências"
    : role.replaceAll("_", " ");
const editorialResolutionLabel = (resolution: string | null) => {
  if (resolution === "evidence_only_summary") return "Síntese segura entregue";
  if (resolution === "approved_with_advisory_findings") {
    return "Aprovado com ajustes opcionais";
  }
  return "Diagnóstico editorial";
};
const v2Stages = [
  [["planner"], "Planejador", Search],
  [["researcher"], "Pesquisador", Database],
  [["research_gatekeeper"], "Auditor de pesquisa", ShieldCheck],
  [["writer"], "Redator", FileText],
  [["editor"], "Revisor editorial", BookOpen],
  [["finalizer"], "Finalizador", Sparkles],
  [["human_approval"], "Editor-chefe", UserCheck],
] as const;
const v3Stages = [
  [["content_contract", "knowledge_architect", "knowledge_gate"], "Contrato", BookOpen],
  [["intelligence_planner"], "Inteligência editorial", Brain],
  [["research_planner"], "Plano de pesquisa", Search],
  [["source_discovery", "source_reader", "source_coverage_gate", "targeted_source_recovery"], "Fontes", Database],
  [["knowledge_synthesizer", "evidence_graph_builder", "intelligence_gate", "knowledge_completeness_gate"], "Síntese inteligente", ShieldCheck],
  [["writer"], "Redação", FileText],
  [["development_editor", "fact_checker", "language_editor", "external_reference_gate"], "Revisões", ShieldCheck],
  [["finalizer", "quality_gate"], "Finalização", Sparkles],
  [["human_approval"], "Editor-chefe", UserCheck],
] as const;
export function Pipeline() {
  const { id = "" } = useParams();
  const [data, setData] = useState<Detail | null>(null);
  const [events, setEvents] = useState<PipelineStreamEvent[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [activeTab, setActiveTab] = useState<"overview" | "logs">("overview");
  const [error, setError] = useState("");
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [rerunBusy, setRerunBusy] = useState(false);
  const [rerunError, setRerunError] = useState("");
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [reviewObservation, setReviewObservation] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const lastSequenceByRun = useRef<Record<string, number>>({});
  const detailRefreshTimer = useRef<number | null>(null);
  const reviewRequest = useRef<{ decision: string; key: string } | null>(null);
  const rerunRequest = useRef<string | null>(null);
  const selectedRun =
    data?.selected_pipeline_run?.id === selectedRunId
      ? data.selected_pipeline_run
      : data?.pipeline_runs.find((run) => run.id === selectedRunId);
  const streamEnabled = Boolean(
    selectedRun && CANCELLABLE_RUN_STATUSES.has(selectedRun.status),
  );
  const load = useCallback(async (pipelineRunId?: string) => {
    try {
      const runScope = pipelineRunId
        ? `?pipeline_run_id=${encodeURIComponent(pipelineRunId)}`
        : "";
      const detail = await adminApi<Detail>(`/projects/${id}${runScope}`);
      setData(detail);
      setError("");
      return detail;
    } catch (reason) {
      setError((reason as Error).message);
      return null;
    }
  }, [id]);
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setEvents([]);
    setSelectedRunId("");
    setActiveTab("overview");
    setError("");
    setReviewer("");
    setReviewObservation("");
    setReviewError("");
    setRerunError("");
    rerunRequest.current = null;
    reviewRequest.current = null;
    lastSequenceByRun.current = {};
    void load().then((detail) => {
      if (!cancelled && detail) {
        setSelectedRunId(
          detail.selected_pipeline_run?.id ||
            detail.latest_pipeline_run?.id ||
            detail.pipeline_runs[0]?.id ||
            "",
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, [id, load]);
  useEffect(() => {
    if (!selectedRunId || !streamEnabled) return;
    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    const scheduleReconnect = () => {
      if (cancelled || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        void connect();
      }, RECONNECT_DELAY_MS);
    };
    const scheduleDetailRefresh = () => {
      if (cancelled || detailRefreshTimer.current !== null) return;
      detailRefreshTimer.current = window.setTimeout(() => {
        detailRefreshTimer.current = null;
        if (!cancelled) void load(selectedRunId);
      }, DETAIL_REFRESH_WINDOW_MS);
    };
    const receiveBatch = (message: MessageEvent) => {
      try {
        const batch = JSON.parse(message.data) as EventBatch;
        if (
          batch.type !== "events.batch" ||
          batch.pipeline_run_id !== selectedRunId ||
          !Array.isArray(batch.events)
        ) {
          return;
        }
        const previousCursor = lastSequenceByRun.current[selectedRunId] || 0;
        const incoming = batch.events
          .filter(
            (event) =>
              event.pipeline_run_id === selectedRunId &&
              Number.isSafeInteger(event.sequence) &&
              event.sequence > previousCursor,
          )
          .sort((left, right) => left.sequence - right.sequence);
        if (incoming.length === 0) return;
        lastSequenceByRun.current[selectedRunId] =
          incoming[incoming.length - 1].sequence;
        setEvents((current) => {
          const unique = new Map<string, PipelineStreamEvent>();
          for (const event of [...current, ...incoming]) {
            if (event.pipeline_run_id === selectedRunId) {
              unique.set(`${event.pipeline_run_id}:${event.sequence}`, event);
            }
          }
          return [...unique.values()]
            .sort((left, right) => left.sequence - right.sequence)
            .slice(-TIMELINE_EVENT_LIMIT);
        });
        scheduleDetailRefresh();
      } catch {
        return;
      }
    };
    const connect = async () => {
      try {
        const issued = await adminApi<EventTicket>(
          `/projects/${id}/events/ticket`,
          {
            method: "POST",
            body: JSON.stringify({ pipeline_run_id: selectedRunId }),
          },
        );
        if (cancelled) return;
        if (issued.protocol !== "seo-events") {
          throw new Error("Subprotocolo de eventos inválido.");
        }
        setError("");
        const currentSocket = new WebSocket(wsUrl(id, selectedRunId), [
          "seo-events",
          issued.ticket,
        ]);
        socket = currentSocket;
        currentSocket.onopen = () => {
          if (cancelled || socket !== currentSocket) return;
          currentSocket.send(
            JSON.stringify({
              type: "subscribe",
              after_sequence: lastSequenceByRun.current[selectedRunId] || 0,
            }),
          );
        };
        currentSocket.onmessage = receiveBatch;
        currentSocket.onerror = () => currentSocket.close();
        currentSocket.onclose = () => {
          if (socket !== currentSocket) return;
          socket = null;
          scheduleReconnect();
        };
      } catch (reason) {
        if (!cancelled) {
          setError((reason as Error).message);
          scheduleReconnect();
        }
      }
    };
    void connect();
    return () => {
      cancelled = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (detailRefreshTimer.current !== null) {
        window.clearTimeout(detailRefreshTimer.current);
        detailRefreshTimer.current = null;
      }
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [id, load, selectedRunId, streamEnabled]);
  function selectRun(pipelineRunId: string) {
    if (!pipelineRunId || pipelineRunId === selectedRunId) return;
    delete lastSequenceByRun.current[pipelineRunId];
    setEvents([]);
    setReviewer("");
    setReviewObservation("");
    setReviewError("");
    reviewRequest.current = null;
    setData((current) =>
      current
        ? {
            ...current,
            facts: { pipeline_run_id: pipelineRunId, total: 0, approved: 0 },
            runs: [],
          }
        : current,
    );
    setSelectedRunId(pipelineRunId);
    void load(pipelineRunId);
  }
  async function cancelSelectedRun() {
    if (!selectedRun || cancelling) return;
    setCancelling(true);
    setCancelError("");
    try {
      const result = await adminApi<CancellationResult>(
        `/pipeline-runs/${selectedRun.id}/cancel`,
        { method: "POST" },
      );
      setData((current) =>
        current
          ? {
              ...current,
              pipeline_runs: current.pipeline_runs.map((run) =>
                run.id === result.pipeline_run_id
                  ? {
                      ...run,
                      status: result.status,
                      cancellation_requested_at:
                        result.cancellation_requested_at,
                    }
                  : run,
              ),
            }
          : current,
      );
      setCancelDialogOpen(false);
      void load(selectedRun.id);
    } catch (reason) {
      setCancelError(safePublicMessage((reason as Error).message));
    } finally {
      setCancelling(false);
    }
  }
  async function decideHumanReview(
    decision: "approve" | "reject" | "request_revision",
  ) {
    if (!selectedRun || reviewBusy || !reviewer.trim()) return;
    if (
      (decision === "reject" || decision === "request_revision") &&
      !reviewObservation.trim()
    ) {
      setReviewError("Informe o motivo ou as instruções para esta decisão.");
      return;
    }
    setReviewBusy(true);
    setReviewError("");
    if (!reviewRequest.current || reviewRequest.current.decision !== decision) {
      reviewRequest.current = {
        decision,
        key: globalThis.crypto.randomUUID(),
      };
    }
    try {
      const result = await adminApi<HumanReviewResult>(
        `/pipeline-runs/${selectedRun.id}/human-review`,
        {
          method: "POST",
          headers: { "Idempotency-Key": reviewRequest.current.key },
          body: JSON.stringify({
            decision,
            reviewer: reviewer.trim(),
            observation: reviewObservation.trim() || null,
          }),
        },
      );
      reviewRequest.current = null;
      setReviewer("");
      setReviewObservation("");
      if (result.revision_run_id) {
        setEvents([]);
        setSelectedRunId(result.revision_run_id);
        await load(result.revision_run_id);
      } else {
        await load(selectedRun.id);
      }
    } catch (reason) {
      setReviewError(safePublicMessage((reason as Error).message));
    } finally {
      setReviewBusy(false);
    }
  }
  async function exportPackage() {
    if (exporting || !data?.article_version?.markdown?.trim()) return;
    setExporting(true);
    setExportError("");
    try {
      const { blob, filename } = await adminDownload(
        data.human_review?.decision === "approved"
          ? `/projects/${id}/export`
          : `/projects/${id}/export?draft=true`,
        "pacote-editorial.zip",
      );
      const objectUrl = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filename;
        anchor.hidden = true;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    } catch (reason) {
      setExportError(safePublicMessage((reason as Error).message));
    } finally {
      setExporting(false);
    }
  }
  async function startNewResearch() {
    if (rerunBusy) return;
    setRerunBusy(true);
    setRerunError("");
    rerunRequest.current ||= globalThis.crypto.randomUUID();
    try {
      const result = await adminApi<StartRunResult>(`/projects/${id}/run`, {
        method: "POST",
        headers: { "Idempotency-Key": rerunRequest.current },
      });
      rerunRequest.current = null;
      setEvents([]);
      setSelectedRunId(result.pipeline_run_id);
      await load(result.pipeline_run_id);
    } catch (reason) {
      setRerunError(safePublicMessage((reason as Error).message));
    } finally {
      setRerunBusy(false);
    }
  }
  if (error)
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    );
  if (!data)
    return <div className="page loading">Carregando trilha de auditoria…</div>;
  const currentStage = data.selected_pipeline_run?.current_stage || data.project.current_stage;
  const manifestPipelineVersion =
    data.execution_manifest?.feature_flags?.editorial_pipeline_version;
  const usesV3Stages =
    manifestPipelineVersion === "v3" ||
    v3Stages.some(([keys]) => (keys as readonly string[]).includes(currentStage));
  const stages = usesV3Stages ? v3Stages : v2Stages;
  const currentIndex = stages.findIndex(([keys]) =>
    (keys as readonly string[]).includes(currentStage),
  );
  const selectedRunStatus = data.selected_pipeline_run?.status || data.project.status;
  const timelineEvents = events
    .filter((event) => !TIMELINE_NOISE_EVENTS.has(event.type))
    .slice(-50);
  const diagnostic = data.research_diagnostic;
  const latestRunStatus = data.latest_pipeline_run?.status;
  const canStartNewResearch =
    !latestRunStatus ||
    ["blocked", "failed", "cancelled", "rejected"].includes(latestRunStatus);
  const humanReview = data.human_review;
  const reviewPackage = humanReview?.review_package || {};
  const canDecideHumanReview = Boolean(
    humanReview?.decision === "pending" &&
      data.selected_pipeline_run?.status === "needs_human_approval" &&
      data.article_matches_selected_pipeline_run === true,
  );
  const publishable = humanReview?.decision === "approved";
  const selectedRunError = data.selected_pipeline_run?.error_message;
  const selectedRunErrorCode = data.selected_pipeline_run?.error_code;
  const selectedRunStopped =
    selectedRunStatus === "failed" || selectedRunStatus === "blocked";
  const informationCoverage = data.v3_research_runtime?.information_coverage;
  const informationRequirements = informationCoverage?.requirement_reports || [];
  const missingInformationRequirements = informationRequirements.filter(
    (item) => item.status !== "covered",
  );
  return (
    <div className="page">
      <Link to="/" className="back">
        <ArrowLeft size={16} />
        Todos os projetos
      </Link>
      <div className="page-heading pipeline-heading">
        <div>
          <div className="heading-line">
            <span className="eyebrow">PIPELINE AUDITÁVEL</span>
            <Status value={data.outcome_code || diagnostic?.outcome_code || data.project.status} />
          </div>
          <h1>{data.project.name}</h1>
          <p>{data.project.topic}</p>
        </div>
        <div className="pipeline-heading-actions">
          {canStartNewResearch && (
            <button
              className="button primary"
              disabled={rerunBusy}
              aria-busy={rerunBusy}
              onClick={startNewResearch}
            >
              <RefreshCw size={16} />
              {rerunBusy
                ? "Iniciando…"
                : latestRunStatus
                  ? "Executar nova pesquisa"
                  : "Iniciar execução"}
            </button>
          )}
          <button
            className="button secondary"
            disabled={!data.article_version?.markdown?.trim() || exporting}
            aria-busy={exporting}
            onClick={exportPackage}
          >
            <Download size={16} />
            {exporting
              ? "Gerando pacote…"
              : publishable
                ? "Exportar pacote"
                : "Exportar rascunho"}
          </button>
        </div>
      </div>
      <nav className="project-tabs" aria-label="Seções do projeto">
        <button
          type="button"
          className={activeTab === "overview" ? "active" : ""}
          aria-current={activeTab === "overview" ? "page" : undefined}
          onClick={() => setActiveTab("overview")}
        >
          Visão geral
        </button>
        <button
          type="button"
          className={activeTab === "logs" ? "active" : ""}
          aria-current={activeTab === "logs" ? "page" : undefined}
          onClick={() => setActiveTab("logs")}
        >
          Logs de erros
          {selectedRunStopped && <span className="tab-alert" aria-label="Execução com erro">!</span>}
        </button>
      </nav>
      {activeTab === "logs" ? (
        <ErrorLogsPanel
          projectId={id}
          pipelineRunId={selectedRunId || undefined}
          live={streamEnabled}
        />
      ) : (
        <>
      {exportError && (
        <div className="notice error" role="alert">
          {exportError}
        </div>
      )}
      {rerunError && (
        <div className="notice error" role="alert">
          {rerunError}
        </div>
      )}
      {cancelError && (
        <div className="notice error" role="alert">
          {cancelError}
        </div>
      )}
      {reviewError && (
        <div className="notice error" role="alert">
          {reviewError}
        </div>
      )}
      {selectedRunStopped && (selectedRunError || selectedRunErrorCode) && (
        <div
          className={`notice ${selectedRunStatus === "failed" ? "error" : "warning"}`}
          role="status"
        >
          <AlertTriangle size={18} />
          <div>
            <strong>
              {selectedRunStatus === "failed"
                ? "A execução encontrou uma falha técnica"
                : "A execução foi bloqueada por uma regra editorial"}
            </strong>
            <p>{safePublicMessage(selectedRunError || selectedRunErrorCode)}</p>
            {selectedRunErrorCode && <small>Diagnóstico: {selectedRunErrorCode}</small>}
            {selectedRunStatus === "failed" && (
              <button type="button" className="text-button" onClick={() => setActiveTab("logs")}>
                Abrir logs técnicos
              </button>
            )}
          </div>
        </div>
      )}
      {data.article_version &&
        data.article_matches_selected_pipeline_run === false && (
          <div className="notice warning" role="status">
            <AlertTriangle size={18} />
            <div>
              <strong>O artigo exibido foi produzido por outro run</strong>
              <p>
                Run selecionado: {data.selected_pipeline_run?.id || "nenhum"}. Run do
                artigo: {data.article_pipeline_run_id || "não registrado"}.
              </p>
            </div>
          </div>
        )}
      <section
        className="stage-track"
        style={{ gridTemplateColumns: `repeat(${stages.length}, 1fr)` }}
      >
        {stages.map(([keys, label, Icon], i) => {
          const done = i < currentIndex || selectedRunStatus === "completed";
          const active =
            i === currentIndex && selectedRunStatus !== "completed";
          return (
            <div
              className={`stage ${done ? "done" : ""} ${active ? "active" : ""}`}
              key={keys[0]}
            >
              <div className="stage-icon">{done ? <Check /> : <Icon />}</div>
              <span>{String(i + 1).padStart(2, "0")}</span>
              <strong>{label}</strong>
              {i < stages.length - 1 && (
                <ChevronRight className="stage-arrow" />
              )}
            </div>
          );
        })}
      </section>
      {diagnostic?.decision === "insufficient" && (
        <section className="panel research-diagnostic" aria-label="Diagnóstico da pesquisa">
          <div className="panel-head">
            <div className="title-icon">
              <AlertTriangle size={22} />
              <div>
                <span className="eyebrow">PESQUISA INSUFICIENTE</span>
                <h2>
                  {diagnostic.covered_question_count} de {diagnostic.total_question_count}{" "}
                  perguntas cobertas
                </h2>
                <p>O gate editorial permaneceu fechado; nenhum fato foi aprovado automaticamente.</p>
              </div>
            </div>
            <Status value="research_insufficient" />
          </div>
          <div className="diagnostic-body">
            <div className="diagnostic-metrics">
              <div><span>Fatos recomendados</span><strong>{diagnostic.recommended_fact_count}</strong></div>
              <div>
                <span>Fontes selecionadas</span>
                <strong>{diagnostic.distinct_source_count} / {diagnostic.minimum_distinct_sources}</strong>
              </div>
              <div><span>Conflitos abertos</span><strong>{diagnostic.unresolved_conflicts.length}</strong></div>
            </div>
            {diagnostic.missing_questions.length > 0 && (
              <div className="diagnostic-list">
                <h3>Lacunas de cobertura</h3>
                <ul>{diagnostic.missing_questions.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            )}
            {diagnostic.unresolved_conflicts.length > 0 && (
              <div className="diagnostic-list">
                <h3>Conflitos a resolver</h3>
                <ul>{diagnostic.unresolved_conflicts.map((item) => <li key={item}>{item.replaceAll("_", " ")}</li>)}</ul>
              </div>
            )}
            {Object.keys(diagnostic.rejection_reason_counts).length > 0 && (
              <div className="diagnostic-list">
                <h3>Rejeições do auditor</h3>
                <p>{Object.entries(diagnostic.rejection_reason_counts).map(([reason, count]) => `${reason.replaceAll("_", " ")}: ${count}`).join(" · ")}</p>
              </div>
            )}
            {diagnostic.instructions.length > 0 && (
              <div className="diagnostic-list">
                <h3>Próxima pesquisa</h3>
                <ul>{diagnostic.instructions.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            )}
          </div>
        </section>
      )}
      {data.execution_manifest && (
        <section className="panel execution-manifest">
          <div className="panel-head">
            <div className="title-icon">
              <ShieldCheck size={22} />
              <div>
                <span className="eyebrow">MANIFESTO REPRODUZÍVEL</span>
                <h2>Dependências fixadas para este run</h2>
                <p>
                  Skills, modelos, contratos, contexto e artefatos efetivamente usados.
                </p>
              </div>
            </div>
            <Status value={data.execution_manifest.status} />
          </div>
          {data.execution_manifest.status === "ready" ? (
            <div className="manifest-summary">
              <div>
                <span>Commit e build</span>
                <strong>
                  {data.execution_manifest.build?.commit_sha || "não informado"}
                </strong>
                <small>
                  {data.execution_manifest.build?.build_version || "sem versão"}
                </small>
              </div>
              <div>
                <span>Modo de contexto</span>
                <strong>{data.execution_manifest.mode || "não informado"}</strong>
              </div>
              <div>
                <span>Dependências editoriais</span>
                <strong>
                  {(data.execution_manifest.default_skills || []).length} skills padrão · {" "}
                  {Object.values(data.execution_manifest.learned_skills || {}).flat().length}{" "}
                  aprendidas
                </strong>
              </div>
              <div>
                <span>Mercados de pesquisa</span>
                <strong>
                  {(data.execution_manifest.search_route?.policy?.available_markets ||
                    data.execution_manifest.search_route?.policy?.international_markets || [])
                    .map((market) => market.toUpperCase())
                    .join(" · ") || "definidos dinamicamente"}
                </strong>
                <small>
                  Mercado local primeiro; consultas localizadas por idioma e função de evidência
                </small>
              </div>
              <div>
                <span>Artefatos run-scoped</span>
                <strong>
                  {(data.execution_manifest.handoff_ids || []).length} handoffs · {" "}
                  {(data.execution_manifest.source_snapshot_ids || []).length} snapshots
                </strong>
              </div>
              <code className="manifest-checksum">
                SHA-256 {data.execution_manifest.checksum}
              </code>
              <details>
                <summary>Ver manifesto seguro completo</summary>
                <pre>{JSON.stringify(data.execution_manifest, null, 2)}</pre>
              </details>
            </div>
          ) : (
            <div className="notice warning" role="status">
              Este run não possui um manifesto reproduzível válido.
            </div>
          )}
        </section>
      )}
      {data.v3_research_runtime && (
        <section className="panel execution-manifest">
          <div className="panel-head">
            <div className="title-icon">
              <Database size={22} />
              <div>
                <span className="eyebrow">PESQUISA V3.5</span>
                <h2>Diagnóstico real da descoberta de fontes</h2>
                <p>
                  Intenção factual, mercados, requisições, leitura, diversidade e recuperação.
                </p>
              </div>
            </div>
            <Status value={data.v3_research_runtime.source_coverage?.status || data.v3_research_runtime.stage || "running"} />
          </div>
          <div className="manifest-summary">
            <div>
              <span>Assunto factual</span>
              <strong>{data.v3_research_runtime.research_intent?.canonical_subject || "não informado"}</strong>
              <small>{data.v3_research_runtime.research_intent?.project_locale || "locale não informado"}</small>
            </div>
            <div>
              <span>Consultas lógicas</span>
              <strong>
                {data.v3_research_runtime.search_budget?.logical_queries || 0} / {data.v3_research_runtime.search_budget?.maximum_logical_queries || "—"}
              </strong>
              <small>
                {data.v3_research_runtime.search_budget?.provider_requests || 0} requisições reais · {data.v3_research_runtime.search_budget?.provider_retries || 0} retries
              </small>
            </div>
            <div>
              <span>Mercados e idiomas usados</span>
              <strong>
                {Array.from(new Set(Object.values(data.v3_research_runtime.markets_by_task || {}).flat())).map((item) => item.toUpperCase()).join(" · ") || "nenhum"}
              </strong>
              <small>
                {Array.from(new Set(Object.values(data.v3_research_runtime.languages_by_task || {}).flat())).join(" · ") || "nenhum idioma registrado"}
              </small>
            </div>
            <div>
              <span>Fontes lidas</span>
              <strong>{data.v3_research_runtime.structured_source_count || 0}</strong>
              <small>
                {data.v3_research_runtime.source_fetch_count || 0} tentativas de leitura · recuperação {data.v3_research_runtime.source_recovery_round || 0}
              </small>
            </div>
            <div>
              <span>Cobertura por nó</span>
              <strong>
                {data.v3_research_runtime.source_coverage?.status === "passed"
                  ? "Aprovada"
                  : `${data.v3_research_runtime.source_coverage?.deficient_task_ids?.length || 0} nós pendentes`}
              </strong>
              <small>
                {(data.v3_research_runtime.source_coverage?.reason_codes || []).join(" · ") || "sem bloqueios de cobertura"}
              </small>
            </div>
          </div>
          {informationCoverage && (
            <div className="information-coverage">
              <div className="information-coverage-head">
                <div>
                  <span className="eyebrow">COBERTURA POR INFORMAÇÃO</span>
                  <h3>O sistema valida o que precisa ser respondido, não uma cota de fatos</h3>
                  <p>
                    Cada pergunta, decisão e critério de conclusão do contrato possui
                    evidência e recuperação próprias.
                  </p>
                </div>
                <Status value={informationCoverage.status || "running"} />
              </div>
              <div className="information-coverage-metrics">
                <div>
                  <span>Cobertura geral</span>
                  <strong>{Math.round((informationCoverage.overall_coverage_ratio || 0) * 100)}%</strong>
                </div>
                <div>
                  <span>Cobertura crítica</span>
                  <strong>{Math.round((informationCoverage.critical_coverage_ratio || 0) * 100)}%</strong>
                </div>
                <div>
                  <span>Informações cobertas</span>
                  <strong>{informationCoverage.covered_requirement_ids?.length || 0}</strong>
                </div>
                <div>
                  <span>Pendências</span>
                  <strong>{missingInformationRequirements.length}</strong>
                </div>
              </div>
              <div className="information-progress" aria-label="Cobertura geral das informações">
                <i style={{ width: `${Math.min(100, Math.max(0, (informationCoverage.overall_coverage_ratio || 0) * 100))}%` }} />
              </div>
              <div className="information-recovery-status">
                <strong>Recuperação direcionada:</strong>{" "}
                rodada {data.v3_research_runtime.information_recovery_round || 0}
                {data.v3_research_runtime.information_recovery_task_count
                  ? ` · ${data.v3_research_runtime.information_recovery_task_count} informações na fila`
                  : " · nenhuma pendência na fila"}
                {data.v3_research_runtime.information_recovery_exhausted
                  ? " · tentativas esgotadas"
                  : ""}
              </div>
              {missingInformationRequirements.length > 0 && (
                <div className="information-gap-list">
                  <h4>Informações ainda sem suporte suficiente</h4>
                  {missingInformationRequirements.slice(0, 12).map((item) => (
                    <article key={item.requirement_id} className={`information-gap ${item.critical ? "critical" : ""}`}>
                      <div>
                        <strong>{item.description}</strong>
                        <small>
                          {item.knowledge_node_id.replaceAll("_", " ")} · {item.requirement_type.replaceAll("_", " ")}
                          {item.critical ? " · crítica" : ""}
                        </small>
                      </div>
                      <div className="information-gap-evidence">
                        <span>
                          {item.approved_claim_count}{" "}
                          {item.approved_claim_count === 1
                            ? "fato aprovado"
                            : "fatos aprovados"}
                        </span>
                        <span>
                          {item.independent_source_count}{" "}
                          {item.independent_source_count === 1
                            ? "fonte independente"
                            : "fontes independentes"}
                        </span>
                        <code>
                          {(item.reason_codes || [])
                            .map(informationReasonLabel)
                            .join(" · ") || item.status}
                        </code>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
          )}
          {(data.v3_research_runtime.blocking_code || data.v3_research_runtime.search_budget?.exhausted_by) && (
            <div className="notice warning" role="status">
              <strong>{data.v3_research_runtime.blocking_code || "V3_SEARCH_ATTEMPT_BUDGET_EXHAUSTED"}</strong>
              <p>{data.v3_research_runtime.blocking_reason || data.v3_research_runtime.search_budget?.exhausted_by}</p>
            </div>
          )}
        </section>
      )}
      {data.quality_evaluation && (
        <section className="panel quality-evaluation">
          <div className="panel-head">
            <div className="title-icon">
              <ShieldCheck size={22} />
              <div>
                <span className="eyebrow">AVALIAÇÃO INDEPENDENTE</span>
                <h2>Rubrica determinística de qualidade</h2>
                <p>
                  Scores do writer não substituem citações, cobertura e suporte verificados.
                </p>
              </div>
            </div>
            <Status value={data.quality_evaluation.status} />
          </div>
          <div className="quality-summary">
            <div>
              <span>Score geral</span>
              <strong>{Math.round(data.quality_evaluation.overall_score * 100)}%</strong>
            </div>
            <div>
              <span>Rubrica</span>
              <strong>{data.quality_evaluation.rubric_version}</strong>
            </div>
            <div>
              <span>Blockers críticos</span>
              <strong>{data.quality_evaluation.critical_blockers.length}</strong>
            </div>
            <div>
              <span>Decisão humana</span>
              <strong>
                {data.quality_evaluation.human_comparison
                  ? data.quality_evaluation.human_comparison.agreement
                    ? "Concordante"
                    : "Divergente"
                  : "Ainda pendente"}
              </strong>
            </div>
            <ul>
              {Object.entries(data.quality_evaluation.axes).map(([axis, result]) => (
                <li key={axis}>
                  <span>{axis.replaceAll("_", " ")}</span>
                  <strong>{Math.round(result.score * 100)}%</strong>
                </li>
              ))}
            </ul>
            {data.quality_evaluation.critical_blockers.length > 0 && (
              <div className="notice error" role="alert">
                {data.quality_evaluation.critical_blockers.map((item) => item.code).join(" · ")}
              </div>
            )}
            <small>O score nunca publica conteúdo automaticamente.</small>
          </div>
        </section>
      )}
      <section className="audit-layout">
        <div className="panel run-panel">
          <div className="panel-head">
            <div>
              <h2>Trilha de execução</h2>
              <p>
                Decisões, tentativas e custo do run selecionado
                {data.selected_pipeline_run
                  ? ` ${data.selected_pipeline_run.id.slice(0, 8)}`
                  : ""}
              </p>
            </div>
            <div>
              {data.pipeline_runs.length > 0 && (
                <select
                  aria-label="Execução do pipeline"
                  value={selectedRunId}
                  onChange={(event) => selectRun(event.target.value)}
                >
                  {data.pipeline_runs.map((run) => (
                    <option key={run.id} value={run.id}>
                      {run.id.slice(0, 8)}{run.status ? ` · ${statusLabel(run.outcome_code || run.status)}` : ""}
                      {run.id === data.latest_pipeline_run?.id ? " · mais recente" : ""}
                    </option>
                  ))}
                </select>
              )}
              {selectedRun?.cancellation_requested_at &&
              selectedRun.status === "running" ? (
                <Status value="cancellation_requested" />
              ) : selectedRun &&
                CANCELLABLE_RUN_STATUSES.has(selectedRun.status) ? (
                <button
                  type="button"
                  className="button danger"
                  onClick={() => setCancelDialogOpen(true)}
                >
                  Cancelar execução
                </button>
              ) : null}
              {streamEnabled && (
                <span className="live">
                  <i />
                  AO VIVO
                </span>
              )}
            </div>
          </div>
          {data.runs.length === 0 && timelineEvents.length === 0 ? (
            <div className="empty small">
              <Clock3 />
              <h3>Aguardando o primeiro agente</h3>
              <p>
                Os eventos aparecerão aqui assim que o worker assumir o projeto.
              </p>
            </div>
          ) : (
            <div className="timeline">
              {data.runs.map((run) => (
                <div className="timeline-item" key={run.id}>
                  <div className="timeline-dot">
                    {run.recovered || ["succeeded", "completed"].includes(run.status) ? (
                      <Check size={13} />
                    ) : run.status === "failed" ? (
                      <AlertTriangle size={13} />
                    ) : (
                      <Clock3 size={13} />
                    )}
                  </div>
                  <div>
                    <div className="timeline-title">
                      <strong>{agentRoleLabel(run.role, run.purpose)}</strong>
                      <Status value={run.recovered ? "recovered" : run.status} />
                    </div>
                    <p>
                      {run.recovered
                        ? `Saída recuperada com segurança${run.recovery_code ? `: ${run.recovery_code}` : ""}`
                        : run.error
                        ? run.error
                        : run.decision
                          ? `Decisão: ${run.decision}`
                          : "Execução registrada"}
                    </p>
                    <small>
                      {run.latency_ms} ms · US$ {run.cost.toFixed(4)}
                    </small>
                    {run.error_code&&<small className="diagnostic-reference">
                      Diagnóstico: {run.error_category||run.error_code}
                      {run.http_status?` · HTTP ${run.http_status}`:''}
                      {run.correlation_id?` · referência ${run.correlation_id}`:''}
                    </small>}
                  </div>
                </div>
              ))}
              {timelineEvents.map((e) => (
                <div
                  className="timeline-item"
                  key={`${e.pipeline_run_id}:${e.sequence}`}
                >
                  <div className="timeline-dot pulse" />
                  <div>
                    <strong>{e.stage.replaceAll("_", " ")}</strong>
                    <p>{safePublicMessage(e.payload.message || TIMELINE_EVENT_LABELS[e.type] || e.type, e.type)}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <aside className="side-stack">
          <div className="panel ledger-card">
            <div className="panel-head">
              <div>
                <h2>Fact Ledger</h2>
                <p>Base permitida para a redação</p>
              </div>
              <Database size={20} />
            </div>
            <div className="ledger-score">
              <div>
                <strong>{data.facts.approved}</strong>
                <span>fatos aprovados</span>
              </div>
              <div>
                <strong>{data.facts.total}</strong>
                <span>fatos coletados</span>
              </div>
            </div>
            <div className="progress">
              <i
                style={{
                  width: `${data.facts.total ? (data.facts.approved / data.facts.total) * 100 : 0}%`,
                }}
              />
            </div>
            <Link to="#" className="text-link">
              Inspecionar evidências <ExternalLink size={14} />
            </Link>
          </div>
          <div className="panel invariant">
            <AlertTriangle size={20} />
            <div>
              <strong>Portão fechado por padrão</strong>
              <p>
                A redação só começa quando cada nó crítico possui fontes relevantes,
                legíveis, independentes e adequadas à função de evidência exigida.
              </p>
            </div>
          </div>
        </aside>
      </section>
      {data.editorial_diagnostic && data.editorial_diagnostic.findings.length > 0 && (
        <section className="panel editorial-diagnostic">
          <div className="panel-head">
            <div className="title-icon">
              <BookOpen size={22} />
              <div>
                <span className="eyebrow">DIAGNÓSTICO EDITORIAL</span>
                <h2>{editorialResolutionLabel(data.editorial_diagnostic.resolution)}</h2>
                <p>
                  {data.editorial_diagnostic.blocking_finding_count > 0
                    ? "Afirmações sem suporte suficiente foram corrigidas ou removidas antes da entrega."
                    : "Os apontamentos abaixo são melhorias opcionais e não impedem a entrega do conteúdo."}
                </p>
              </div>
            </div>
            <Status value={data.editorial_diagnostic.decision || "approved"} />
          </div>
          <ul>
            {data.editorial_diagnostic.findings.map((finding, index) => (
              <li key={`${finding.category}-${finding.severity}-${index}`}>
                <strong>{finding.severity} · {finding.category}</strong>
                <span>{finding.issue}</span>
                {finding.suggested_action && <small>{finding.suggested_action}</small>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {humanReview && (
        <section className="panel chief-review">
          <div className="panel-head">
            <div className="title-icon">
              <UserCheck size={22} />
              <div>
                <span className="eyebrow">EDITOR-CHEFE HUMANO</span>
                <h2>Pacote de revisão final</h2>
                <p>
                  A automação terminou, mas a peça só é publicável após uma decisão
                  humana identificada.
                </p>
              </div>
            </div>
            <Status value={humanReview.decision} />
          </div>
          <div className="review-grid">
            <article>
              <h3>Fontes e fatos</h3>
              <p>
                {(reviewPackage.facts || []).filter((fact) => fact.approved).length} fatos
                aprovados · {(reviewPackage.sources || []).length} fontes
              </p>
              <ul>
                {(reviewPackage.sources || []).slice(0, 8).map((source) => (
                  <li key={source.id}>
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.title}
                    </a>
                  </li>
                ))}
              </ul>
              <ul>
                {(reviewPackage.facts || []).slice(0, 8).map((fact) => (
                  <li key={fact.id}>
                    <span>{fact.claim}</span>
                    <Status value={fact.approved ? "approved" : "rejected"} />
                  </li>
                ))}
              </ul>
            </article>
            <article>
              <h3>Cobertura</h3>
              <Status
                value={reviewPackage.coverage?.complete ? "approved" : "insufficient"}
              />
              <ul>
                {(reviewPackage.coverage?.questions || []).map((question) => (
                  <li key={question.id}>
                    <span>{question.question}</span>
                    <Status value={question.coverage_status} />
                  </li>
                ))}
              </ul>
            </article>
            <article>
              <h3>Conflitos</h3>
              {(reviewPackage.conflicts || []).length === 0 ? (
                <p>Nenhum conflito não resolvido.</p>
              ) : (
                <ul>
                  {(reviewPackage.conflicts || []).map((conflict) => (
                    <li key={conflict.group}>
                      <strong>{conflict.group}</strong>
                      <span>{conflict.claims.length} afirmações em conflito</span>
                    </li>
                  ))}
                </ul>
              )}
            </article>
            <article>
              <h3>SEO</h3>
              <pre>{JSON.stringify(reviewPackage.seo || {}, null, 2)}</pre>
            </article>
            <article>
              <h3>Mudanças da última versão</h3>
              <pre>{JSON.stringify(reviewPackage.changes || {}, null, 2)}</pre>
            </article>
            <article>
              <h3>Riscos</h3>
              <ul>
                {(reviewPackage.risks || []).map((risk, index) => (
                  <li key={`${typeof risk === "string" ? risk : risk.code}-${index}`}>
                    {typeof risk === "string"
                      ? risk
                      : risk.message || risk.code || "Risco editorial"}
                  </li>
                ))}
              </ul>
            </article>
          </div>
          {canDecideHumanReview ? (
            <div className="review-actions">
              <label>
                Identidade do revisor humano
                <input
                  required
                  value={reviewer}
                  onChange={(event) => setReviewer(event.target.value)}
                  placeholder="Nome ou identificação interna"
                />
              </label>
              <label>
                Observação ou instruções de revisão
                <textarea
                  rows={4}
                  value={reviewObservation}
                  onChange={(event) => setReviewObservation(event.target.value)}
                  placeholder="Obrigatório para rejeitar ou solicitar revisão"
                />
              </label>
              <div>
                <button
                  className="button primary"
                  disabled={reviewBusy || !reviewer.trim()}
                  onClick={() => void decideHumanReview("approve")}
                >
                  Aprovar para publicação
                </button>
                <button
                  className="button secondary"
                  disabled={reviewBusy || !reviewer.trim() || !reviewObservation.trim()}
                  onClick={() => void decideHumanReview("request_revision")}
                >
                  Solicitar revisão
                </button>
                <button
                  className="button danger"
                  disabled={reviewBusy || !reviewer.trim() || !reviewObservation.trim()}
                  onClick={() => void decideHumanReview("reject")}
                >
                  Rejeitar
                </button>
              </div>
            </div>
          ) : humanReview.decision !== "pending" ? (
            <div className="review-decision">
              <strong>Decisão registrada por {humanReview.reviewer}</strong>
              <span>
                {humanReview.reviewed_at
                  ? new Date(humanReview.reviewed_at).toLocaleString("pt-BR")
                  : "Data não registrada"}
              </span>
              {humanReview.observation && <p>{humanReview.observation}</p>}
            </div>
          ) : null}
          {(data.human_review_history || []).length > 1 && (
            <details className="review-history">
              <summary>Histórico de decisões humanas</summary>
              <ul>
                {(data.human_review_history || []).map((review) => (
                  <li key={review.id}>
                    Run {review.pipeline_run_id.slice(0, 8)} · {review.decision} · {" "}
                    {review.reviewer || "aguardando revisor"}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}
      {data.article_version && (
        <section className="panel article-result">
          <div className="panel-head">
            <div>
              <span className="eyebrow">
                {publishable ? "CONTEÚDO PUBLICÁVEL" : "PEÇA PARA REVISÃO"}
              </span>
              <h2>Artigo e relatório de transparência</h2>
              <p>
                Versão {data.article_version.version} · Run de origem:{" "}
                {data.article_pipeline_run_id || "não registrado"}
              </p>
            </div>
            <Status value={data.article_version.editorial_status} />
          </div>
          <pre>{data.article_version.markdown}</pre>
        </section>
      )}
        </>
      )}
      <ConfirmDialog
        open={cancelDialogOpen}
        title="Cancelar esta execução?"
        description="O pedido afeta somente o run selecionado. O worker concluirá a transação atual e parará na próxima fronteira segura."
        confirmLabel="Cancelar execução"
        danger
        busy={cancelling}
        onCancel={() => setCancelDialogOpen(false)}
        onConfirm={cancelSelectedRun}
      />
    </div>
  );
}
