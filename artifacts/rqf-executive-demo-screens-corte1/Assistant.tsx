import type { CSSProperties } from "react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  Layers3,
  LockKeyhole,
  MessageSquareText,
  Paperclip,
  RefreshCw,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  UploadCloud,
  UserRound,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { OperationsWorkspaceShell } from "@/components/bi/OperationsWorkspaceShell";
import { buildAssistantHref, parseAssistantRouteContext } from "@/lib/assistantContext";
import { buildEnterpriseLoginUrl } from "@/lib/enterpriseAuth";

type Me = {
  empleado_id: string;
  nombre?: string | null;
  correo?: string | null;
  rol?: string | null;
};

type SpecialistPreviewItem = Record<string, unknown>;

type SpecialistPreviewSection = {
  section_id: string;
  title: string;
  items: SpecialistPreviewItem[];
  status?: string;
};

type SpecialistPreviewRender = {
  preview_id: string;
  task_id: string;
  title: string;
  preview_type: string;
  sections: SpecialistPreviewSection[];
  primary_action_label: string;
  primary_action_enabled: boolean;
  blocked_reason?: string | null;
  execution_status: string;
  audit_language: string;
};

type WorkspaceCard = {
  card_id: string;
  title: string;
  kind: string;
  status?: string;
  authority?: string;
  summary?: string;
  data?: Record<string, unknown>;
};

type WorkspaceStep = {
  step_id: string;
  title: string;
  kind: string;
  status?: string;
  summary?: string;
  authority?: string;
  inputs?: string[];
  outputs?: string[];
  data?: Record<string, unknown>;
};

type WorkspaceSource = {
  source_id: string;
  title: string;
  kind: string;
  status?: string;
  summary?: string;
  data?: Record<string, unknown>;
};

type AssistantSurfaceCard = {
  id: string;
  title: string;
  kind?: string;
  status?: string;
  summary?: string;
  data?: Record<string, unknown>;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  tool_trace?: Array<Record<string, unknown>>;
  tool_payload?: Record<string, unknown> | null;
  preview_render?: SpecialistPreviewRender | null;
};

type PersistedChatMessage = {
  id?: string;
  role?: string;
  content?: string | null;
  created_at?: string | null;
  tool_payload?: Record<string, unknown> | null;
};

type PendingConfirmation = {
  run_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  summary: string;
};

type RAGStatus = {
  index_path: string;
  created_at?: string;
  updated_at?: string;
  total_chunks: number;
  total_sources: number;
  sources_sample: string[];
};

type RAGResult = {
  score: number;
  source: string;
  chunk_id: string;
  text: string;
};

type RAGMetricsResponse = {
  metrics: Record<string, number>;
  cache_size: number;
  weights?: {
    doc_weight: number;
    sql_weight: number;
    recency_weight: number;
  };
  latest_eval?: RAGEvalResponse | null;
};

type RAGConfigResponse = {
  weights: {
    doc_weight: number;
    sql_weight: number;
    recency_weight: number;
  };
  presets?: Record<string, { doc_weight: number; sql_weight: number; recency_weight: number }>;
  latest_change?: RAGConfigEvent | null;
  config_path?: string;
  updated?: boolean;
};

type RAGConfigEvent = {
  timestamp?: string;
  action?: string;
  preset?: string;
  before?: Record<string, number>;
  after?: Record<string, number>;
  changed_by?: {
    empleado_id?: string;
    rol?: string;
  };
};

type RAGConfigHistoryResponse = {
  count: number;
  items: RAGConfigEvent[];
};

type RAGAutoTuneResponse = {
  applied: boolean;
  would_change: boolean;
  current_weights: {
    doc_weight: number;
    sql_weight: number;
    recency_weight: number;
  };
  recommendation: {
    preset: string;
    target_weights: {
      doc_weight: number;
      sql_weight: number;
      recency_weight: number;
    };
    reason: string;
    coverage_score: number;
    source_mix?: {
      sql_hits: number;
      doc_hits: number;
      sql_ratio: number;
      doc_ratio: number;
    };
  };
  evaluation?: {
    coverage_score: number;
    questions_total: number;
    questions_with_evidence: number;
  };
};

type RAGEvalResponse = {
  timestamp?: string;
  questions_total: number;
  questions_with_evidence: number;
  coverage_score: number;
};

type RAGEvalHistoryResponse = {
  count: number;
  items: RAGEvalResponse[];
};

type RAGCodexResponse = {
  path: string;
  exists?: boolean;
  content?: string;
  updated_at?: string | null;
  saved?: boolean;
  ingest?: {
    indexed_files?: number;
    indexed_chunks?: number;
    total_chunks?: number;
    embedding_error?: string | null;
  } | null;
};

type FinancePreset = {
  id: string;
  label: string;
  prompt: string;
};

type AssistantMode = "ahorro" | "balanceado" | "calidad";

type ExecutiveDashboard = {
  generated_at: string;
  year: number;
  scope: string;
  segment: string;
  kpis: {
    expense_total: number;
    records: number;
    prev_year_total: number;
    yoy_pct: number | null;
    run_rate_projection: number;
  };
  monthly_trend: Array<{ month: string; amount: number }>;
  top_vendors: Array<{ vendor: string; total: number }>;
};

type AlertsResponse = {
  generated_at: string;
  year: number;
  scope: string;
  segment: string;
  alerts: Array<{
    severity: string;
    code: string;
    title: string;
    detail: string;
  }>;
};

const ASSISTANT_MODE_STORAGE_KEY = "samchat_assistant_mode";
const LEGACY_PROVIDER_CREDENTIAL_STORAGE = "samchat_assistant_openai_api_key";
const SUPPORTED_ASSISTANT_MODES: AssistantMode[] = [
  "ahorro",
  "balanceado",
  "calidad",
];

const assistantThemeVars = {
  "--assistant-bg": "#f5f7fb",
  "--assistant-surface": "#ffffff",
  "--assistant-surface-elevated": "#f8fafc",
  "--assistant-text": "#111827",
  "--assistant-muted": "#64748b",
  "--assistant-border": "#d8dee9",
  "--assistant-accent": "#2563eb",
  "--assistant-success": "#15803d",
  "--assistant-warning": "#b45309",
  "--assistant-danger": "#b91c1c",
  "--assistant-radius": "18px",
  "--assistant-shadow": "0 16px 40px rgba(15, 23, 42, 0.08)",
} as CSSProperties;

const fieldClass =
  "rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface)] px-3 py-2 text-sm text-[var(--assistant-text)] shadow-sm outline-none transition focus:border-[var(--assistant-accent)] focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400";

const quietButtonClass =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface)] px-3 py-2 text-sm font-semibold text-[var(--assistant-text)] shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400";

const primaryButtonClass =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--assistant-accent)] px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500";

function formatAssistantDate(raw?: string) {
  if (!raw) return "Ahora";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString("es-MX", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatExecutiveMoney(raw?: number | null) {
  if (raw === null || raw === undefined || Number.isNaN(Number(raw))) return "-";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
    maximumFractionDigits: 0,
  }).format(Number(raw));
}

function normalizeAssistantMode(raw: string | null): AssistantMode {
  if (SUPPORTED_ASSISTANT_MODES.includes(raw as AssistantMode)) {
    return raw as AssistantMode;
  }
  return "ahorro";
}

function modeLabel(mode: AssistantMode) {
  if (mode === "calidad") return "Calidad";
  if (mode === "balanceado") return "Balanceado";
  return "Ahorro";
}


function asPreviewRender(value: unknown): SpecialistPreviewRender | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<SpecialistPreviewRender>;
  if (!candidate.preview_id || !candidate.task_id || !Array.isArray(candidate.sections)) return null;
  return candidate as SpecialistPreviewRender;
}

function previewFromPayload(payload: Record<string, unknown> | null | undefined): SpecialistPreviewRender | null {
  return asPreviewRender(payload?.preview_render);
}

