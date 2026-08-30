import { Module } from '@nestjs/common';
import { UploadsController } from './uploads.controller';
import { UploadsService } from './uploads.service';
import { AnalysisModule } from '../analysis/analysis.module';
import { UsersModule } from '../users/users.module';

@Module({
  imports: [AnalysisModule, UsersModule],
  controllers: [UploadsController],
  providers: [UploadsService],
})
export class UploadsModule {}
