import type { KpiPlan } from '../python/python-api.types';

/**
 * The upload's lifecycle.
 *
 * `pending` -> `planning` -> `awaiting_plan` -> (user confirms) -> `confirming`
 * -> `awaiting_question` -> (user submits) -> `analyzing` -> `ready` | `failed`.
 *
 * The three new states in the middle are the onboarding handshake: the Python
 * tier proposes which KPIs this file can actually support, and the user decides.
 */
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

export interface UploadRecord {
  id: string;
  user_id: string;
  /**
   * Filing/display metadata only. Drives the dashboard's tab filter and nothing
   * else: it is never sent to the Python tier and never selects a template.
   */
  domain: 'retail' | 'supply-chain';
  filename: string;
  storage_path: string;
  size_bytes: number;
  context_filename: string | null;
  context_storage_path: string | null;
  row_count: number | null;
  status: UploadStatus;
  stage: AnalysisStage | null;
  /** The Python draft plan's id, needed to confirm it. */
  plan_id: string | null;
  /** The full `KpiPlan` the Python tier proposed, for the selection UI. */
  kpi_plan: KpiPlan | null;
  /** What went wrong, when `status` is `failed`. */
  error_message: string | null;
  created_at: string;
}
