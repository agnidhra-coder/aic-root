import {
  BadRequestException,
  Injectable,
  InternalServerErrorException,
} from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { AnalysisService } from '../analysis/analysis.service';
import { UploadRecord } from './upload.entity';

const TABLE = 'uploads';
const BUCKET = 'kpi-uploads';

@Injectable()
export class UploadsService {
  constructor(
    private readonly supabase: SupabaseService,
    private readonly analysisService: AnalysisService,
  ) {}

  async create(params: {
    userId: string;
    domain: 'retail' | 'supply-chain';
    file: Express.Multer.File;
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

    const { data, error } = await this.supabase.client
      .from(TABLE)
      .insert({
        user_id: params.userId,
        domain: params.domain,
        filename: params.file.originalname,
        storage_path: storagePath,
        size_bytes: params.file.size,
      })
      .select('*')
      .single();

    if (error) {
      await this.supabase.client.storage.from(BUCKET).remove([storagePath]);
      throw new InternalServerErrorException('Failed to record upload');
    }

    const upload = data as UploadRecord;

    this.analysisService.runInBackground({
      uploadId: upload.id,
      domain: upload.domain,
      csvContent: params.file.buffer.toString('utf-8'),
    });

    return upload;
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
}
