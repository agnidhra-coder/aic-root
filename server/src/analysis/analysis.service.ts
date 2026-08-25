import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { AnalysisRecord, AnalysisResult } from './analysis.entity';
import { runSimulatedAnalysis } from './simulated-analysis';
import { AnalysisStage } from '../uploads/upload.entity';

const TABLE = 'analyses';
const UPLOADS_TABLE = 'uploads';
const STAGE_DELAY_MS = 1250;
const STAGES: AnalysisStage[] = ['detect', 'decompose', 'explain', 'act'];

@Injectable()
export class AnalysisService {
  private readonly logger = new Logger(AnalysisService.name);

  constructor(private readonly supabase: SupabaseService) {}

  runInBackground(params: {
    uploadId: string;
    domain: string;
    csvContent: string;
  }): void {
    void this.run(params).catch((err: unknown) => {
      this.logger.error(`Analysis failed for upload ${params.uploadId}`, err);
    });
  }

  private async run(params: {
    uploadId: string;
    domain: string;
    csvContent: string;
  }): Promise<void> {
    await this.setStatus(params.uploadId, 'analyzing', STAGES[0]);

    try {
      for (const stage of STAGES) {
        await this.setStage(params.uploadId, stage);
        await sleep(STAGE_DELAY_MS);
      }

      const result = runSimulatedAnalysis(params);

      const { error } = await this.supabase.client
        .from(TABLE)
        .insert({ upload_id: params.uploadId, result });

      if (error) throw new Error(error.message);

      await this.supabase.client
        .from(UPLOADS_TABLE)
        .update({ status: 'ready', stage: null, row_count: result.rowCount })
        .eq('id', params.uploadId);
    } catch (err) {
      await this.setStatus(params.uploadId, 'failed', null);
      throw err;
    }
  }

  private async setStatus(
    uploadId: string,
    status: string,
    stage: AnalysisStage | null,
  ): Promise<void> {
    await this.supabase.client
      .from(UPLOADS_TABLE)
      .update({ status, stage })
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
