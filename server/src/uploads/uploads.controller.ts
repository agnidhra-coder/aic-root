import {
  BadRequestException,
  Body,
  Controller,
  ConflictException,
  Get,
  NotFoundException,
  Param,
  Post,
  UploadedFiles,
  UseGuards,
  UseInterceptors,
} from '@nestjs/common';
import { FileFieldsInterceptor } from '@nestjs/platform-express';
import { JwtAuthGuard } from '../auth/guards/jwt-auth.guard';
import { CurrentUser } from '../auth/decorators/current-user.decorator';
import type { PublicUser } from '../users/user.entity';
import { UploadsService } from './uploads.service';
import { AnalysisService } from '../analysis/analysis.service';
import { CreateUploadDto } from './dto/create-upload.dto';
import { ConfirmPlanDto } from './dto/confirm-plan.dto';
import { StartAnalysisDto } from './dto/start-analysis.dto';

const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024; // 20MB
const MAX_CONTEXT_DOC_SIZE_BYTES = 20 * 1024 * 1024; // 20MB
const ALLOWED_CONTEXT_DOC_EXTENSIONS = ['.pdf', '.docx', '.txt'];

@Controller('uploads')
@UseGuards(JwtAuthGuard)
export class UploadsController {
  constructor(
    private readonly uploadsService: UploadsService,
    private readonly analysisService: AnalysisService,
  ) {}

  @Post()
  @UseInterceptors(
    FileFieldsInterceptor([
      { name: 'file', maxCount: 1 },
      { name: 'contextDoc', maxCount: 1 },
    ]),
  )
  async create(
    @UploadedFiles()
    files: {
      file?: Express.Multer.File[];
      contextDoc?: Express.Multer.File[];
    },
    @Body() dto: CreateUploadDto,
    @CurrentUser() user: PublicUser,
  ) {
    const file = files.file?.[0];
    const contextDoc = files.contextDoc?.[0];

    if (!file) {
      throw new BadRequestException('No file was uploaded');
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      throw new BadRequestException('File exceeds the 20MB limit');
    }
    if (!file.originalname.toLowerCase().endsWith('.csv')) {
      throw new BadRequestException('Only CSV files are accepted');
    }

    if (contextDoc) {
      if (contextDoc.size > MAX_CONTEXT_DOC_SIZE_BYTES) {
        throw new BadRequestException(
          'Context document exceeds the 20MB limit',
        );
      }
      const lowerName = contextDoc.originalname.toLowerCase();
      if (!ALLOWED_CONTEXT_DOC_EXTENSIONS.some((ext) => lowerName.endsWith(ext))) {
        throw new BadRequestException(
          'Context document must be a PDF, DOCX, or TXT file',
        );
      }
    }

    // No domain-contract pre-validation any more. The KPI engine derives what
    // this file can compute from its own columns against the catalogue, so a
    // gate on a fixed per-domain column list would reject files it can handle.
    return this.uploadsService.create({
      userId: user.id,
      userName: user.name,
      domain: dto.domain,
      file,
      contextDoc,
    });
  }

  @Get()
  list(@CurrentUser() user: PublicUser) {
    return this.uploadsService.listForUser(user.id);
  }

  @Get(':id')
  async getOne(@Param('id') id: string, @CurrentUser() user: PublicUser) {
    const upload = await this.uploadsService.findByIdForUser(id, user.id);
    if (!upload) {
      throw new NotFoundException('Upload not found');
    }
    return upload;
  }

  /** The proposed KPI plan, for the accept/reject step. */
  @Get(':id/plan')
  async getPlan(@Param('id') id: string, @CurrentUser() user: PublicUser) {
    const upload = await this.uploadsService.findByIdForUser(id, user.id);
    if (!upload) {
      throw new NotFoundException('Upload not found');
    }
    if (!upload.kpi_plan) {
      throw new ConflictException(
        `The KPI plan is not ready yet (status: ${upload.status})`,
      );
    }
    return upload.kpi_plan;
  }

  /** Commit the user's accept/reject decisions and configure their workspace. */
  @Post(':id/plan/confirm')
  confirmPlan(
    @Param('id') id: string,
    @Body() dto: ConfirmPlanDto,
    @CurrentUser() user: PublicUser,
  ) {
    return this.uploadsService.confirmPlan({
      uploadId: id,
      userId: user.id,
      acceptedKpis: dto.acceptedKpis,
    });
  }

  /** Ask the question and open the real analysis stream. */
  @Post(':id/analysis')
  startAnalysis(
    @Param('id') id: string,
    @Body() dto: StartAnalysisDto,
    @CurrentUser() user: PublicUser,
  ) {
    return this.uploadsService.startAnalysis({
      uploadId: id,
      userId: user.id,
      question: dto.question,
    });
  }

  @Get(':id/analysis')
  async getAnalysis(@Param('id') id: string, @CurrentUser() user: PublicUser) {
    const upload = await this.uploadsService.findByIdForUser(id, user.id);
    if (!upload) {
      throw new NotFoundException('Upload not found');
    }
    if (upload.status !== 'ready') {
      throw new ConflictException(
        `Analysis is not ready yet (status: ${upload.status})`,
      );
    }

    return this.analysisService.getResultForUpload(id);
  }
}
