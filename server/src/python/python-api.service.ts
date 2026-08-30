import { Injectable, Logger, ServiceUnavailableException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  CompanyDescription,
  ConfirmSyncResponse,
  PlanDecision,
  PlanSyncResponse,
  PythonDomain,
  SseFrame,
} from './python-api.types';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';

/**
 * Server-to-server client for `llm_backend`'s FastAPI service.
 *
 * The Python tier has no authentication and binds 127.0.0.1 on purpose — it is
 * only ever reached from here, never from the browser. Every route takes its
 * selectors (`company`, `source_id`, `run_id`) as query parameters; none of them
 * has a path parameter.
 */
@Injectable()
export class PythonApiService {
  private readonly logger = new Logger(PythonApiService.name);
  private readonly baseUrl: string;

  constructor(config: ConfigService) {
    this.baseUrl = (
      config.get<string>('PYTHON_API_URL') ?? DEFAULT_BASE_URL
    ).replace(/\/+$/, '');
  }

  /** `POST /companies` — provision a tenant. Always seeded from `blank`. */
  async createCompany(params: {
    companyId: string;
    displayName: string;
    supabaseUserIds: string[];
  }): Promise<CompanyDescription> {
    // `blank` declares no KPIs, so the tenant never claims a metric its file
    // cannot produce — the domain label the wizard carries is never used here.
    const domains: PythonDomain[] = ['other'];

    return this.json<CompanyDescription>('/companies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_id: params.companyId,
        display_name: params.displayName,
        domains,
        template: 'blank',
        supabase_user_ids: params.supabaseUserIds,
      }),
    });
  }

  /** `GET /company?company=` — 404 means the tenant is not registered. */
  async getCompany(companySlug: string): Promise<CompanyDescription | null> {
    const res = await this.fetch(
      `/company?company=${encodeURIComponent(companySlug)}`,
    );
    if (res.status === 404) return null;
    if (!res.ok) throw await this.toError(res, 'GET /company');
    return (await res.json()) as CompanyDescription;
  }

  /**
   * `POST /kpi-plan/sync?company=` — propose a KPI configuration from the CSV.
   *
   * The buffer NestJS already holds is forwarded directly; the file is never
   * re-read from Supabase storage. The upload is *staged* by this call, which
   * leaves the company `awaiting_data` until a plan is confirmed.
   */
  async planKpis(params: {
    companySlug: string;
    filename: string;
    buffer: Buffer;
    noLlm?: boolean;
  }): Promise<PlanSyncResponse> {
    const form = new FormData();
    form.append(
      'file',
      new Blob([new Uint8Array(params.buffer)], { type: 'text/csv' }),
      params.filename,
    );

    const query = new URLSearchParams({ company: params.companySlug });
    if (params.noLlm) query.set('no_llm', 'true');

    return this.json<PlanSyncResponse>(`/kpi-plan/sync?${query.toString()}`, {
      method: 'POST',
      body: form,
    });
  }

  /**
   * `POST /kpi-plan/confirm/sync?company=` — commit the decided plan.
   *
   * Writes the tenant's real configs, accepts the staged data, and warms the
   * pipeline. `/ask` is a 409 until this has succeeded.
   */
  async confirmKpiPlan(params: {
    companySlug: string;
    planId: string;
    decisions: PlanDecision[];
  }): Promise<ConfirmSyncResponse> {
    const query = new URLSearchParams({ company: params.companySlug });

    return this.json<ConfirmSyncResponse>(
      `/kpi-plan/confirm/sync?${query.toString()}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          plan_id: params.planId,
          decisions: params.decisions,
          warm_up: true,
          logs: false,
        }),
      },
    );
  }

  /**
   * `POST /ask?company=` — the real SSE stream, not `/ask/sync`.
   *
   * Frames are `event: <name>\ndata: <json>\n\n`, plus `: keepalive` comments
   * during the long silences between stages. `onFrame` is awaited, so a slow
   * consumer (a Supabase write per stage) backpressures rather than racing.
   */
  async *askStream(params: {
    companySlug: string;
    question: string;
    persona?: string;
    onFrame?: (frame: SseFrame) => Promise<void> | void;
  }): AsyncGenerator<SseFrame> {
    const query = new URLSearchParams({ company: params.companySlug });
    const res = await this.fetch(`/ask?${query.toString()}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({
        question: params.question,
        persona: params.persona ?? 'analyst',
        // The stage log is noise for this consumer; the events carry progress.
        logs: false,
      }),
    });

    if (!res.ok) throw await this.toError(res, 'POST /ask');
    if (!res.body) {
      throw new ServiceUnavailableException(
        'The analysis service returned an empty stream',
      );
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Frames are separated by a blank line. Anything before the last
        // separator is complete; the tail may be a partial frame.
        let separator = buffer.indexOf('\n\n');
        while (separator !== -1) {
          const raw = buffer.slice(0, separator);
          buffer = buffer.slice(separator + 2);
          const frame = parseSseFrame(raw);
          if (frame) yield frame;
          separator = buffer.indexOf('\n\n');
        }
      }

      const tail = parseSseFrame(buffer);
      if (tail) yield tail;
    } finally {
      await reader.cancel().catch(() => undefined);
    }
  }

  private async json<T>(path: string, init: RequestInit): Promise<T> {
    const res = await this.fetch(path, init);
    if (!res.ok) throw await this.toError(res, path);
    return (await res.json()) as T;
  }

  private async fetch(path: string, init?: RequestInit): Promise<Response> {
    try {
      return await fetch(`${this.baseUrl}${path}`, init);
    } catch (err) {
      this.logger.error(`Cannot reach the analysis service at ${this.baseUrl}`, err);
      throw new ServiceUnavailableException(
        'The analysis service is unreachable. Is the KPI engine running?',
      );
    }
  }

  private async toError(res: Response, where: string): Promise<Error> {
    const text = await res.text().catch(() => '');
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (parsed?.detail) {
        detail =
          typeof parsed.detail === 'string'
            ? parsed.detail
            : JSON.stringify(parsed.detail);
      }
    } catch {
      // Not JSON — the raw body is the best detail we have.
    }
    const message = `${where} failed (${res.status}): ${detail || res.statusText}`;
    this.logger.error(message);
    return new PythonApiError(res.status, message);
  }
}

/** Carries the Python tier's status code, so callers can distinguish a 409. */
export class PythonApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'PythonApiError';
  }
}

function parseSseFrame(raw: string): SseFrame | null {
  const trimmed = raw.trim();
  // `: keepalive` comments carry nothing and must not be decoded.
  if (!trimmed || trimmed.startsWith(':')) return null;

  let event = 'message';
  const dataLines: string[] = [];

  for (const line of trimmed.split('\n')) {
    if (line.startsWith(':')) continue;
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trim());
    }
  }

  if (dataLines.length === 0) return null;

  try {
    const data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;
    return { event, data };
  } catch {
    return null;
  }
}
