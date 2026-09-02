import { IsOptional, IsString, MaxLength } from 'class-validator';

export class StartAnalysisDto {
  @IsOptional()
  @IsString()
  @MaxLength(2000)
  question?: string;
}
