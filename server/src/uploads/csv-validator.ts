import Papa from 'papaparse';
import {
  kpiContracts,
  normalizeColumnName,
  UploadDomain,
} from './kpi-contracts';

export class CsvValidationError extends Error {}

export interface ValidatedCsv {
  headers: string[];
  rowCount: number;
  kpiColumns: string[];
}

const NUMERIC_RE = /^-?\d+(\.\d+)?$/;

export function validateCsv(
  csvContent: string,
  domain: UploadDomain,
): ValidatedCsv {
  const parsed = Papa.parse<Record<string, string>>(csvContent, {
    header: true,
    skipEmptyLines: true,
    dynamicTyping: false,
  });

  if (parsed.errors.length > 0) {
    const first = parsed.errors[0];
    throw new CsvValidationError(
      `Could not parse CSV (row ${first.row ?? '?'}): ${first.message}`,
    );
  }

  const headers = parsed.meta.fields ?? [];
  if (headers.length === 0) {
    throw new CsvValidationError('The file has no header row.');
  }

  const rows = parsed.data;
  if (rows.length === 0) {
    throw new CsvValidationError('The file has a header row but no data rows.');
  }

  const contract = kpiContracts[domain];
  const contractKpiNames = new Set(contract.kpis.map(normalizeColumnName));
  const contractDimensionNames = new Set(
    contract.dimensions.map(normalizeColumnName),
  );

  const kpiColumns = headers.filter((h) =>
    contractKpiNames.has(normalizeColumnName(h)),
  );
  const dimensionColumns = headers.filter((h) =>
    contractDimensionNames.has(normalizeColumnName(h)),
  );

  if (kpiColumns.length === 0 && dimensionColumns.length === 0) {
    throw new CsvValidationError(
      [
        `None of this file's columns match a known ${contract.domain} field.`,
        `Expected KPIs: ${contract.kpis.join(', ')}`,
        `Expected dimensions: ${contract.dimensions.join(', ')}`,
      ].join('\n'),
    );
  }

  for (const column of kpiColumns) {
    rows.forEach((row, index) => {
      const value = row[column]?.trim();
      if (value === undefined || value === '') {
        throw new CsvValidationError(
          `Column "${column}" is missing a value on row ${index + 2}.`,
        );
      }
      if (!NUMERIC_RE.test(value)) {
        throw new CsvValidationError(
          `Column "${column}" should be numeric, but row ${index + 2} has "${value}".`,
        );
      }
    });
  }

  return { headers, rowCount: rows.length, kpiColumns };
}
