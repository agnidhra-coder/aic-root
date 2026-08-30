import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { AnalysisRecord, AnalysisResult } from './analysis.entity';
import { AnalysisStage, UploadStatus } from '../uploads/upload.entity';
import { PythonApiService } from '../python/python-api.service';
import { AskNarrative } from '../python/python-api.types';
import {
  absorbFrame,
  attachActions,
  attachNarratives,
  buildAnalysisResult,
  emptyAccumulator,
  KpiUnit,
} from './evidence-mapper';

const TABLE = 'analyses';
const UPLOADS_TABLE = 'uploads';

/**
 * Python's `/ask` events, mapped onto the four stages the frontend already
 * polls. The stepper is a coarse view of a much longer chain; what matters is
 * that it only ever moves forward.
 */
const STAGE_FOR_EVENT: Record<string, AnalysisStage> = {
  started: 'detect',
  ingest: 'detect',
  plan: 'detect',
  engine: 'decompose',
  evidence: 'explain',
  narrative: 'act',
  verification: 'act',
  report: 'act',
  no_findings: 'act',
};

const STAGE_ORDER: AnalysisStage[] = ['detect', 'decompose', 'explain', 'act'];

/** The base question. A blank prompt sends exactly this and nothing else. */
export const BASE_QUESTION =
  'Analyze the KPIs I selected and tell me what needs attention.';

export function buildQuestion(userText?: string | null): string {
  const extra = (userText ?? '').trim();
  if (!extra) return BASE_QUESTION;
  return `${BASE_QUESTION} ${extra}`;
}

@Injectable()
export class AnalysisService {
  private readonly logger = new Logger(AnalysisService.name);

  constructor(
    private readonly supabase: SupabaseService,
    private readonly python: PythonApiService,
  ) {}

  runInBackground(params: {
    uploadId: string;
    companySlug: string;
    question: string;
    columns: string[];
    fallbackRowCount: number;
    kpiUnits?: Map<string, KpiUnit>;
  }): void {
    void this.run(params).catch(async (err: unknown) => {
      this.logger.error(`Analysis failed for upload ${params.uploadId}`, err);
      await this.setStatus(
        params.uploadId,
        'failed',
        null,
        err instanceof Error ? err.message : 'The analysis run failed.',
      );
    });
  }

  /**
   * Drive one real `/ask` run, forwarding stage progress into `uploads.stage`
   * as the events land, and store the mapped `AnalysisResult` on completion.
   */
  private async run(params: {
    uploadId: string;
    companySlug: string;
    question: string;
    columns: string[];
    fallbackRowCount: number;
    kpiUnits?: Map<string, KpiUnit>;
  }): Promise<void> {
    await this.setStatus(params.uploadId, 'analyzing', 'detect');

    const acc = emptyAccumulator();
    let currentStage: AnalysisStage = 'detect';

    for await (const frame of this.python.askStream({
      companySlug: params.companySlug,
      question: params.question,
    })) {
      absorbFrame(acc, frame.event, frame.data);

      const next = STAGE_FOR_EVENT[frame.event];
      // Only ever forward: the repair loop revisits `narrate` and `verify`, and
      // a stepper that walked backwards would read as the run restarting.
      if (
        next &&
        STAGE_ORDER.indexOf(next) > STAGE_ORDER.indexOf(currentStage)
      ) {
        currentStage = next;
        await this.setStage(params.uploadId, next);
      }
    }

    if (acc.error) {
      throw new Error(`${acc.error.type}: ${acc.error.message}`);
    }

    // `clarify` is terminal and computes nothing. Storing it as a `ready`
    // analysis with zero cases would read as "we looked and found nothing",
    // which is a different — and wrong — answer.
    if (acc.clarification) {
      throw new Error(
        acc.clarification.message ??
          'The engine needed more detail to answer that question.',
      );
    }

    const result = buildAnalysisResult({
      uploadId: params.uploadId,
      question: params.question,
      columns: params.columns,
      fallbackRowCount: params.fallbackRowCount,
      acc,
      kpiUnits: params.kpiUnits,
    });

    const narrative: AskNarrative | null | undefined = acc.narrative?.narrative;

    // A `KpiCase.id` is its event's id; each event names its own fact ids
    // directly, which is a far more reliable way to tell which facts (and so
    // which actions and narrative claims) are "this case's" than a fact's
    // `lineage` -- movement facts carry no `event_id` in theirs at all, only
    // a contribution fact's does, so routing by lineage alone silently
    // misses anything that also cites a movement fact.
    const factIdsByEvent = new Map(
      (acc.evidence?.events ?? []).map((e) => [
        e.event_id,
        new Set(e.fact_ids),
      ]),
    );
    attachActions(result, narrative, factIdsByEvent);
    attachNarratives(result, narrative, factIdsByEvent);

    // `upsert` rather than `insert`: `analyses.upload_id` is unique, and a user
    // may legitimately re-run a question against the same upload.
    const { error } = await this.supabase.client
      .from(TABLE)
      .upsert(
        { upload_id: params.uploadId, result },
        { onConflict: 'upload_id' },
      );

    if (error) throw new Error(error.message);

    await this.supabase.client
      .from(UPLOADS_TABLE)
      .update({
        status: 'ready',
        stage: null,
        row_count: result.rowCount,
        error_message: null,
      })
      .eq('id', params.uploadId);
  }

  private async setStatus(
    uploadId: string,
    status: UploadStatus,
    stage: AnalysisStage | null,
    errorMessage?: string,
  ): Promise<void> {
    await this.supabase.client
      .from(UPLOADS_TABLE)
      .update({
        status,
        stage,
        ...(errorMessage === undefined ? {} : { error_message: errorMessage }),
      })
      .eq('id', uploadId);
  }

  private async setStage(
    uploadId: string,
    stage: AnalysisStage,
  ): Promise<void> {
    await this.supabase.client
      .from(UPLOADS_TABLE)
      .update({ stage })
      .eq('id', uploadId);
  }

  async getResultForUpload(uploadId: string): Promise<AnalysisResult> {
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .select('*')
      .eq('upload_id', uploadId)
      .maybeSingle();

    if (error || !data) {
      throw new NotFoundException('Analysis not found for this upload');
    }

    return (data as AnalysisRecord).result;
  }
}
