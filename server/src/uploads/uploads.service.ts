import {
  BadRequestException,
  ConflictException,
  Injectable,
  InternalServerErrorException,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { AnalysisService, buildQuestion } from '../analysis/analysis.service';
import { unitsFromPlan } from '../analysis/evidence-mapper';
import { PythonApiService } from '../python/python-api.service';
import { UsersService } from '../users/users.service';
import { KpiPlan, PlanDecision } from '../python/python-api.types';
import { UploadRecord, UploadStatus } from './upload.entity';

const TABLE = 'uploads';
const BUCKET = 'kpi-uploads';

/**
 * The uploaded CSV, kept in memory between the plan and the confirm step.
 *
 * Python stages the file itself at `/kpi-plan`, so the confirm call does not
 * need the bytes again — but the header and row count are wanted for the
 * eventual `AnalysisResult`, and re-downloading from Supabase storage to get
 * them would be a round trip for two numbers.
 */
interface CsvSummary {
  columns: string[];
  rowCount: number;
}

@Injectable()
export class UploadsService {
  private readonly logger = new Logger(UploadsService.name);

  constructor(
    private readonly supabase: SupabaseService,
    private readonly analysisService: AnalysisService,
    private readonly python: PythonApiService,
    private readonly users: UsersService,
  ) {}

  async create(params: {
    userId: string;
    userName: string;
    domain: 'retail' | 'supply-chain';
    file: Express.Multer.File;
    contextDoc?: Express.Multer.File;
  }): Promise<UploadRecord> {
    const storagePath = `${params.userId}/${Date.now()}-${params.file.originalname}`;

    const { error: uploadError } = await this.supabase.client.storage
      .from(BUCKET)
      .upload(storagePath, params.file.buffer, {
        contentType: params.file.mimetype || 'text/csv',
        upsert: false,
      });

    if (uploadError) {
      throw new BadRequestException(
        `Failed to store file: ${uploadError.message}`,
      );
    }

    let contextStoragePath: string | null = null;
    if (params.contextDoc) {
      contextStoragePath = `${params.userId}/${Date.now()}-context-${params.contextDoc.originalname}`;
      const { error: contextUploadError } = await this.supabase.client.storage
        .from(BUCKET)
        .upload(contextStoragePath, params.contextDoc.buffer, {
          contentType: params.contextDoc.mimetype || 'application/octet-stream',
          upsert: false,
        });

      if (contextUploadError) {
        await this.supabase.client.storage.from(BUCKET).remove([storagePath]);
        throw new BadRequestException(
          `Failed to store context document: ${contextUploadError.message}`,
        );
      }
    }

    const summary = summariseCsv(params.file.buffer);

    const { data, error } = await this.supabase.client
      .from(TABLE)
      .insert({
        user_id: params.userId,
        // Filing metadata only — never sent to Python, never a template selector.
        domain: params.domain,
        filename: params.file.originalname,
        storage_path: storagePath,
        size_bytes: params.file.size,
        context_filename: params.contextDoc?.originalname ?? null,
        context_storage_path: contextStoragePath,
        row_count: summary.rowCount,
        status: 'planning' satisfies UploadStatus,
      })
      .select('*')
      .single();

    if (error) {
      const pathsToRemove = [
        storagePath,
        ...(contextStoragePath ? [contextStoragePath] : []),
      ];
      await this.supabase.client.storage.from(BUCKET).remove(pathsToRemove);
      throw new InternalServerErrorException('Failed to record upload');
    }

    const upload = data as UploadRecord;

    // Proposing the plan is minutes of profiling on a wide file, so it runs in
    // the background exactly like the analysis does; the frontend polls status.
    this.planInBackground({
      uploadId: upload.id,
      userId: params.userId,
      userName: params.userName,
      filename: params.file.originalname,
      buffer: params.file.buffer,
    });

    return upload;
  }

  private planInBackground(params: {
    uploadId: string;
    userId: string;
    userName: string;
    filename: string;
    buffer: Buffer;
  }): void {
    void this.planKpis(params).catch(async (err: unknown) => {
      this.logger.error(
        `KPI planning failed for upload ${params.uploadId}`,
        err,
      );
      await this.markFailed(
        params.uploadId,
        err instanceof Error ? err.message : 'KPI planning failed.',
      );
    });
  }

  /**
   * Ensure the tenant exists, then propose which KPIs this file can support.
   *
   * The file is *staged* by this call, not accepted: the company stays
   * `awaiting_data` and `/ask` keeps refusing it until the plan is confirmed.
   */
  private async planKpis(params: {
    uploadId: string;
    userId: string;
    userName: string;
    filename: string;
    buffer: Buffer;
  }): Promise<void> {
    const companySlug = await this.users.ensureCompanySlug({
      id: params.userId,
      name: params.userName,
    });

    const response = await this.python.planKpis({
      companySlug,
      filename: params.filename,
      buffer: params.buffer,
    });

    if (response.error) {
      throw new Error(`${response.error.type}: ${response.error.message}`);
    }

    const plan = response.plan?.plan;
    if (!plan) {
      throw new Error(
        'The analysis service proposed no KPI plan for this file.',
      );
    }

    const { error } = await this.supabase.client
      .from(TABLE)
      .update({
        status: 'awaiting_plan' satisfies UploadStatus,
        plan_id: plan.plan_id,
        kpi_plan: plan,
        error_message: null,
      })
      .eq('id', params.uploadId);

    if (error) throw new Error(error.message);
  }

  /**
   * Step 4: commit the user's accept/reject decisions.
   *
   * A KPI not named in `decisions` keeps its proposal, so both verdicts are sent
   * explicitly — an omitted rejection would silently be accepted.
   */
  async confirmPlan(params: {
    uploadId: string;
    userId: string;
    acceptedKpis: string[];
  }): Promise<UploadRecord> {
    const upload = await this.requireUpload(params.uploadId, params.userId);

    if (upload.status !== 'awaiting_plan') {
      throw new ConflictException(
        `This upload is not awaiting a KPI plan (status: ${upload.status})`,
      );
    }
    const plan = upload.kpi_plan;
    if (!plan || !upload.plan_id) {
      throw new ConflictException('No KPI plan is on record for this upload');
    }

    const companySlug = await this.companySlugFor(params.userId);
    const decisions = buildDecisions(plan, params.acceptedKpis);

    if (decisions.every((d) => d.verdict === 'reject')) {
      throw new BadRequestException(
        'Select at least one KPI — a workspace with no KPIs answers every question with silence.',
      );
    }

    await this.setStatus(params.uploadId, 'confirming');

    try {
      const response = await this.python.confirmKpiPlan({
        companySlug,
        planId: upload.plan_id,
        decisions,
      });
      if (response.error) {
        throw new Error(`${response.error.type}: ${response.error.message}`);
      }
    } catch (err) {
      await this.markFailed(
        params.uploadId,
        err instanceof Error ? err.message : 'Failed to confirm the KPI plan.',
      );
      throw err;
    }

    await this.setStatus(params.uploadId, 'awaiting_question');
    return this.requireUpload(params.uploadId, params.userId);
  }

  /**
   * Step 6/7: build the final question and open the real `/ask` stream.
   *
   * The plan must be confirmed first — `/ask` is a 409 against a company whose
   * data has not been accepted — which is what the status gate below enforces.
   */
  async startAnalysis(params: {
    uploadId: string;
    userId: string;
    question?: string | null;
  }): Promise<UploadRecord> {
    const upload = await this.requireUpload(params.uploadId, params.userId);

    if (upload.status !== 'awaiting_question') {
      throw new ConflictException(
        `This upload is not ready for a question (status: ${upload.status})`,
      );
    }

    const companySlug = await this.companySlugFor(params.userId);

    this.analysisService.runInBackground({
      uploadId: upload.id,
      companySlug,
      question: buildQuestion(params.question),
      columns: upload.kpi_plan?.header ?? [],
      fallbackRowCount: upload.row_count ?? 0,
      kpiUnits: unitsFromPlan(upload.kpi_plan),
    });

    await this.setStatus(params.uploadId, 'analyzing');
    return this.requireUpload(params.uploadId, params.userId);
  }

  async findByIdForUser(
    uploadId: string,
    userId: string,
  ): Promise<UploadRecord | null> {
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .select('*')
      .eq('id', uploadId)
      .eq('user_id', userId)
      .maybeSingle();

    if (error) {
      throw new InternalServerErrorException('Failed to look up upload');
    }

    return data as UploadRecord | null;
  }

  async listForUser(userId: string): Promise<UploadRecord[]> {
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .select('*')
      .eq('user_id', userId)
      .order('created_at', { ascending: false });

    if (error) {
      throw new InternalServerErrorException('Failed to list uploads');
    }

    return data as UploadRecord[];
  }

  private async requireUpload(
    uploadId: string,
    userId: string,
  ): Promise<UploadRecord> {
    const upload = await this.findByIdForUser(uploadId, userId);
    if (!upload) throw new NotFoundException('Upload not found');
    return upload;
  }

  private async companySlugFor(userId: string): Promise<string> {
    const user = await this.users.findById(userId);
    if (!user?.company_slug) {
      throw new ConflictException(
        'No analysis workspace exists for this account yet',
      );
    }
    return user.company_slug;
  }

  private async setStatus(
    uploadId: string,
    status: UploadStatus,
  ): Promise<void> {
    await this.supabase.client
      .from(TABLE)
      .update({ status, error_message: null })
      .eq('id', uploadId);
  }

  private async markFailed(uploadId: string, message: string): Promise<void> {
    await this.supabase.client
      .from(TABLE)
      .update({ status: 'failed', stage: null, error_message: message })
      .eq('id', uploadId);
  }
}

/**
 * Every proposed KPI gets an explicit verdict.
 *
 * `PlanDecision` defaults to `accept` and a KPI left out of the list keeps its
 * proposal, so naming only the accepted ones would quietly accept the rest.
 */
function buildDecisions(plan: KpiPlan, accepted: string[]): PlanDecision[] {
  const acceptedSet = new Set(accepted);
  return plan.proposed.map((proposal) => ({
    name: proposal.name,
    verdict: acceptedSet.has(proposal.name) ? 'accept' : 'reject',
  }));
}

/** Header and row count, straight off the buffer NestJS already holds. */
function summariseCsv(buffer: Buffer): CsvSummary {
  const text = buffer.toString('utf-8');
  const lines = text.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length === 0) return { columns: [], rowCount: 0 };
  return {
    columns: lines[0].split(',').map((h) => h.trim().replace(/^"|"$/g, '')),
    rowCount: Math.max(0, lines.length - 1),
  };
}
