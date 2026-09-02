import { ArrayNotEmpty, IsArray, IsString } from 'class-validator';

export class ConfirmPlanDto {
  @IsArray()
  @ArrayNotEmpty()
  @IsString({ each: true })
  acceptedKpis: string[];
}
