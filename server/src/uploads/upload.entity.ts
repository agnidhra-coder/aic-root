export type UploadStatus = 'pending' | 'analyzing' | 'ready' | 'failed';
export type AnalysisStage = 'detect' | 'decompose' | 'explain' | 'act';

export interface UploadRecord {
  id: string;
  user_id: string;
  domain: 'retail' | 'supply-chain';
  filename: string;
  storage_path: string;
  size_bytes: number;
  row_count: number | null;
  status: UploadStatus;
  stage: AnalysisStage | null;
  created_at: string;
}
