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

/**
 * `KpiPlan` as Python sends it, plus two diagnostics NestJS attaches before
 * storing it. Neither comes from the plan itself: `staged.warnings` is about
 * the CSV as uploaded (before profiling even starts), and
 * `bindings.unmatched_columns` is what the model could not place anywhere --
 * both real onboarding-stream events with no other home in `KpiPlan`, and
 * both worth a user seeing before they confirm.
 */
export interface StoredKpiPlan extends KpiPlan {
  stagingWarnings?: string[];
  unmatchedColumns?: string[];
}

export interface UploadRecord {
  id: string;
  user_id: string;
  /**
   * Drives the dashboard's tab filter, and selects which Python company (one
   * per (user, domain)) and starting template this upload's KPI plan runs
   * against — see `UsersService.companySlugFor`. The confirmed contract still
   * ends up shaped by the file's real columns regardless of the template.
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
  kpi_plan: StoredKpiPlan | null;
  /** What went wrong, when `status` is `failed`. */
  error_message: string | null;
  created_at: string;
}
