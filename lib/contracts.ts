import type { Domain } from "./types";

export interface KpiContract {
  domain: Domain;
  kpis: string[];
  dimensions: string[];
  drivers: string[];
  structured: string[];
  unstructured: string[];
  exogenous: string[];
  personas: string[];
}

export const contracts: Record<Domain, KpiContract> = {
  retail: {
    domain: "retail",
    kpis: ["Revenue", "Conversion Rate", "Average Order Value", "Return Rate"],
    dimensions: ["Region", "Channel", "Product category", "Traffic source"],
    drivers: ["Pricing / promo changes", "Checkout / site performance", "Sizing / SKU quality", "Assortment shifts"],
    structured: ["Web analytics (sessions, funnel)", "POS", "Marketing-spend ledger"],
    unstructured: ["Customer support tickets", "Product reviews"],
    exogenous: ["Weather", "Local events / festivals", "Competitor pricing feed"],
    personas: ["Store / Regional Ops Manager", "VP Merchandising"],
  },
  "supply-chain": {
    domain: "supply-chain",
    kpis: ["OTIF", "Stockout Rate", "Supplier Lead-Time Variance", "Cost-per-Order"],
    dimensions: ["Region", "DC", "Lane", "Carrier", "Supplier", "SKU category"],
    drivers: ["Carrier / depot incidents", "Supplier lead-time shifts", "Road / weather disruption", "Load consolidation"],
    structured: ["ERP (POs, cost, lead time)", "WMS (inventory, inbound/outbound)", "TMS / carrier feed"],
    unstructured: ["Carrier / DC incident tickets", "Supplier notices"],
    exogenous: ["Weather", "Festival / road-closure calendar"],
    personas: ["DC Ops Manager", "VP Supply Chain"],
  },
};
