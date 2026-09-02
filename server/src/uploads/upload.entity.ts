import type { KpiPlan } from '../python/python-api.types';

export type UploadStatus =
  | 'pending'
  | 'planning'
  | 'awaiting_plan'
  | 'confirming'
  | 'awaiting_question'
  | 'analyzing'
  | 'ready'
  | 'failed';

export type AnalysisStage = 'detect' | 'decompose' | 'explain' | 'act';

export interface StoredKpiPlan extends KpiPlan {
  stagingWarnings?: string[];
  unmatchedColumns?: string[];
}

export interface UploadRecord {
  id: string;
  user_id: string;
  domain: 'retail' | 'supply-chain';
  filename: string;
  storage_path: string;
  size_bytes: number;
  context_filename: string | null;
  context_storage_path: string | null;
  row_count: number | null;
  status: UploadStatus;
  stage: AnalysisStage | null;
  plan_id: string | null;
  kpi_plan: StoredKpiPlan | null;
  error_message: string | null;
  created_at: string;
}
