import {
  BadRequestException,
  Body,
  Controller,
  ConflictException,
  Get,
  NotFoundException,
  Param,
  Post,
  UploadedFile,
  UseGuards,
  UseInterceptors,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { JwtAuthGuard } from '../auth/guards/jwt-auth.guard';
import { CurrentUser } from '../auth/decorators/current-user.decorator';
import type { PublicUser } from '../users/user.entity';
import { UploadsService } from './uploads.service';
import { AnalysisService } from '../analysis/analysis.service';
import { CreateUploadDto } from './dto/create-upload.dto';
import { CsvValidationError, validateCsv } from './csv-validator';

const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024; // 20MB

@Controller('uploads')
@UseGuards(JwtAuthGuard)
export class UploadsController {
  constructor(
    private readonly uploadsService: UploadsService,
    private readonly analysisService: AnalysisService,
  ) {}

  @Post()
  @UseInterceptors(FileInterceptor('file'))
  async create(
    @UploadedFile() file: Express.Multer.File,
    @Body() dto: CreateUploadDto,
    @CurrentUser() user: PublicUser,
  ) {
    if (!file) {
      throw new BadRequestException('No file was uploaded');
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      throw new BadRequestException('File exceeds the 20MB limit');
    }
    if (!file.originalname.toLowerCase().endsWith('.csv')) {
      throw new BadRequestException('Only CSV files are accepted');
    }

    try {
      validateCsv(file.buffer.toString('utf-8'), dto.domain);
    } catch (err) {
      if (err instanceof CsvValidationError) {
        throw new BadRequestException(err.message);
      }
      throw err;
    }

    return this.uploadsService.create({
      userId: user.id,
      domain: dto.domain,
      file,
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
