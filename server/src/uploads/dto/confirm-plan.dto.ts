import { ArrayNotEmpty, IsArray, IsString } from 'class-validator';

export class ConfirmPlanDto {
  /**
   * The KPI names the user accepted. Everything else the plan proposed is
   * rejected explicitly — an omission would otherwise keep its proposal.
   */
  @IsArray()
  @ArrayNotEmpty()
  @IsString({ each: true })
  acceptedKpis: string[];
}
