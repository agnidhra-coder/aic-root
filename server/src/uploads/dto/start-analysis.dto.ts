import { IsOptional, IsString, MaxLength } from 'class-validator';

export class StartAnalysisDto {
  /**
   * Optional. Left blank, the engine is asked the base question alone; typed
   * into, the text is appended to it for a more detailed ask.
   */
  @IsOptional()
  @IsString()
  @MaxLength(2000)
  question?: string;
}
