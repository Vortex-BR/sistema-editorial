import {
  AlertTriangle,
  Bug,
  Check,
  Clipboard,
  Download,
  Filter,
  RefreshCw,
  Search,
  ServerCrash,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { adminApi, safePublicMessage } from "../lib/api";

export type ErrorLogSeverity = "warning" | "error" | "critical";
export type ErrorLogSource = "internal" | "pipeline" | "agent" | "provider" | "event";

export type ErrorLogItem = {
  id: string;
  source: ErrorLogSource;
  severity: ErrorLogSeverity;
  timestamp: string;
  stage: string;
  title: string;
  message: string | null;
  error_code: string | null;
  error_category: string | null;
  correlation_id: string | null;
  retryable: boolean | null;
  recovered: boolean;
  http_status: number | null;
  provider: string | null;
  model: string | null;
  attempt: number | null;
  operation: string | null;
  exception_type: string | null;
  sql_template: string | null;
  traceback: string | null;
  metadata: Record<string, unknown>;
};

export type ErrorLogsResponse = {
  project_id: string;
  pipeline_run_id: string | null;
  generated_at: string;
  total: number;
  summary: {
    critical: number;
    error: number;
    warning: number;
    retryable: number;
    recovered: number;
  };
  logs: ErrorLogItem[];
};

type Props = {
  projectId: string;
  pipelineRunId?: string;
  live?: boolean;
};

const SOURCE_LABELS: Record<ErrorLogSource, string> = {
  internal: "Interno",
  pipeline: "Pipeline",
  agent: "Agente",
  provider: "Provedor",
  event: "Evento",
};

const SEVERITY_LABELS: Record<ErrorLogSeverity, string> = {
  critical: "Crítico",
  error: "Erro",
  warning: "Aviso",
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("pt-BR");
}

function searchable(log: ErrorLogItem): string {
  return [
    log.title,
    log.message,
    log.stage,
    log.error_code,
    log.error_category,
    log.correlation_id,
    log.provider,
    log.model,
    log.exception_type,
    log.operation,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("pt-BR");
}

async function copyText(value: string): Promise<void> {
  if (!navigator.clipboard?.writeText) throw new Error("Área de transferência indisponível.");
  await navigator.clipboard.writeText(value);
}

export function ErrorLogsPanel({ projectId, pipelineRunId, live = false }: Props) {
  const [data, setData] = useState<ErrorLogsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [severity, setSeverity] = useState<"all" | ErrorLogSeverity>("all");
  const [source, setSource] = useState<"all" | ErrorLogSource>("all");
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState("");
  const requestSequence = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestSequence.current;
    setLoading(true);
    try {
      const scope = pipelineRunId
        ? `?pipeline_run_id=${encodeURIComponent(pipelineRunId)}&limit=200`
        : "?limit=200";
      const response = await adminApi<ErrorLogsResponse>(
        `/projects/${projectId}/error-logs${scope}`,
      );
      if (requestId !== requestSequence.current) return;
      setData(response);
      setError("");
    } catch (reason) {
      if (requestId !== requestSequence.current) return;
      setError(safePublicMessage((reason as Error).message));
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [pipelineRunId, projectId]);

  useEffect(() => {
    setData(null);
    setError("");
    void load();
    return () => {
      requestSequence.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("pt-BR");
    return (data?.logs || []).filter((log) => {
      if (severity !== "all" && log.severity !== severity) return false;
      if (source !== "all" && log.source !== source) return false;
      return !normalizedQuery || searchable(log).includes(normalizedQuery);
    });
  }, [data?.logs, query, severity, source]);

  async function copyLog(log: ErrorLogItem) {
    try {
      await copyText(JSON.stringify(log, null, 2));
      setCopied(log.id);
      window.setTimeout(() => setCopied((current) => (current === log.id ? "" : current)), 1800);
    } catch (reason) {
      setError(safePublicMessage((reason as Error).message));
    }
  }

  async function copyAll() {
    if (!data) return;
    try {
      await copyText(JSON.stringify({ ...data, logs: filtered }, null, 2));
      setCopied("all");
      window.setTimeout(() => setCopied((current) => (current === "all" ? "" : current)), 1800);
    } catch (reason) {
      setError(safePublicMessage((reason as Error).message));
    }
  }

  function downloadJson() {
    if (!data) return;
    const payload = JSON.stringify({ ...data, logs: filtered }, null, 2);
    const blob = new Blob([payload], { type: "application/json;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `logs-${projectId.slice(0, 8)}-${pipelineRunId?.slice(0, 8) || "todos"}.json`;
      anchor.hidden = true;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  }

  return (
    <section className="panel error-logs-panel" aria-label="Logs de erros">
      <div className="panel-head error-logs-head">
        <div className="title-icon">
          <ServerCrash size={22} />
          <div>
            <span className="eyebrow">DIAGNÓSTICO TÉCNICO</span>
            <h2>Logs de erros da execução</h2>
            <p>
              Erros internos, falhas de agentes, tentativas de provedores e eventos do pipeline.
            </p>
          </div>
        </div>
        <div className="error-log-actions">
          <button className="button secondary compact" onClick={() => void copyAll()} disabled={!data?.logs.length}>
            {copied === "all" ? <Check size={14} /> : <Clipboard size={14} />}
            {copied === "all" ? "Copiado" : "Copiar"}
          </button>
          <button className="button secondary compact" onClick={downloadJson} disabled={!data?.logs.length}>
            <Download size={14} />
            JSON
          </button>
          <button className="button primary compact" onClick={() => void load()} disabled={loading} aria-busy={loading}>
            <RefreshCw size={14} className={loading ? "spin" : ""} />
            Atualizar
          </button>
        </div>
      </div>

      {error && <div className="notice error error-log-notice" role="alert">{error}</div>}

      <div className="error-log-summary" aria-label="Resumo dos logs">
        <div className="critical"><span>Críticos</span><strong>{data?.summary.critical || 0}</strong></div>
        <div className="error"><span>Erros</span><strong>{data?.summary.error || 0}</strong></div>
        <div className="warning"><span>Avisos</span><strong>{data?.summary.warning || 0}</strong></div>
        <div><span>Recuperáveis</span><strong>{data?.summary.retryable || 0}</strong></div>
        <div><span>Recuperados</span><strong>{data?.summary.recovered || 0}</strong></div>
      </div>

      <div className="error-log-filters">
        <label className="error-log-search">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Buscar por código, etapa, referência ou mensagem"
            aria-label="Buscar nos logs"
          />
        </label>
        <label>
          <Filter size={14} />
          <select value={severity} onChange={(event) => setSeverity(event.target.value as typeof severity)} aria-label="Filtrar por gravidade">
            <option value="all">Todas as gravidades</option>
            <option value="critical">Crítico</option>
            <option value="error">Erro</option>
            <option value="warning">Aviso</option>
          </select>
        </label>
        <label>
          <Bug size={14} />
          <select value={source} onChange={(event) => setSource(event.target.value as typeof source)} aria-label="Filtrar por origem">
            <option value="all">Todas as origens</option>
            {Object.entries(SOURCE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </div>

      {loading && !data ? (
        <div className="empty small">Carregando logs técnicos…</div>
      ) : filtered.length === 0 ? (
        <div className="empty error-log-empty">
          <div><Check size={22} /></div>
          <h3>Nenhum erro encontrado</h3>
          <p>{data?.logs.length ? "Nenhum registro corresponde aos filtros selecionados." : "Esta execução ainda não registrou falhas técnicas."}</p>
        </div>
      ) : (
        <div className="error-log-list">
          {filtered.map((log) => (
            <details className={`error-log-entry severity-${log.severity}`} key={log.id}>
              <summary>
                <span className="error-log-icon">
                  {log.severity === "warning" ? <AlertTriangle size={16} /> : <ServerCrash size={16} />}
                </span>
                <span className="error-log-main">
                  <span>
                    <strong>{log.title}</strong>
                    <em className={`log-severity ${log.severity}`}>{SEVERITY_LABELS[log.severity]}</em>
                    {log.recovered && <em className="log-recovered">Recuperado</em>}
                  </span>
                  <small>{formatTimestamp(log.timestamp)} · {log.stage.replaceAll("_", " ")} · {SOURCE_LABELS[log.source]}</small>
                </span>
                <code>{log.error_code || log.exception_type || "sem código"}</code>
              </summary>
              <div className="error-log-body">
                {log.message && <p className="error-log-message">{log.message}</p>}
                <dl className="error-log-metadata">
                  <div><dt>Etapa</dt><dd>{log.stage}</dd></div>
                  <div><dt>Origem</dt><dd>{SOURCE_LABELS[log.source]}</dd></div>
                  <div><dt>Código</dt><dd>{log.error_code || "—"}</dd></div>
                  <div><dt>Categoria</dt><dd>{log.error_category || "—"}</dd></div>
                  <div><dt>Referência</dt><dd>{log.correlation_id || "—"}</dd></div>
                  <div><dt>HTTP</dt><dd>{log.http_status || "—"}</dd></div>
                  <div><dt>Provedor</dt><dd>{log.provider || "—"}</dd></div>
                  <div><dt>Modelo</dt><dd>{log.model || "—"}</dd></div>
                  <div><dt>Tentativa</dt><dd>{log.attempt || "—"}</dd></div>
                  <div><dt>Retry</dt><dd>{log.retryable === null ? "—" : log.retryable ? "sim" : "não"}</dd></div>
                </dl>
                {(log.exception_type || log.operation) && (
                  <div className="error-log-technical-line">
                    {log.exception_type && <code>{log.exception_type}</code>}
                    {log.operation && <span>Operação: {log.operation}</span>}
                  </div>
                )}
                {log.sql_template && (
                  <details className="error-log-code-block">
                    <summary>SQL sanitizado</summary>
                    <pre>{log.sql_template}</pre>
                  </details>
                )}
                {log.traceback && (
                  <details className="error-log-code-block">
                    <summary>Traceback sanitizado</summary>
                    <pre>{log.traceback}</pre>
                  </details>
                )}
                {Object.keys(log.metadata || {}).length > 0 && (
                  <details className="error-log-code-block">
                    <summary>Metadados</summary>
                    <pre>{JSON.stringify(log.metadata, null, 2)}</pre>
                  </details>
                )}
                <button className="button ghost compact" onClick={() => void copyLog(log)}>
                  {copied === log.id ? <Check size={14} /> : <Clipboard size={14} />}
                  {copied === log.id ? "Log copiado" : "Copiar este log"}
                </button>
              </div>
            </details>
          ))}
        </div>
      )}
      {data && <footer className="error-log-footer">{filtered.length} de {data.total} registros · atualizado em {formatTimestamp(data.generated_at)}</footer>}
    </section>
  );
}