function chatMessageFromHistoryRecord(record: PersistedChatMessage): ChatMessage | null {
  const role = record.role === "assistant" || record.role === "user" ? record.role : null;
  if (!role || !record.id) return null;
  const toolPayload = asRecord(record.tool_payload);
  return {
    id: String(record.id),
    role,
    content: String(record.content || ""),
    created_at: record.created_at || undefined,
    tool_payload: toolPayload,
    preview_render: previewFromPayload(toolPayload),
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asWorkspaceCards(value: unknown): WorkspaceCard[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .filter((item) => typeof item.card_id === "string" && typeof item.title === "string" && typeof item.kind === "string")
    .map((item) => ({
      card_id: String(item.card_id),
      title: String(item.title),
      kind: String(item.kind),
      status: typeof item.status === "string" ? item.status : undefined,
      authority: typeof item.authority === "string" ? item.authority : undefined,
      summary: typeof item.summary === "string" ? item.summary : undefined,
      data: asRecord(item.data) || undefined,
    }));
}

function workspaceCardsFromMessage(message: ChatMessage): WorkspaceCard[] {
  const payloadCards = asWorkspaceCards(message.tool_payload?.workspace_cards);
  if (payloadCards.length) return payloadCards;

  for (const trace of message.tool_trace || []) {
    const traceRecord = asRecord(trace);
    if (!traceRecord) continue;
    const surface = asRecord(traceRecord.specialist_preview_surface);
    const surfaceCards = asWorkspaceCards(surface?.workspace_cards);
    if (surfaceCards.length) return surfaceCards;

    const result = asRecord(traceRecord.result);
    const resultSurface = asRecord(result?.specialist_preview_surface);
    const resultCards = asWorkspaceCards(resultSurface?.workspace_cards || result?.workspace_cards);
    if (resultCards.length) return resultCards;
  }

  return [];
}

function asWorkspaceSteps(value: unknown): WorkspaceStep[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .filter((item) => typeof item.step_id === "string" && typeof item.title === "string" && typeof item.kind === "string")
    .map((item) => ({
      step_id: String(item.step_id),
      title: String(item.title),
      kind: String(item.kind),
      status: typeof item.status === "string" ? item.status : undefined,
      summary: typeof item.summary === "string" ? item.summary : undefined,
      authority: typeof item.authority === "string" ? item.authority : undefined,
      inputs: Array.isArray(item.inputs) ? item.inputs.map(String) : undefined,
      outputs: Array.isArray(item.outputs) ? item.outputs.map(String) : undefined,
      data: asRecord(item.data) || undefined,
    }));
}

function asWorkspaceSources(value: unknown): WorkspaceSource[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .filter((item) => typeof item.source_id === "string" && typeof item.title === "string" && typeof item.kind === "string")
    .map((item) => ({
      source_id: String(item.source_id),
      title: String(item.title),
      kind: String(item.kind),
      status: typeof item.status === "string" ? item.status : undefined,
      summary: typeof item.summary === "string" ? item.summary : undefined,
      data: asRecord(item.data) || undefined,
    }));
}

function workspaceStepsFromMessage(message: ChatMessage): WorkspaceStep[] {
  const payloadSteps = asWorkspaceSteps(message.tool_payload?.step_trace);
  if (payloadSteps.length) return payloadSteps;

  for (const trace of message.tool_trace || []) {
    const traceRecord = asRecord(trace);
    if (!traceRecord) continue;
    const surface = asRecord(traceRecord.specialist_preview_surface);
    const surfaceSteps = asWorkspaceSteps(surface?.step_trace);
    if (surfaceSteps.length) return surfaceSteps;

    const result = asRecord(traceRecord.result);
    const resultSurface = asRecord(result?.specialist_preview_surface);
    const resultSteps = asWorkspaceSteps(resultSurface?.step_trace || result?.step_trace);
    if (resultSteps.length) return resultSteps;
  }

  return [];
}

function workspaceSourcesFromMessage(message: ChatMessage): WorkspaceSource[] {
  const payloadSources = asWorkspaceSources(message.tool_payload?.source_panel);
  if (payloadSources.length) return payloadSources;

  for (const trace of message.tool_trace || []) {
    const traceRecord = asRecord(trace);
    if (!traceRecord) continue;
    const surface = asRecord(traceRecord.specialist_preview_surface);
    const surfaceSources = asWorkspaceSources(surface?.source_panel);
    if (surfaceSources.length) return surfaceSources;

    const result = asRecord(traceRecord.result);
    const resultSurface = asRecord(result?.specialist_preview_surface);
    const resultSources = asWorkspaceSources(resultSurface?.source_panel || result?.source_panel);
    if (resultSources.length) return resultSources;
  }

  return [];
}

function assistantPayloadRecords(message: ChatMessage): Record<string, unknown>[] {
  const records: Record<string, unknown>[] = [];
  const pushRecord = (value: unknown) => {
    const record = asRecord(value);
    if (record) records.push(record);
  };

  pushRecord(message.tool_payload);
  for (const trace of message.tool_trace || []) {
    const traceRecord = asRecord(trace);
    if (!traceRecord) continue;
    pushRecord(traceRecord.specialist_preview_surface);
    const result = asRecord(traceRecord.result);
    pushRecord(result);
    pushRecord(result?.specialist_preview_surface);
  }
  return records;
}

function surfaceCardFromValue(value: unknown, fallbackKind: string, index: number): AssistantSurfaceCard | null {
  const record = asRecord(value);
  if (!record) {
    if (value === null || value === undefined || value === "") return null;
    return { id: `${fallbackKind}-${index}`, title: previewValue(value), kind: fallbackKind };
  }

  const title = record.title || record.name || record.label || record.artifact_id || record.id || record.field || record.question || record.action || record.tool_name;
  if (!title) return null;
  return {
    id: String(record.card_id || record.artifact_id || record.id || record.source_id || `${fallbackKind}-${index}-${title}`),
    title: previewValue(title),
    kind: typeof record.kind === "string" ? record.kind : fallbackKind,
    status: typeof record.status === "string" ? record.status : undefined,
    summary: typeof record.summary === "string" ? record.summary : typeof record.description === "string" ? record.description : undefined,
    data: record,
  };
}

function cardsFromArray(value: unknown, fallbackKind: string): AssistantSurfaceCard[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item, index) => surfaceCardFromValue(item, fallbackKind, index))
    .filter((item): item is AssistantSurfaceCard => Boolean(item));
}

function ownerPackStatusCards(value: unknown): AssistantSurfaceCard[] {
  const record = asRecord(value);
  if (!record) return [];
  const keys: Array<[string, unknown]> = [
    ["torneo", record.tournament || record.torneo],
    ["entity folder", record.entity_folder || record.entityFolder],
    ["national phase", record.national_phase || record.nationalPhase],
    ["marketing", record.marketing],
  ];
  return keys
    .filter(([, item]) => item !== undefined && item !== null)
    .map(([title, item], index) => ({
      id: `owner-pack-${title}-${index}`,
      title: String(title),
      kind: "owner_pack",
      status: typeof item === "string" ? item : undefined,
      summary: typeof item === "object" ? previewValue(item) : undefined,
      data: asRecord(item) || { estado: item },
    }));
}

