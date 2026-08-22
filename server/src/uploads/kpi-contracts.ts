/**
 * Duplicated from frontend/lib/contracts.ts. Keep in sync by hand until
 * these apps share a workspace package.
 */

export type UploadDomain = 'retail' | 'supply-chain';

export interface KpiContract {
  domain: UploadDomain;
  kpis: string[];
  dimensions: string[];
}

export const kpiContracts: Record<UploadDomain, KpiContract> = {
  retail: {
    domain: 'retail',
    kpis: ['Revenue', 'Conversion Rate', 'Average Order Value', 'Return Rate'],
    dimensions: ['Region', 'Channel', 'Product category', 'Traffic source'],
  },
  'supply-chain': {
    domain: 'supply-chain',
    kpis: [
      'OTIF',
      'Stockout Rate',
      'Supplier Lead-Time Variance',
      'Cost-per-Order',
    ],
    dimensions: ['Region', 'DC', 'Lane', 'Carrier', 'Supplier', 'SKU category'],
  },
};

export function normalizeColumnName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}
