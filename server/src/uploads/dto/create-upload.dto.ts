import { IsIn } from 'class-validator';

export class CreateUploadDto {
  @IsIn(['retail', 'supply-chain'])
  domain: 'retail' | 'supply-chain';
}