function artifactCardsFromMessage(message: ChatMessage): AssistantSurfaceCard[] {
  const cards: AssistantSurfaceCard[] = [];
  for (const record of assistantPayloadRecords(message)) {
    cards.push(...cardsFromArray(record.artifact_cards, "artifact"));
    cards.push(...cardsFromArray(record.artifacts, "artifact"));
    cards.push(...cardsFromArray(record.runtime_artifacts, "artifact"));
    cards.push(...ownerPackStatusCards(record.owner_pack_readiness || record.readiness));

    const review = asRecord(record.artifact_connection_review || record.connection_review);
    if (review) {
      const buckets: Array<[string, unknown]> = [
        ["Conectar ahora", review.connect_now || review.safe_to_wire_now],
        ["Mantener interno", review.keep_internal || review.maintain_internal],
        ["Fusionar", review.merge_with || review.merge_queue],
        ["Necesita datos", review.needs_data_first || review.needs_data],
        ["Obsoleto", review.obsolete],
      ];
      for (const [title, value] of buckets) {
        const items = Array.isArray(value) ? value : [];
        if (!items.length) continue;
        cards.push({
          id: `artifact-review-${title}`,
          title,
          kind: "artifact_review",
          status: title === "Conectar ahora" ? "ready" : title === "Obsoleto" ? "blocked" : "info",
          summary: `${items.length} artefacto(s)`,
          data: { items: items.slice(0, 8) },
        });
      }
    }
  }

  const seen = new Set<string>();
  return cards.filter((card) => {
    const key = `${card.id}-${card.title}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 8);
}

function missingItemsFromMessage(message: ChatMessage): AssistantSurfaceCard[] {
  const cards: AssistantSurfaceCard[] = [];
  for (const record of assistantPayloadRecords(message)) {
    cards.push(...cardsFromArray(record.missing_items, "faltante"));
    cards.push(...cardsFromArray(record.missing_fields, "faltante"));
    cards.push(...cardsFromArray(record.missing_evidence, "faltante"));
    cards.push(...cardsFromArray(record.faltantes, "faltante"));
    cards.push(...cardsFromArray(record.needs_data_first, "faltante"));
    cards.push(...cardsFromArray(record.next_questions, "pregunta"));
  }
  return cards.slice(0, 8);
}

function proposedActionsFromMessage(message: ChatMessage): AssistantSurfaceCard[] {
  const cards: AssistantSurfaceCard[] = [];
  for (const record of assistantPayloadRecords(message)) {
    cards.push(...cardsFromArray(record.proposed_actions, "accion_propuesta"));
    cards.push(...cardsFromArray(record.actions_proposed, "accion_propuesta"));
    cards.push(...cardsFromArray(record.next_actions, "accion_propuesta"));
    const proposedAction = surfaceCardFromValue(record.proposed_action, "accion_propuesta", 0);
    if (proposedAction) cards.push(proposedAction);
  }
  return cards.slice(0, 6);
}

function hasAssistantEvidenceSurface(message: ChatMessage): boolean {
  return Boolean(
    message.tool_payload ||
      (message.tool_trace && message.tool_trace.length > 0) ||
      message.preview_render ||
      workspaceCardsFromMessage(message).length ||
      workspaceStepsFromMessage(message).length ||
      workspaceSourcesFromMessage(message).length,
  );
}

function SurfaceCardPanel({
  title,
  subtitle,
  icon: Icon,
  cards,
  tone = "slate",
}: {
  title: string;
  subtitle?: string;
  icon: typeof Layers3;
  cards: AssistantSurfaceCard[];
  tone?: "slate" | "amber" | "emerald" | "blue";
}) {
  if (!cards.length) return null;
  const toneClasses = {
    slate: "border-slate-200 bg-slate-50 text-slate-700",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
    blue: "border-blue-200 bg-blue-50 text-blue-900",
  }[tone];

  return (
    <div className={`mt-3 rounded-[22px] border p-3 shadow-sm ${toneClasses}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em]">
            <Icon className="h-4 w-4" />
            {title}
          </div>
          {subtitle ? <p className="mt-1 text-xs opacity-80">{subtitle}</p> : null}
        </div>
        <span className="rounded-full border border-white/70 bg-white/80 px-3 py-1 text-xs font-semibold">
          {cards.length}
        </span>
      </div>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => {
          const rows = compactWorkspaceData(card.data);
          return (
            <div key={`${title}-${card.id}`} className="rounded-2xl border border-white/80 bg-white p-3 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h4 className="text-sm font-semibold text-slate-950">{card.title}</h4>
                  {card.kind ? <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">{card.kind.replace(/_/g, " ")}</p> : null}
                </div>
                {card.status ? (
                  <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${workspaceCardStatusClasses(card.status)}`}>
                    {card.status.replace(/_/g, " ")}
                  </span>
                ) : null}
              </div>
              {card.summary ? <p className="mt-2 text-xs leading-relaxed text-slate-600">{card.summary}</p> : null}
              {rows.length ? (
                <dl className="mt-2 space-y-1 border-t border-slate-100 pt-2">
                  {rows.slice(0, 4).map(([key, value]) => (
                    <div key={key} className="flex items-start justify-between gap-3 text-xs">
                      <dt className="capitalize text-slate-400">{key}</dt>
                      <dd className="max-w-[60%] truncate text-right font-medium text-slate-700" title={value}>{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReadOnlyAssistantBadge({ message }: { message: ChatMessage }) {
  if (!hasAssistantEvidenceSurface(message)) return null;
  return (
    <div className="mt-3 inline-flex flex-wrap items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800">
      <ShieldCheck className="h-3.5 w-3.5" />
      Consulta segura · evidencia, vistas previas y propuestas sujetas a confirmación
    </div>
  );
}

function workspaceCardStatusClasses(status?: string): string {
  const normalized = (status || "info").toLowerCase();
  if (["ready", "ok", "matched", "available", "supported", "complete"].includes(normalized)) {
    return "border-emerald-200 bg-emerald-50 text-emerald-800";
  }
  if (["blocked", "missing", "needs_more_context", "risk", "error"].includes(normalized)) {
    return "border-amber-200 bg-amber-50 text-amber-900";
  }
  if (["read_only", "preview_only", "not_authorized"].includes(normalized)) {
    return "border-slate-200 bg-slate-100 text-slate-700";
  }
  return "border-blue-200 bg-blue-50 text-blue-800";
}

function workspaceCardIcon(kind: string) {
  const normalized = kind.toLowerCase();
  if (normalized.includes("evidence") || normalized.includes("live")) return Database;
  if (normalized.includes("diagnostic") || normalized.includes("risk")) return AlertTriangle;
  if (normalized.includes("authority") || normalized.includes("approval")) return LockKeyhole;
  if (normalized.includes("preview") || normalized.includes("draft")) return FileText;
  if (normalized.includes("context") || normalized.includes("knowledge")) return Brain;
  return Layers3;
}

function compactWorkspaceData(data?: Record<string, unknown>): Array<[string, string]> {
  if (!data) return [];
  return Object.entries(data)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 4)
    .map(([key, value]) => [key.replace(/_/g, " "), previewValue(value)]);
}

function WorkspaceTracePanel({ steps, sources }: { steps: WorkspaceStep[]; sources: WorkspaceSource[] }) {
  if (!steps.length && !sources.length) return null;

  return (
    <div className="mt-3 grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
      {steps.length ? (
        <div className="rounded-[22px] border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-600">
            <Clock3 className="h-4 w-4" />
            Pasos de trabajo
          </div>
          <div className="mt-4 space-y-3">
            {steps.map((step, index) => (
              <div key={step.step_id} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <span className={`flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold ${workspaceCardStatusClasses(step.status)}`}>
                    {index + 1}
                  </span>
                  {index < steps.length - 1 ? <span className="mt-1 h-full min-h-6 w-px bg-slate-200" /> : null}
                </div>
                <div className="min-w-0 flex-1 rounded-2xl border border-slate-100 bg-slate-50 px-3 py-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h4 className="text-sm font-semibold text-slate-950">{step.title}</h4>
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">{step.kind}</p>
                    </div>
                    {step.status ? (
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${workspaceCardStatusClasses(step.status)}`}>
                        {step.status.replace(/_/g, " ")}
                      </span>
                    ) : null}
                  </div>
                  {step.summary ? <p className="mt-2 text-xs leading-relaxed text-slate-600">{step.summary}</p> : null}
                  <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-slate-500">
                    {(step.outputs || []).slice(0, 4).map((output) => (
                      <span key={output} className="rounded-full border border-slate-200 bg-white px-2 py-0.5">{output}</span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {sources.length ? (
        <div className="rounded-[22px] border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-600">
            <Database className="h-4 w-4" />
            Fuentes usadas
          </div>
          <div className="mt-4 space-y-2">
            {sources.map((source) => {
              const rows = compactWorkspaceData(source.data);
              return (
                <div key={source.source_id} className="rounded-2xl border border-slate-100 bg-slate-50 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h4 className="text-sm font-semibold text-slate-950">{source.title}</h4>
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">{source.kind}</p>
                    </div>
                    {source.status ? (
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${workspaceCardStatusClasses(source.status)}`}>
                        {source.status.replace(/_/g, " ")}
                      </span>
                    ) : null}
                  </div>
                  {source.summary ? <p className="mt-2 text-xs leading-relaxed text-slate-600">{source.summary}</p> : null}
                  {rows.length ? (
                    <dl className="mt-2 space-y-1 border-t border-slate-200 pt-2">
                      {rows.slice(0, 3).map(([key, value]) => (
                        <div key={key} className="flex items-start justify-between gap-3 text-xs">
                          <dt className="capitalize text-slate-400">{key}</dt>
                          <dd className="max-w-[60%] truncate text-right font-medium text-slate-700" title={value}>{value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function WorkspaceCardsPanel({ cards }: { cards: WorkspaceCard[] }) {
  if (!cards.length) return null;

  return (
    <div className="mt-4 rounded-[22px] border border-slate-200 bg-slate-50 p-3 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-slate-600">
            <Layers3 className="h-4 w-4" />
            Mesa de trabajo
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Contexto, evidencia, diagnostico y limites de autoridad generados por el asistente.
          </p>
        </div>
        <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600">
          {cards.length} tarjetas
        </span>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {cards.map((card) => {
          const Icon = workspaceCardIcon(card.kind);
          const rows = compactWorkspaceData(card.data);
          return (
            <div key={card.card_id} className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-blue-50 text-blue-700">
                    <Icon className="h-4 w-4" />
                  </span>
                  <div>
                    <h4 className="text-sm font-semibold text-slate-950">{card.title}</h4>
                    <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">{card.kind}</p>
                  </div>
                </div>
                {card.status ? (
                  <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${workspaceCardStatusClasses(card.status)}`}>
                    {card.status.replace(/_/g, " ")}
                  </span>
                ) : null}
              </div>
              {card.summary ? <p className="mt-3 text-xs leading-relaxed text-slate-600">{card.summary}</p> : null}
              {rows.length ? (
                <dl className="mt-3 space-y-1.5 border-t border-slate-100 pt-3">
                  {rows.map(([key, value]) => (
                    <div key={key} className="flex items-start justify-between gap-3 text-xs">
                      <dt className="capitalize text-slate-400">{key}</dt>
                      <dd className="max-w-[60%] truncate text-right font-medium text-slate-700" title={value}>
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {card.authority ? (
                <div className="mt-3 rounded-xl border border-slate-100 bg-slate-50 px-2.5 py-2 text-[11px] font-medium text-slate-500">
                  Autoridad: {card.authority.replace(/_/g, " ")}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function previewSection(preview: SpecialistPreviewRender, id: string): SpecialistPreviewSection | null {
  return preview.sections.find((section) => section.section_id === id) || null;
}

function previewValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "Si" : "No";
  if (typeof value === "number") {
    if (Number.isFinite(value) && Math.abs(value) >= 1000) {
      return new Intl.NumberFormat("es-MX", { maximumFractionDigits: 2 }).format(value);
    }
    return String(value);
  }
  if (Array.isArray(value)) return value.map(previewValue).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function previewStatusClasses(status?: string): string {
  const normalized = (status || "info").toLowerCase();
  if (normalized === "ok" || normalized === "supported") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (normalized === "blocked" || normalized === "missing" || normalized === "error") return "border-amber-200 bg-amber-50 text-amber-900";
  return "border-blue-200 bg-blue-50 text-blue-800";
}

function SpecialistPreviewCard({ preview }: { preview?: SpecialistPreviewRender | null }) {
  if (!preview) return null;
  const summary = previewSection(preview, "summary");
  const changes = previewSection(preview, "proposed_changes");
  const evidence = previewSection(preview, "evidence");
  const missingEvidence = previewSection(preview, "missing_evidence");
  const steps = previewSection(preview, "steps");
  const checks = previewSection(preview, "checks");
  const authority = previewSection(preview, "authority");
  const changeItems = (changes?.items || []).slice(0, 6);
  const evidenceItems = evidence?.items || [];
  const missingItems = missingEvidence?.items || [];
  const summaryItems = summary?.items || [];
  const agentType = summaryItems.find((item) => item.label === "agent_type")?.value;
  const capability = summaryItems.find((item) => item.label === "capability")?.value;

  return (
    <div className="mt-4 overflow-hidden rounded-[22px] border border-blue-200 bg-white shadow-sm">
      <div className="border-b border-blue-100 bg-gradient-to-r from-blue-50 via-white to-emerald-50 p-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-blue-700">
              <ShieldCheck className="h-4 w-4" />
              Preview especialista
              <span className={`rounded-full border px-2 py-0.5 normal-case tracking-normal ${previewStatusClasses(authority?.status)}`}>
                {preview.execution_status === "not_executed" ? "No ejecutado" : preview.execution_status}
              </span>
            </div>
            <h3 className="mt-2 text-base font-semibold text-slate-950">{preview.title}</h3>
            <p className="mt-1 text-xs text-slate-500">
              {preview.task_id} / {preview.preview_type}
              {agentType ? ` / agente ${previewValue(agentType)}` : ""}
              {capability ? ` / ${previewValue(capability)}` : ""}
            </p>
          </div>
          <button
            type="button"
            disabled={!preview.primary_action_enabled}
            className="inline-flex items-center justify-center rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white opacity-45 disabled:cursor-not-allowed"
            title={preview.blocked_reason || "Requiere autorizacion"}
          >
            <LockKeyhole className="mr-2 h-4 w-4" />
            {preview.primary_action_label}
          </button>
        </div>
      </div>

      <div className="grid gap-3 p-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
        <div className="space-y-3">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
            <div className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-slate-500">Cambios propuestos</div>
            {changeItems.length > 0 ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {changeItems.map((item, idx) => (
                  <div key={`${preview.preview_id}-change-${idx}`} className="rounded-xl border border-white bg-white p-3 shadow-sm">
                    <div className="flex items-start justify-between gap-2">
                      <div className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-500">
                        {previewValue(item.field || item.label || `Campo ${idx + 1}`)}
                      </div>
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${previewStatusClasses(String(item.status || changes?.status || "info"))}`}>
                        {previewValue(item.status || changes?.status || "info")}
                      </span>
                    </div>
                    <div className="mt-1 break-words text-sm font-semibold text-slate-950">{previewValue(item.value)}</div>
                    <div className="mt-2 break-all text-[11px] text-slate-500">
                      {item.evidence_id ? `Evidencia: ${previewValue(item.evidence_id)}` : "Sin evidencia ligada"}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-slate-500">No hay cambios propuestos.</div>
            )}
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-3">
            <div className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-slate-500">Plan de trabajo</div>
            <div className="grid gap-2 md:grid-cols-2">
              {(steps?.items || []).map((item, idx) => (
                <div key={`${preview.preview_id}-step-${idx}`} className="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                  <span>{previewValue(item.step || item.label || item)}</span>
                </div>
              ))}
              {(steps?.items || []).length === 0 ? <div className="text-sm text-slate-500">Sin pasos declarados.</div> : null}
            </div>
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-3">
            <div className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-emerald-800">Evidencia</div>
            <div className="flex flex-wrap gap-2">
              {evidenceItems.map((item, idx) => (
                <span key={`${preview.preview_id}-ev-${idx}`} className="rounded-full border border-emerald-200 bg-white px-2.5 py-1 text-xs font-semibold text-emerald-900">
                  {previewValue(item.evidence_id || item.label || item)}
                </span>
              ))}
              {evidenceItems.length === 0 ? <span className="text-sm text-emerald-900">Sin evidencia encontrada.</span> : null}
            </div>
            {missingItems.length > 0 ? (
              <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
                Falta: {missingItems.map((item) => previewValue(item.evidence_id || item.label || item)).join(", ")}
              </div>
            ) : null}
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-3">
            <div className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-slate-500">Checks</div>
            <div className="space-y-1.5">
              {(checks?.items || []).slice(0, 5).map((item, idx) => (
                <div key={`${preview.preview_id}-check-${idx}`} className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
                  {previewValue(item.check || item.label || item)}
                </div>
              ))}
              {(checks?.items || []).length === 0 ? <div className="text-sm text-slate-500">Sin checks.</div> : null}
            </div>
          </div>

          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-amber-950">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.14em]">
              <LockKeyhole className="h-4 w-4" />
              Frontera de autoridad
            </div>
            <p className="mt-2 text-sm leading-5">
              {preview.audit_language === "preview_only"
                ? "Vista previa unicamente. No se ejecutaron escrituras ni efectos reales."
                : `Modo: ${preview.audit_language}`}
            </p>
            <p className="mt-1 text-xs text-amber-800">Bloqueo: {preview.blocked_reason || "requiere autorizacion"}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Assistant() {
  const [urlFilters] = useSearchParams();
  const assistantSearchKey = urlFilters.toString();
  const assistantEntry = useMemo(
    () => parseAssistantRouteContext(urlFilters),
    [assistantSearchKey, urlFilters],
  );
  const [me, setMe] = useState<Me | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [assistantMode, setAssistantMode] = useState<AssistantMode>(
    () => normalizeAssistantMode(localStorage.getItem(ASSISTANT_MODE_STORAGE_KEY)),
  );
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [mediaKind, setMediaKind] = useState<"image" | "voice" | "spreadsheet" | "text">("image");
  const [mediaFile, setMediaFile] = useState<File | null>(null);
  const [mediaNote, setMediaNote] = useState("");
  const [cfdiTarget, setCfdiTarget] = useState("");
  const [financeYear, setFinanceYear] = useState<string>(String(new Date().getFullYear()));
  const [financeScope, setFinanceScope] = useState<string>("all");
  const [financeDepartment, setFinanceDepartment] = useState<string>("");
  const [financeProject, setFinanceProject] = useState<string>("");
  const [financeBudget, setFinanceBudget] = useState<string>("");
  const [executive, setExecutive] = useState<ExecutiveDashboard | null>(null);
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null);
  const [execLoading, setExecLoading] = useState(false);
  const [execError, setExecError] = useState<string | null>(null);
  const [ragStatus, setRagStatus] = useState<RAGStatus | null>(null);
  const [ragQuery, setRagQuery] = useState("");
  const [ragResults, setRagResults] = useState<RAGResult[]>([]);
  const [ragBusy, setRagBusy] = useState(false);
  const [ragIngestBusy, setRagIngestBusy] = useState(false);
  const [ragEvalBusy, setRagEvalBusy] = useState(false);
  const [ragError, setRagError] = useState<string | null>(null);
  const [ragMetrics, setRagMetrics] = useState<RAGMetricsResponse | null>(null);
  const [ragEval, setRagEval] = useState<RAGEvalResponse | null>(null);
  const [ragEvalHistory, setRagEvalHistory] = useState<RAGEvalResponse[]>([]);
  const [ragConfig, setRagConfig] = useState<RAGConfigResponse | null>(null);
  const [ragConfigBusy, setRagConfigBusy] = useState(false);
  const [ragConfigHistory, setRagConfigHistory] = useState<RAGConfigEvent[]>([]);
  const [ragAutoTune, setRagAutoTune] = useState<RAGAutoTuneResponse | null>(null);
  const [codexPath, setCodexPath] = useState<string>("");
  const [codexDraft, setCodexDraft] = useState<string>("");
  const [codexBusy, setCodexBusy] = useState(false);
  const [codexStatus, setCodexStatus] = useState<string | null>(null);
  const [ragWeightsDraft, setRagWeightsDraft] = useState({
    doc_weight: "1.0",
    sql_weight: "1.15",
    recency_weight: "0.8",
  });

  const isAdmin = useMemo(() => {
    const r = (me?.rol || "").toLowerCase();
    return r === "super_admin" || r === "superadmin";
  }, [me?.rol]);

  const routerBase = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "") || "/";
  const assistantRoute = useMemo(
    () =>
      buildAssistantHref({
        pathname: "/assistant",
        searchParams: urlFilters,
        moduleKey: assistantEntry?.moduleKey,
        moduleLabel: assistantEntry?.moduleLabel,
        moduleContext: assistantEntry?.moduleContext,
      }),
    [assistantEntry, assistantSearchKey, urlFilters],
  );
  const assistantExternalSessionId = useMemo(
    () => `assistant-web:${assistantSearchKey || "default"}`,
    [assistantSearchKey],
  );

  useEffect(() => {
    const url = new URL(window.location.href);
    const hasUrlProviderKey =
      url.searchParams.has("openai_api_key") ||
      url.searchParams.has("openai_key");
    localStorage.removeItem(LEGACY_PROVIDER_CREDENTIAL_STORAGE);
    if (!hasUrlProviderKey) return;
    url.searchParams.delete("openai_api_key");
    url.searchParams.delete("openai_key");
    window.history.replaceState(
      {},
      document.title,
      `${url.pathname}${url.search}${url.hash}`,
    );
    setAuthError(
      "Las API keys no se aceptan por URL. Usa credenciales server-side configuradas.",
    );
  }, []);

  useEffect(() => {
    localStorage.setItem(ASSISTANT_MODE_STORAGE_KEY, assistantMode);
  }, [assistantMode]);

  useEffect(() => {
    const nextYear = urlFilters.get("bi_year") || String(new Date().getFullYear());
    const nextScope = urlFilters.get("bi_scope") || "all";
    setFinanceYear(nextYear);
    setFinanceScope(nextScope);
  }, [urlFilters]);

  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    // Avoid UI hangs on network issues.
    const candidates: string[] = [path];
    // If API is mounted under the SPA base path (e.g. `/copa-america/api/...`), try that too.
    if (routerBase !== "/" && path.startsWith("/api/")) {
      candidates.push(`${routerBase}${path}`);
    }

    let lastErr: Error | null = null;
    for (const url of candidates) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 30_000);
      try {
        const body = init?.body;
        const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
        const mergedHeaders = isFormData
          ? { ...(init?.headers || {}) }
          : {
              "Content-Type": "application/json",
              ...(init?.headers || {}),
            };
        const res = await fetch(url, {
          credentials: "include",
          signal: controller.signal,
          ...init,
          headers: mergedHeaders,
        });
        if (res.status === 404 && url !== candidates[candidates.length - 1]) {
          // Try next candidate (common behind reverse proxies).
          continue;
        }
        if (res.status === 401) {
          throw new Error("UNAUTHENTICATED");
        }
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `HTTP ${res.status}`);
        }
        return (await res.json()) as T;
      } catch (e) {
        lastErr = e instanceof Error ? e : new Error(String(e));
      } finally {
        window.clearTimeout(timeout);
      }
    }
    throw lastErr || new Error("Request failed");
  }

  async function apiDownload(path: string, init?: RequestInit): Promise<Blob> {
    const candidates: string[] = [path];
    if (routerBase !== "/" && path.startsWith("/api/")) {
      candidates.push(`${routerBase}${path}`);
    }
    let lastErr: Error | null = null;
    for (const url of candidates) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 45_000);
      try {
        const mergedHeaders: Record<string, string> = {
          "Content-Type": "application/json",
          ...(init?.headers as Record<string, string> | undefined),
        };
        const res = await fetch(url, {
          credentials: "include",
          signal: controller.signal,
          ...init,
          headers: mergedHeaders,
        });
        if (res.status === 404 && url !== candidates[candidates.length - 1]) continue;
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `HTTP ${res.status}`);
        }
        return await res.blob();
      } catch (e) {
        lastErr = e instanceof Error ? e : new Error(String(e));
      } finally {
        window.clearTimeout(timeout);
      }
    }
    throw lastErr || new Error("Download failed");
  }

  async function ensureConversation(): Promise<string> {
    const created = await api<{
      conversation_id: string;
    }>("/api/assistant/conversations", {
      method: "POST",
      body: JSON.stringify({
        title: assistantEntry?.moduleLabel
          ? `Asistente · ${assistantEntry.moduleLabel}`
          : "Asistente Plataforma Sports",
        module_key: assistantEntry?.moduleKey,
        module_label: assistantEntry?.moduleLabel,
        module_context: assistantEntry?.moduleContext,
        external_session_id: assistantExternalSessionId,
      }),
    });
    return created.conversation_id;
  }

  async function loadConversationMessages(cid: string): Promise<ChatMessage[]> {
    const rows = await api<PersistedChatMessage[]>(
      `/api/assistant/conversations/${cid}/messages`
    );
    if (!Array.isArray(rows)) return [];
    return rows
      .map((row) => chatMessageFromHistoryRecord(row))
      .filter((message): message is ChatMessage => Boolean(message));
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const meData = await api<Me>("/api/assistant/me");
        if (cancelled) return;
        setMe(meData);
      } catch (e) {
        const msg = String(e);
        if (msg.includes("UNAUTHENTICATED")) {
          window.location.replace(buildEnterpriseLoginUrl(assistantRoute));
          return;
        }
        setError(msg);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!me?.empleado_id) return;
    let cancelled = false;
    (async () => {
      try {
        setBusy(true);
        setError(null);
        setHistoryError(null);
        setPending(null);
        setConversationId(null);
        const cid = await ensureConversation();
        if (cancelled) return;
        setConversationId(cid);
        setHistoryLoading(true);
        try {
          const history = await loadConversationMessages(cid);
          if (!cancelled) setMessages(history);
        } catch (historyLoadError) {
          if (!cancelled) {
            setMessages([]);
            setHistoryError(String(historyLoadError));
          }
        } finally {
          if (!cancelled) setHistoryLoading(false);
        }
      } catch (e) {
        if (cancelled) return;
        setError(String(e));
      } finally {
        if (!cancelled) {
          setBusy(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistantSearchKey, me?.empleado_id]);

  async function refreshRagStatus() {
    setRagError(null);
    try {
      const status = await api<RAGStatus>("/api/assistant/rag/status");
      setRagStatus(status);
    } catch (e) {
      setRagError(String(e));
    }
  }

  async function loadExecutiveDashboard() {
    if (!me) return;
    setExecLoading(true);
    setExecError(null);
    try {
      const [execData, alertsData] = await Promise.all([
        api<ExecutiveDashboard>("/api/assistant/reports/executive", {
          method: "POST",
          body: JSON.stringify({
            year: Number(financeYear) || new Date().getFullYear(),
            bi_scope: financeScope || "all",
          }),
        }),
        api<AlertsResponse>("/api/assistant/reports/alerts", {
          method: "POST",
          body: JSON.stringify({
            year: Number(financeYear) || new Date().getFullYear(),
            bi_scope: financeScope || "all",
          }),
        }),
      ]);
      setExecutive(execData);
      setAlerts(alertsData);
    } catch (e) {
      setExecutive(null);
      setAlerts(null);
      setExecError(String(e));
    } finally {
      setExecLoading(false);
    }
  }

  useEffect(() => {
    if (!me) return;
    void loadExecutiveDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.empleado_id, financeYear, financeScope]);

  async function sendMessageText(text: string) {
    setError(null);
    setPending(null);
    const normalized = text.trim();
    if (!normalized) return;
    if (!conversationId) {
      setError("Aún no hay conversación. Si no has iniciado sesión, entra a /login y recarga esta página.");
      return;
    }

    const optimistic: ChatMessage = {
      id: `local_${Date.now()}`,
      role: "user",
      content: normalized,
    };
    setMessages((m) => [...m, optimistic]);
    setBusy(true);
    try {
      const resp = await api<{
        assistant_message: string;
        run_id: string;
        tool_trace?: Array<Record<string, unknown>>;
        pending_confirmation?: PendingConfirmation | null;
        preview_render?: SpecialistPreviewRender | null;
      }>(`/api/assistant/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({
          message: normalized,
          assistant_mode: assistantMode,
          bi_year: Number(financeYear) || undefined,
          bi_scope: financeScope || "all",
          module_key: assistantEntry?.moduleKey,
          module_label: assistantEntry?.moduleLabel,
          module_context: assistantEntry?.moduleContext,
        }),
      });

      const trace = resp.tool_trace || [];
      setMessages((m) => [
        ...m,
        {
          id: resp.run_id,
          role: "assistant",
          content: resp.assistant_message,
          tool_trace: trace,
          preview_render: resp.preview_render || null,
        },
      ]);

      if (resp.pending_confirmation) setPending(resp.pending_confirmation);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function clearWindow() {
    if (busy) return;
    setError(null);
    setPending(null);
    setMessages([]);
    setInput("");
    setMediaFile(null);
    setMediaNote("");
    setCfdiTarget("");
    try {
      setBusy(true);
      const cid = await ensureConversation();
      setConversationId(cid);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text) return;
    setInput("");
    const handledExport = await handleInlineExportIntent(text);
    if (handledExport) return;
    await sendMessageText(text);
  }

  async function confirmWrite(approve: boolean) {
    if (!conversationId || !pending) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await api<{
        assistant_message: string;
        run_id: string;
        tool_trace?: Array<Record<string, unknown>>;
        pending_confirmation?: PendingConfirmation | null;
        preview_render?: SpecialistPreviewRender | null;
      }>(`/api/assistant/conversations/${conversationId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ run_id: pending.run_id, approve, assistant_mode: assistantMode }),
      });
      const trace = resp.tool_trace || [];
      setMessages((m) => [
        ...m,
        {
          id: resp.run_id,
          role: "assistant",
          content: resp.assistant_message,
          tool_trace: trace,
          preview_render: resp.preview_render || null,
        },
      ]);
      setPending(resp.pending_confirmation || null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function sendMedia() {
    setError(null);
    setPending(null);
    if (!conversationId) {
      setError("Aún no hay conversación.");
      return;
    }
    if (!mediaFile) {
      setError("Selecciona un archivo (imagen, voz, Excel o texto/documento).");
      return;
    }

    const previewLabel =
      mediaKind === "image"
        ? `📷 Archivo: ${mediaFile.name}`
        : mediaKind === "voice"
        ? `🎤 Archivo: ${mediaFile.name}`
        : mediaKind === "text"
        ? `📝 Archivo: ${mediaFile.name}`
        : `📊 Archivo: ${mediaFile.name}`;
    const optimistic: ChatMessage = {
      id: `local_media_${Date.now()}`,
      role: "user",
      content: mediaNote.trim() ? `${previewLabel}\nNota: ${mediaNote.trim()}` : previewLabel,
    };
    setMessages((m) => [...m, optimistic]);

    const form = new FormData();
    form.append("kind", mediaKind);
    form.append("file", mediaFile);
    if (mediaNote.trim()) form.append("note", mediaNote.trim());
    if (financeYear) form.append("bi_year", String(Number(financeYear) || ""));
    form.append("bi_scope", financeScope || "all");
    form.append("assistant_mode", assistantMode);
    if (assistantEntry?.moduleKey) form.append("module_key", assistantEntry.moduleKey);
    if (assistantEntry?.moduleLabel) form.append("module_label", assistantEntry.moduleLabel);
    if (assistantEntry?.moduleContext) {
      form.append("module_context_json", JSON.stringify(assistantEntry.moduleContext));
    }

    setBusy(true);
    try {
      const resp = await api<{
        assistant_message: string;
        run_id: string;
        tool_trace?: Array<Record<string, unknown>>;
        pending_confirmation?: PendingConfirmation | null;
        preview_render?: SpecialistPreviewRender | null;
      }>(`/api/assistant/conversations/${conversationId}/media`, {
        method: "POST",
        body: form,
      });
      const trace = resp.tool_trace || [];
      setMessages((m) => [
        ...m,
        {
          id: resp.run_id,
          role: "assistant",
          content: resp.assistant_message,
          tool_trace: trace,
          preview_render: resp.preview_render || null,
        },
      ]);
      if (resp.pending_confirmation) setPending(resp.pending_confirmation);
      setMediaFile(null);
      setMediaNote("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function requestCfdiNow() {
    const target = cfdiTarget.trim();
    if (!target) {
      setError("Ingresa número de referencia o expense_id.");
      return;
    }
    setCfdiTarget("");
    await sendMessageText(
      `Solicita CFDI ahora para el gasto ${target}. Si es numero de referencia úsalo como numero_referencia; si es UUID úsalo como expense_id.`
    );
  }

  const financePresets: FinancePreset[] = useMemo(() => {
    const y = Number(financeYear) || new Date().getFullYear();
    const yStart = `${y}-01-01`;
    const yEnd = `${y}-12-31`;
    const budgetPart = financeBudget.trim()
      ? ` budget_total=${Number(financeBudget)}`
      : "";
    const deptPart = financeDepartment.trim()
      ? ` departamento="${financeDepartment.trim()}"`
      : "";
    const projectPart = financeProject.trim()
      ? ` proyecto="${financeProject.trim()}"`
      : "";
    const scopeLabel =
      financeScope === "copa-america"
        ? "Copa América"
        : financeScope === "copa-telmex"
          ? "Copa Telmex"
          : financeScope === "beisbol"
            ? "Liga Telmex Béisbol"
            : "Todos";
    const scopePart =
      financeScope !== "all"
        ? ` Limita el analisis al ambito "${scopeLabel}" usando torneo/proyecto/departamento si aplica.`
        : "";
    return [
      {
        id: "exec",
        label: "Riesgo presupuesto",
        prompt:
          `Genera reporte ejecutivo de riesgo presupuestal para ${yStart} a ${yEnd}.` +
          `${deptPart}${projectPart}${budgetPart} group_by="proyecto" compare_years=1 projection_mode="run_rate".${scopePart}`,
      },
      {
        id: "yoy",
        label: "Comparativo anual",
        prompt:
          `Genera comparativo financiero anual para ${yStart} a ${yEnd}.` +
          `${deptPart}${projectPart} compare_years=3 group_by="concepto" projection_mode="none".${scopePart}`,
      },
      {
        id: "forecast",
        label: "Cierre estimado",
        prompt:
          `Genera proyeccion ejecutiva de cierre para ${yStart} a ${yEnd}.` +
          `${deptPart}${projectPart}${budgetPart} compare_years=2 group_by="departamento" projection_mode="run_rate".${scopePart}`,
      },
      {
        id: "vendor",
        label: "Proveedores clave",
        prompt:
          `Genera reporte ejecutivo de proveedores clave para ${yStart} a ${yEnd}.` +
          `${deptPart}${projectPart} group_by="proveedor" compare_years=1 projection_mode="none".${scopePart}`,
      },
    ];
  }, [financeYear, financeScope, financeDepartment, financeProject, financeBudget]);

  const executiveQuickLinks = [
    { label: "Owner Pack", href: "/api/assistant/owner-pack/export-preview.html", note: "Vista de evidencia y faltantes" },
    { label: "Presupuestos", href: "/admin/presupuestos", note: "Presupuesto, real y comprometido" },
    { label: "Flujo de efectivo", href: "/admin/finanzas/cashflow", note: "Caja, obligaciones y forecast" },
    { label: "Cuentas por cobrar", href: "/admin/finanzas/cuentas-por-cobrar", note: "Facturado, cobrado y pendiente" },
  ];

  const executiveDemoPrompts = [
    "¿Qué está listo para presentarle al dueño y qué evidencia falta?",
    "Dame el estado ejecutivo del Owner Pack por torneo y entidad.",
    "¿Cómo vamos de flujo de efectivo y qué riesgos hay este mes?",
    "¿Dónde hay riesgo presupuestal por torneo, real y comprometido?",
  ];

  function triggerBlobDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function extractReportResultFromMessages():
    | { report: Record<string, unknown>; runId: string | null }
    | null {
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (!lastAssistant?.tool_trace?.length) return null;
    let reportResult: Record<string, unknown> | null = null;
    for (const step of [...lastAssistant.tool_trace].reverse()) {
      const stepObj = step as Record<string, unknown>;
      const result = stepObj.result;
      if (!result || typeof result !== "object") continue;
      const obj = result as Record<string, unknown>;
      if (obj.breakdown !== undefined || obj.trend_monthly !== undefined) {
        reportResult = obj;
        break;
      }
      const rows = Array.isArray(obj.rows) ? obj.rows : null;
      if (rows && rows.length > 0 && rows.every((r) => typeof r === "object")) {
        reportResult = {
          title: `Export ${(stepObj.tool as string) || "query"}`,
          generated_at: new Date().toISOString(),
          period: {},
          totals: { registros: rows.length },
          budget: {},
          projection: {},
          breakdown: { items: rows },
          trend_monthly: [],
          comparison_yoy: [],
        };
        break;
      }
      const items = Array.isArray(obj.items) ? obj.items : null;
      if (items && items.length > 0 && items.every((r) => typeof r === "object")) {
        reportResult = {
          title: `Export ${(stepObj.tool as string) || "query"}`,
          generated_at: new Date().toISOString(),
          period: {},
          totals: { registros: items.length },
          budget: {},
          projection: {},
          breakdown: { items },
          trend_monthly: [],
          comparison_yoy: [],
        };
        break;
      }
    }
    if (!reportResult) return null;
    return {
      report: reportResult,
      runId: lastAssistant.id || null,
    };
  }

  function resolveExportIntent(raw: string): "csv" | "pdf" | null {
    const text = (raw || "").trim().toLowerCase();
    if (!text) return null;
    const asksPdf =
      text === "pdf" ||
      text.includes(" en pdf") ||
      text.includes("a pdf") ||
      text.includes("exporta pdf") ||
      text.includes("exportar pdf");
    if (asksPdf) return "pdf";

    const asksExcel =
      text === "excel" ||
      text === "xlsx" ||
      text === "csv" ||
      text.includes(" en excel") ||
      text.includes("a excel") ||
      text.includes("xlsx") ||
      text.includes("csv") ||
      text.includes("exporta excel") ||
      text.includes("exportar excel");
    if (asksExcel) return "csv";
    return null;
  }

  async function handleInlineExportIntent(raw: string): Promise<boolean> {
    const format = resolveExportIntent(raw);
    if (!format) return false;
    const extracted = extractReportResultFromMessages();
    if (!extracted) return false;
    setMessages((m) => [
      ...m,
      {
        id: `local_export_user_${Date.now()}`,
        role: "user",
        content: raw,
      },
    ]);
    const ok = await exportLatestAssistant(format, true);
    setMessages((m) => [
      ...m,
      {
        id: `local_export_assistant_${Date.now()}`,
        role: "assistant",
        content: ok
          ? format === "pdf"
            ? "Listo. Generé y descargué el reporte en PDF."
            : "Listo. Generé y descargué el reporte en Excel (CSV)."
          : "No pude exportar el reporte. Revisa el error y vuelve a intentar.",
      },
    ]);
    return true;
  }

  async function exportLatestAssistant(format: "csv" | "pdf", silent = false): Promise<boolean> {
    if (!conversationId) {
      if (!silent) setError("No hay conversación activa.");
      return false;
    }
    const extracted = extractReportResultFromMessages();
    if (!extracted) {
      if (!silent) setError("No se encontró un reporte exportable en la última respuesta.");
      return false;
    }
    try {
      const blob = await apiDownload("/api/assistant/reports/export", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: conversationId,
          run_id: extracted.runId,
          format,
          report_data: extracted.report,
          filename: `reporte_financiero_${Date.now()}.${format === "csv" ? "csv" : "pdf"}`,
        }),
      });
      triggerBlobDownload(
        blob,
        `reporte_financiero_${Date.now()}.${format === "csv" ? "csv" : "pdf"}`
      );
      return true;
    } catch (e) {
      if (!silent) setError(String(e));
      return false;
    }
  }

  async function searchRag() {
    const q = ragQuery.trim();
    if (!q) return;
    setRagBusy(true);
    setRagError(null);
    try {
      const resp = await api<{ query: string; results: RAGResult[] }>(
        "/api/assistant/rag/search",
        {
          method: "POST",
          body: JSON.stringify({ query: q, top_k: 6, min_score: 0.15 }),
        }
      );
      setRagResults(resp.results || []);
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagBusy(false);
    }
  }

  async function ingestRag() {
    if (!isAdmin) return;
    setRagIngestBusy(true);
    setRagError(null);
    try {
      await api<{
        indexed_files: number;
        indexed_chunks: number;
        total_chunks: number;
        embedding_error?: string | null;
      }>("/api/assistant/rag/ingest", {
        method: "POST",
        body: JSON.stringify({
          paths: ["docs", "reports", "codex.md"],
          reset: false,
          max_files: 200,
        }),
      });
      await refreshRagStatus();
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagIngestBusy(false);
    }
  }

  async function loadCodexDoc() {
    if (!isAdmin) return;
    setCodexStatus(null);
    try {
      const data = await api<RAGCodexResponse>("/api/assistant/rag/codex");
      setCodexPath(data.path || "");
      setCodexDraft(data.content || "");
    } catch (e) {
      setCodexStatus(String(e));
    }
  }

  async function saveCodexAndReindex() {
    if (!isAdmin) return;
    if (!codexDraft.trim()) {
      setCodexStatus("Codex vacio. Agrega contenido antes de guardar.");
      return;
    }
    setCodexBusy(true);
    setCodexStatus(null);
    try {
      const data = await api<RAGCodexResponse>("/api/assistant/rag/codex", {
        method: "PUT",
        body: JSON.stringify({
          content: codexDraft,
          auto_ingest: true,
          max_files: 200,
          paths: ["docs", "reports", "codex.md"],
        }),
      });
      setCodexPath(data.path || codexPath);
      const ingest = data.ingest;
      if (ingest) {
        setCodexStatus(
          `Guardado y reindexado. files=${ingest.indexed_files || 0}, chunks+=${ingest.indexed_chunks || 0}, total=${ingest.total_chunks || 0}${
            ingest.embedding_error ? `, embedding_error=${ingest.embedding_error}` : ""
          }`
        );
      } else {
        setCodexStatus("Guardado.");
      }
      await refreshRagStatus();
      await loadRagMetrics();
    } catch (e) {
      setCodexStatus(String(e));
    } finally {
      setCodexBusy(false);
    }
  }

  async function loadRagMetrics() {
    if (!isAdmin) return;
    setRagError(null);
    try {
      const data = await api<RAGMetricsResponse>("/api/assistant/rag/metrics");
      setRagMetrics(data);
    } catch (e) {
      setRagError(String(e));
    }
  }

  async function runRagEval() {
    if (!isAdmin) return;
    setRagEvalBusy(true);
    setRagError(null);
    try {
      const data = await api<RAGEvalResponse>("/api/assistant/rag/eval", {
        method: "POST",
        body: JSON.stringify({}),
      });
      setRagEval(data);
      await loadRagEvalHistory();
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagEvalBusy(false);
    }
  }

  async function loadRagConfig() {
    if (!isAdmin) return;
    setRagError(null);
    try {
      const data = await api<RAGConfigResponse>("/api/assistant/rag/config");
      setRagConfig(data);
      setRagWeightsDraft({
        doc_weight: String(data.weights.doc_weight),
        sql_weight: String(data.weights.sql_weight),
        recency_weight: String(data.weights.recency_weight),
      });
    } catch (e) {
      setRagError(String(e));
    }
  }

  async function saveRagConfig() {
    if (!isAdmin) return;
    setRagConfigBusy(true);
    setRagError(null);
    try {
      const payload = {
        doc_weight: Number(ragWeightsDraft.doc_weight),
        sql_weight: Number(ragWeightsDraft.sql_weight),
        recency_weight: Number(ragWeightsDraft.recency_weight),
      };
      const data = await api<RAGConfigResponse>("/api/assistant/rag/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setRagConfig(data);
      await loadRagMetrics();
      await loadRagConfigHistory();
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagConfigBusy(false);
    }
  }

  async function loadRagEvalHistory() {
    if (!isAdmin) return;
    setRagError(null);
    try {
      const data = await api<RAGEvalHistoryResponse>("/api/assistant/rag/eval/history?limit=8");
      setRagEvalHistory(data.items || []);
    } catch (e) {
      setRagError(String(e));
    }
  }

  async function loadRagConfigHistory() {
    if (!isAdmin) return;
    setRagError(null);
    try {
      const data = await api<RAGConfigHistoryResponse>("/api/assistant/rag/config/history?limit=8");
      setRagConfigHistory(data.items || []);
    } catch (e) {
      setRagError(String(e));
    }
  }

  async function applyRagPreset(preset: string) {
    if (!isAdmin) return;
    setRagConfigBusy(true);
    setRagError(null);
    try {
      const data = await api<RAGConfigResponse>("/api/assistant/rag/config/preset", {
        method: "POST",
        body: JSON.stringify({ preset }),
      });
      setRagConfig(data);
      setRagWeightsDraft({
        doc_weight: String(data.weights.doc_weight),
        sql_weight: String(data.weights.sql_weight),
        recency_weight: String(data.weights.recency_weight),
      });
      await loadRagMetrics();
      await loadRagConfigHistory();
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagConfigBusy(false);
    }
  }

  async function resetRagConfig() {
    if (!isAdmin) return;
    setRagConfigBusy(true);
    setRagError(null);
    try {
      const data = await api<RAGConfigResponse>("/api/assistant/rag/config/reset", {
        method: "POST",
      });
      setRagConfig(data);
      setRagWeightsDraft({
        doc_weight: String(data.weights.doc_weight),
        sql_weight: String(data.weights.sql_weight),
        recency_weight: String(data.weights.recency_weight),
      });
      await loadRagMetrics();
      await loadRagConfigHistory();
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagConfigBusy(false);
    }
  }

  async function suggestAutoTune(apply: boolean) {
    if (!isAdmin) return;
    setRagConfigBusy(true);
    setRagError(null);
    try {
      const data = await api<RAGAutoTuneResponse>("/api/assistant/rag/config/auto-tune", {
        method: "POST",
        body: JSON.stringify({ apply }),
      });
      setRagAutoTune(data);
      if (apply) {
        setRagWeightsDraft({
          doc_weight: String(data.current_weights.doc_weight),
          sql_weight: String(data.current_weights.sql_weight),
          recency_weight: String(data.current_weights.recency_weight),
        });
        await loadRagConfig();
        await loadRagConfigHistory();
      }
      await loadRagMetrics();
      await loadRagEvalHistory();
    } catch (e) {
      setRagError(String(e));
    } finally {
      setRagConfigBusy(false);
    }
  }

  if (authError) {
    return (
      <OperationsWorkspaceShell
        workspace="assistant"
        title="Acceso requerido"
        description={authError}
        actions={
          <>
            <Button onClick={() => window.location.replace(buildEnterpriseLoginUrl(assistantRoute))}>Ir a /login</Button>
            <Button asChild variant="outline">
              <a href={assistantRoute}>Volver al asistente</a>
            </Button>
          </>
        }
        stats={[
          { label: "Destino", value: "/login", hint: "La autenticación vuelve a la consola interna." },
          { label: "Retorno", value: assistantRoute, hint: "Ruta del asistente en esta instancia." },
        ]}
      >
        <Card className="rounded-[26px] border border-border/70 bg-card p-5">
          <p className="text-sm text-muted-foreground">
            Nota: el login redirige a <span className="font-mono">/platform/panel</span>; después vuelve a{" "}
            <span className="font-mono">{assistantRoute}</span>.
          </p>
        </Card>
      </OperationsWorkspaceShell>
    );
  }

  return (
    <OperationsWorkspaceShell
      workspace="assistant"
      title="Asistente Ejecutivo"
      description="Consola para preparar respuestas ejecutivas con evidencia, faltantes, tableros financieros y propuestas que requieren confirmación humana."
      actions={
        <>
          <Button variant="outline" onClick={() => void clearWindow()} disabled={busy}>
            <RefreshCw className="h-4 w-4" />
            Nueva ventana
          </Button>
          <Button asChild variant="outline">
            <a href="/RAG">
              <Database className="h-4 w-4" />
              Abrir RAG
            </a>
          </Button>
        </>
      }
      aside={
        <div style={assistantThemeVars} className="space-y-3 text-sm text-[var(--assistant-text)]">
          <div className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
              <UserRound className="h-3.5 w-3.5" />
              Sesión
            </div>
            <div className="mt-2 font-semibold">
              {me ? `${me.nombre || me.correo || me.empleado_id}` : "Conectando..."}
            </div>
            <div className="text-sm text-[var(--assistant-muted)]">{me?.rol || "empleado"}</div>
          </div>
          <label className="flex flex-col gap-2 rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
            <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Modo
            </span>
            <select
              value={assistantMode}
              onChange={(e) => setAssistantMode(e.target.value as AssistantMode)}
              className={fieldClass}
              disabled={busy}
            >
              <option value="ahorro">Ahorro</option>
              <option value="balanceado">Balanceado</option>
              <option value="calidad">Calidad</option>
            </select>
            <span className="text-xs text-[var(--assistant-muted)]">
              Ajusta costo, latencia y profundidad sin cambiar la ruta de ejecución.
            </span>
          </label>
          <div className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
              <MessageSquareText className="h-3.5 w-3.5" />
              Conversación
            </div>
            <div className="mt-2 break-all rounded-xl bg-[var(--assistant-surface-elevated)] px-3 py-2 font-mono text-xs">
              {conversationId || "Creando conversación..."}
            </div>
          </div>
          {assistantEntry ? (
            <div className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
                <Layers3 className="h-3.5 w-3.5" />
                Contexto de entrada
              </div>
              <div className="mt-2 text-sm font-semibold">{assistantEntry.moduleLabel}</div>
              <div className="mt-1 break-all font-mono text-[11px] text-[var(--assistant-muted)]">{assistantEntry.moduleKey}</div>
            </div>
          ) : null}
          <div className="rounded-[var(--assistant-radius)] border border-blue-200 bg-blue-50 p-4 text-blue-950">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em]">
              <ShieldCheck className="h-4 w-4" />
              Seguridad
            </div>
            <p className="mt-2 text-xs leading-5 text-blue-900">
              Las acciones sensibles se presentan para revisión. La confirmación visible conserva el flujo existente.
            </p>
          </div>
        </div>
      }
      stats={[
        { label: "Mensajes", value: String(messages.length), hint: "Mensajes visibles en la sesión actual." },
        { label: "Confirmaciones", value: pending ? "1 pendiente" : "Sin pendientes", hint: "Acciones que requieren aprobación visible." },
        { label: "Año", value: financeYear, hint: "Filtro financiero actual." },
        { label: "Evidencia", value: executive ? "Activa" : "Pendiente", hint: "Resumen ejecutivo cargado para la sesión." },
      ]}
    >
      <div style={assistantThemeVars} className="space-y-4 text-[var(--assistant-text)]">
        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-5 shadow-[var(--assistant-shadow)]">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
              <div className="max-w-3xl">
                <div className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-bold uppercase tracking-[0.14em] text-emerald-800">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Demo ejecutivo
                </div>
                <h2 className="mt-3 text-2xl font-semibold tracking-normal text-[var(--assistant-text)]">
                  Responde con evidencia, muestra faltantes y abre los tableros correctos.
                </h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--assistant-muted)]">
                  Punto de entrada para revisar Owner Pack, presupuestos, flujo de efectivo y cuentas por cobrar sin perder el contexto de la conversación.
                </p>
                <div className="mt-5 grid gap-2 md:grid-cols-2">
                  {executiveDemoPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => setInput(prompt)}
                      className="min-w-0 rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3 text-left text-sm font-semibold text-[var(--assistant-text)] transition hover:border-emerald-300 hover:bg-emerald-50"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid min-w-[260px] gap-2 sm:grid-cols-2 lg:grid-cols-1">
                {executiveQuickLinks.map((item) => (
                  <a
                    key={item.href}
                    href={item.href}
                    className="rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3 transition hover:border-blue-300 hover:bg-blue-50"
                  >
                    <div className="text-sm font-semibold text-[var(--assistant-text)]">{item.label}</div>
                    <div className="mt-1 text-xs leading-5 text-[var(--assistant-muted)]">{item.note}</div>
                  </a>
                ))}
              </div>
            </div>
          </Card>

          <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-5 shadow-sm">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
              <Database className="h-4 w-4" />
              Lectura ejecutiva
            </div>
            <div className="mt-4 space-y-3">
              {[
                ["Gasto observado", executive ? formatExecutiveMoney(executive.kpis.expense_total) : "-"],
                ["Proyección cierre", executive ? formatExecutiveMoney(executive.kpis.run_rate_projection) : "-"],
                ["Alertas", alerts?.alerts?.length ? String(alerts.alerts.length) : "Sin señales"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3">
                  <p className="text-xs font-medium text-[var(--assistant-muted)]">{label}</p>
                  <p className="mt-1 text-lg font-semibold text-[var(--assistant-text)]">{value}</p>
                </div>
              ))}
            </div>
          </Card>
        </section>

        {error ? (
          <Card className="rounded-[var(--assistant-radius)] border border-red-200 bg-red-50 p-4 text-sm text-[var(--assistant-danger)] shadow-sm">
            <div className="flex items-start gap-3">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
              <div className="whitespace-pre-wrap">{error}</div>
            </div>
          </Card>
        ) : null}

        {historyError ? (
          <Card className="rounded-[var(--assistant-radius)] border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 shadow-sm">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
              <div className="whitespace-pre-wrap">
                No se pudo cargar el historial: {historyError}
              </div>
            </div>
          </Card>
        ) : null}

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <Card className="overflow-hidden rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] shadow-[var(--assistant-shadow)]">
            <div className="border-b border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] px-4 py-3 sm:px-5">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
                    <Brain className="h-4 w-4" />
                    Conversación operativa
                  </div>
                  <h2 className="mt-1 text-xl font-semibold tracking-normal text-[var(--assistant-text)]">
                    Pregunta, verifica y prepara acciones con contexto
                  </h2>
                </div>
                <div className="flex flex-wrap gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-800">
                    <Clock3 className="h-3.5 w-3.5" />
                    {busy ? "Procesando" : historyLoading ? "Cargando historial" : "Listo"}
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                    <SlidersHorizontal className="h-3.5 w-3.5" />
                    {modeLabel(assistantMode)}
                  </span>
                </div>
              </div>
            </div>

            <div className="min-h-[430px] bg-[var(--assistant-bg)] p-3 sm:p-5">
              {historyLoading ? (
                <div className="flex min-h-[360px] items-center justify-center rounded-[var(--assistant-radius)] border border-dashed border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-6 text-center">
                  <div className="max-w-md">
                    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-[var(--assistant-accent)]">
                      <RefreshCw className="h-6 w-6 animate-spin" />
                    </div>
                    <h3 className="mt-4 text-lg font-semibold text-[var(--assistant-text)]">
                      Cargando historial
                    </h3>
                    <p className="mt-2 text-sm leading-6 text-[var(--assistant-muted)]">
                      Recuperando mensajes persistidos y superficies de trabajo.
                    </p>
                  </div>
                </div>
              ) : messages.length === 0 ? (
                <div className="flex min-h-[360px] items-center justify-center rounded-[var(--assistant-radius)] border border-dashed border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-6 text-center">
                  <div className="max-w-2xl">
                    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-[var(--assistant-accent)]">
                      <Bot className="h-6 w-6" />
                    </div>
                    <h3 className="mt-4 text-lg font-semibold text-[var(--assistant-text)]">
                      Empieza con una pregunta verificable
                    </h3>
                    <p className="mt-2 text-sm leading-6 text-[var(--assistant-muted)]">
                      Usa el asistente para leer datos operativos, preparar reportes, revisar evidencia o crear una solicitud que requiera confirmación.
                    </p>
                    <div className="mt-5 grid gap-2 text-left sm:grid-cols-2">
                      {executiveDemoPrompts.map((sample) => (
                        <button
                          key={sample}
                          type="button"
                          onClick={() => setInput(sample)}
                          className="min-w-0 rounded-xl border border-[var(--assistant-border)] bg-white p-3 text-left text-sm text-[var(--assistant-text)] transition hover:border-blue-300 hover:bg-blue-50"
                        >
                          {sample}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  {messages.map((m) => (
                    <article
                      key={m.id}
                      className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-[96%] min-w-0 rounded-[var(--assistant-radius)] border p-3 shadow-sm sm:max-w-[82%] sm:p-4 ${
                          m.role === "user"
                            ? "border-blue-700 bg-blue-700 text-white"
                            : "border-[var(--assistant-border)] bg-[var(--assistant-surface)] text-[var(--assistant-text)]"
                        }`}
                      >
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em]">
                            {m.role === "user" ? <UserRound className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                            {m.role === "user" ? "Operador" : "SamChat"}
                          </div>
                          <div className={m.role === "user" ? "text-xs text-blue-100" : "text-xs text-[var(--assistant-muted)]"}>
                            {formatAssistantDate(m.created_at)}
                          </div>
                        </div>
                        <div className="break-words whitespace-pre-wrap text-sm leading-6">{m.content}</div>
                        {m.role === "assistant" ? (
                          <>
                            <ReadOnlyAssistantBadge message={m} />
                            <SurfaceCardPanel
                              title="Faltantes"
                              subtitle="Datos, evidencia o preguntas que el asistente todavía no puede resolver."
                              icon={AlertTriangle}
                              cards={missingItemsFromMessage(m)}
                              tone="amber"
                            />
                            <SurfaceCardPanel
                              title="Acciones propuestas"
                              subtitle="Borradores o siguientes pasos; requieren autoridad humana antes de ejecutar."
                              icon={LockKeyhole}
                              cards={proposedActionsFromMessage(m)}
                              tone="blue"
                            />
                            <SurfaceCardPanel
                              title="Artefactos"
                              subtitle="Paquetes, revisiones o superficies de trabajo detectadas en la respuesta."
                              icon={FileText}
                              cards={artifactCardsFromMessage(m)}
                              tone="emerald"
                            />
                            <WorkspaceCardsPanel cards={workspaceCardsFromMessage(m)} />
                            <WorkspaceTracePanel steps={workspaceStepsFromMessage(m)} sources={workspaceSourcesFromMessage(m)} />
                            <SpecialistPreviewCard preview={m.preview_render || previewFromPayload(m.tool_payload)} />
                          </>
                        ) : null}
                        {m.role === "assistant" && m.tool_trace && m.tool_trace.length > 0 ? (
                          <details className="mt-3 rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3">
                            <summary className="cursor-pointer text-xs font-semibold text-[var(--assistant-text)]">
                              Trazas de herramienta ({m.tool_trace.length})
                            </summary>
                            <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-3 text-[11px] leading-5 text-slate-100">
                              {JSON.stringify(m.tool_trace, null, 2)}
                            </pre>
                          </details>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>

            <div className="border-t border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-3 sm:p-4">
              <div className="flex flex-col gap-3 lg:flex-row">
                <label className="sr-only" htmlFor="assistant-message-input">
                  Mensaje para SamChat Assistant
                </label>
                <textarea
                  id="assistant-message-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Escribe una pregunta, una verificación o una solicitud para preparar..."
                  rows={4}
                  className="min-h-[96px] w-full flex-1 resize-y rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3 text-sm leading-6 text-[var(--assistant-text)] outline-none transition placeholder:text-[var(--assistant-muted)] focus:border-[var(--assistant-accent)] focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-100 sm:min-h-[112px] sm:p-4"
                  disabled={busy}
                />
                <button
                  type="button"
                  onClick={send}
                  disabled={busy || !conversationId || !input.trim()}
                  className={`${primaryButtonClass} w-full lg:w-36`}
                >
                  <Send className="h-4 w-4" />
                  {busy ? "Procesando" : "Enviar"}
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--assistant-muted)]">
                <ShieldCheck className="h-3.5 w-3.5" />
                Las solicitudes sensibles deben pasar por el flujo de confirmación existente.
              </div>
            </div>
          </Card>

          <aside className="space-y-4">
            <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
                <ShieldCheck className="h-4 w-4" />
                Estado y límites
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between gap-3 rounded-xl bg-[var(--assistant-surface-elevated)] px-3 py-2">
                  <span className="text-sm text-[var(--assistant-muted)]">Confirmaciones</span>
                  <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${pending ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"}`}>
                    {pending ? "Pendiente" : "Sin pendientes"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3 rounded-xl bg-[var(--assistant-surface-elevated)] px-3 py-2">
                  <span className="text-sm text-[var(--assistant-muted)]">Adjuntos</span>
                  <span className="max-w-[58%] truncate rounded-full bg-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-700">
                    {mediaFile ? mediaFile.name : "Sin archivo"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3 rounded-xl bg-[var(--assistant-surface-elevated)] px-3 py-2">
                  <span className="text-sm text-[var(--assistant-muted)]">Contexto BI</span>
                  <span className="rounded-full bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-800">
                    {financeYear} · {financeScope}
                  </span>
                </div>
              </div>
            </Card>

            {pending ? (
              <Card className="rounded-[var(--assistant-radius)] border border-amber-300 bg-amber-50 p-4 text-amber-950 shadow-sm">
                <div className="flex items-center gap-2 font-bold">
                  <AlertTriangle className="h-5 w-5" />
                  Confirmación requerida
                </div>
                <div className="mt-3 whitespace-pre-wrap rounded-xl bg-white/70 p-3 font-mono text-xs">
                  {pending.summary}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy || !isAdmin}
                    onClick={() => confirmWrite(true)}
                    className={`${isAdmin ? primaryButtonClass : quietButtonClass} ${!isAdmin ? "cursor-not-allowed opacity-60" : ""}`}
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    Confirmar
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => confirmWrite(false)}
                    className={quietButtonClass}
                  >
                    <XCircle className="h-4 w-4" />
                    Cancelar
                  </button>
                </div>
                {!isAdmin ? (
                  <div className="mt-3 flex items-center gap-2 text-xs text-amber-800">
                    <LockKeyhole className="h-3.5 w-3.5" />
                    Solo superadmin puede confirmar escrituras.
                  </div>
                ) : null}
              </Card>
            ) : (
              <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
                <div className="flex items-center gap-2 font-semibold">
                  <CheckCircle2 className="h-5 w-5 text-[var(--assistant-success)]" />
                  Sin acción pendiente
                </div>
                <p className="mt-2 text-sm leading-6 text-[var(--assistant-muted)]">
                  Si el asistente prepara una acción sensible, aparecerá aquí para revisión antes de continuar.
                </p>
              </Card>
            )}

            <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
                <Paperclip className="h-4 w-4" />
                Adjuntar evidencia
              </div>
              <div className="mt-3 space-y-3">
                <select
                  value={mediaKind}
                  onChange={(e) => setMediaKind(e.target.value as "image" | "voice" | "spreadsheet" | "text")}
                  className={`${fieldClass} w-full`}
                  disabled={busy}
                >
                  <option value="image">Imagen</option>
                  <option value="voice">Voz</option>
                  <option value="spreadsheet">Excel/CSV</option>
                  <option value="text">Texto</option>
                </select>
                <label className="block rounded-xl border border-dashed border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-4 text-center text-sm text-[var(--assistant-muted)]">
                  <UploadCloud className="mx-auto mb-2 h-5 w-5" />
                  <span className="block font-semibold text-[var(--assistant-text)]">
                    {mediaFile ? mediaFile.name : "Seleccionar archivo"}
                  </span>
                  <input
                    type="file"
                    accept={
                      mediaKind === "image"
                        ? "image/*"
                        : mediaKind === "voice"
                        ? "audio/*"
                        : mediaKind === "text"
                        ? ".txt,.md,.markdown,.doc,.docx,text/plain,text/markdown,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        : ".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
                    }
                    onChange={(e) => setMediaFile(e.target.files?.[0] || null)}
                    className="sr-only"
                    disabled={busy}
                  />
                </label>
                <textarea
                  value={mediaNote}
                  onChange={(e) => setMediaNote(e.target.value)}
                  placeholder="Nota opcional para el asistente."
                  rows={3}
                  className={`${fieldClass} w-full resize-y`}
                  disabled={busy}
                />
                <button
                  type="button"
                  onClick={sendMedia}
                  disabled={busy || !conversationId || !mediaFile}
                  className={`${primaryButtonClass} w-full`}
                >
                  <UploadCloud className="h-4 w-4" />
                  {busy ? "Procesando" : "Enviar archivo"}
                </button>
              </div>
            </Card>
          </aside>
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm sm:p-5">
            <div className="flex flex-col gap-3 border-b border-[var(--assistant-border)] pb-4 md:flex-row md:items-start md:justify-between">
              <div>
                <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
                  <Database className="h-4 w-4" />
                  Resumen financiero
                </div>
                <h2 className="mt-1 text-lg font-semibold text-[var(--assistant-text)]">
                  Gasto, tendencia y señales de atención
                </h2>
              </div>
              <button
                type="button"
                onClick={() => void loadExecutiveDashboard()}
                disabled={busy || execLoading}
                className={quietButtonClass}
              >
                <RefreshCw className="h-4 w-4" />
                {execLoading ? "Actualizando" : "Actualizar"}
              </button>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                ["Gasto total", executive ? formatExecutiveMoney(executive.kpis.expense_total) : "-"],
                ["Registros", executive ? executive.kpis.records.toLocaleString() : "-"],
                ["Año anterior", executive ? formatExecutiveMoney(executive.kpis.prev_year_total) : "-"],
                ["YoY %", executive?.kpis.yoy_pct ?? "-"],
                ["Proyección cierre", executive ? formatExecutiveMoney(executive.kpis.run_rate_projection) : "-"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3">
                  <p className="text-xs font-medium text-[var(--assistant-muted)]">{label}</p>
                  <p className="mt-1 text-base font-semibold text-[var(--assistant-text)]">{value}</p>
                </div>
              ))}
            </div>
            <div className="mt-5">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-[var(--assistant-text)]">
                <AlertTriangle className="h-4 w-4 text-[var(--assistant-warning)]" />
                Señales de atención
              </div>
              {execError ? (
                <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-[var(--assistant-danger)]">
                  No se pudo cargar el resumen financiero: {execError}
                </div>
              ) : alerts?.alerts?.length ? (
                <div className="grid gap-2 md:grid-cols-2">
                  {alerts.alerts.slice(0, 4).map((item, idx) => (
                    <div key={`${item.code}_${idx}`} className="rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3">
                      <p className="text-xs font-semibold text-[var(--assistant-text)]">
                        {item.severity.toUpperCase()} · {item.title}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-[var(--assistant-muted)]">{item.detail}</p>
                    </div>
                  ))}
                </div>
              ) : !execLoading ? (
                <p className="rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3 text-sm text-[var(--assistant-muted)]">
                  Sin señales activas para el filtro actual.
                </p>
              ) : null}
            </div>
          </Card>

          <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm sm:p-5">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
              <FileText className="h-4 w-4" />
              CFDI
            </div>
            <h2 className="mt-1 text-lg font-semibold text-[var(--assistant-text)]">Solicitud rápida</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--assistant-muted)]">
              Prepara una petición de CFDI usando la referencia o expense_id existente.
            </p>
            <div className="mt-4 space-y-3">
              <input
                value={cfdiTarget}
                onChange={(e) => setCfdiTarget(e.target.value)}
                placeholder="Número de referencia o expense_id"
                className={`${fieldClass} w-full`}
                disabled={busy}
              />
              <button
                type="button"
                onClick={requestCfdiNow}
                disabled={busy || !conversationId || !cfdiTarget.trim()}
                className={`${primaryButtonClass} w-full`}
              >
                <FileText className="h-4 w-4" />
                Solicitar CFDI
              </button>
            </div>
          </Card>
        </section>

        <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm sm:p-5">
          <div className="flex flex-col gap-2 border-b border-[var(--assistant-border)] pb-4 md:flex-row md:items-start md:justify-between">
            <div>
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-[var(--assistant-muted)]">
                <Layers3 className="h-4 w-4" />
                Centro de reportes ejecutivos
              </div>
              <h2 className="mt-1 text-lg font-semibold text-[var(--assistant-text)]">
                Filtros, preguntas guiadas y exportación del último reporte
              </h2>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => void exportLatestAssistant("csv")} disabled={busy} className={quietButtonClass}>
                Exportar CSV
              </button>
              <button type="button" onClick={() => void exportLatestAssistant("pdf")} disabled={busy} className={quietButtonClass}>
                Exportar PDF
              </button>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-5">
            <input
              value={financeYear}
              onChange={(e) => setFinanceYear(e.target.value)}
              placeholder="Año del análisis"
              className={fieldClass}
              disabled={busy}
            />
            <select value={financeScope} onChange={(e) => setFinanceScope(e.target.value)} className={fieldClass} disabled={busy}>
              <option value="all">Ámbito: Todos</option>
              <option value="copa-america">Ámbito: Copa América</option>
              <option value="copa-telmex">Ámbito: Copa Telmex</option>
              <option value="beisbol">Ámbito: Liga Telmex Béisbol</option>
            </select>
            <input
              value={financeDepartment}
              onChange={(e) => setFinanceDepartment(e.target.value)}
              list="finance-department-options"
              placeholder="Departamento"
              className={fieldClass}
              disabled={busy}
            />
            <input
              value={financeProject}
              onChange={(e) => setFinanceProject(e.target.value)}
              list="finance-project-options"
              placeholder="Proyecto"
              className={fieldClass}
              disabled={busy}
            />
            <input
              value={financeBudget}
              onChange={(e) => setFinanceBudget(e.target.value)}
              list="finance-budget-options"
              placeholder="Presupuesto"
              className={fieldClass}
              disabled={busy}
            />
          </div>
          <datalist id="finance-department-options">
            <option value="Operaciones" />
            <option value="Finanzas" />
            <option value="Contabilidad" />
            <option value="Mercadotecnia" />
            <option value="Logística" />
            <option value="Arbitraje" />
            <option value="Médico" />
          </datalist>
          <datalist id="finance-project-options">
            <option value="Copa Telmex Fútbol" />
            <option value="Liga Telmex Béisbol" />
            <option value="Copa América" />
            <option value="Fase Estatal" />
            <option value="Fase Nacional" />
            <option value="Activación de Marca" />
          </datalist>
          <datalist id="finance-budget-options">
            <option value="250000" />
            <option value="500000" />
            <option value="1000000" />
            <option value="2000000" />
            <option value="5000000" />
            <option value="10000000" />
          </datalist>
          <div className="mt-4 grid gap-2 md:grid-cols-4">
            {financePresets.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => void sendMessageText(p.prompt)}
                disabled={busy || !conversationId}
                className="rounded-xl border border-[var(--assistant-border)] bg-[var(--assistant-surface-elevated)] p-3 text-left text-sm font-semibold text-[var(--assistant-text)] transition hover:border-blue-300 hover:bg-blue-50 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
              >
                {p.label}
              </button>
            ))}
          </div>
          <p className="mt-3 text-xs text-[var(--assistant-muted)]">
            También puedes pedirlo directo en el chat: "¿Dónde hay riesgo presupuestal?" o "¿Cómo vamos de flujo de efectivo?".
          </p>
        </Card>

        <Card className="rounded-[var(--assistant-radius)] border border-[var(--assistant-border)] bg-[var(--assistant-surface)] p-4 shadow-sm sm:p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-[var(--assistant-text)]">
                <Database className="h-4 w-4" />
                RAG movido a página dedicada
              </div>
              <p className="mt-1 text-sm text-[var(--assistant-muted)]">
                La administración de conocimiento y configuración de ranking permanece aislada.
              </p>
            </div>
            <a href="/RAG" className={quietButtonClass}>
              Ir a /RAG
            </a>
          </div>
        </Card>
      </div>
    </OperationsWorkspaceShell>
  );
}
