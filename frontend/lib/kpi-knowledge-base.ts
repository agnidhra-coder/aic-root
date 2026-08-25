export type PrimaryMetric =
  | 'Revenue'
  | 'Traffic'
  | 'Profit'
  | 'Costs'
  | 'Shrinkage'
  | 'Retention'
  | 'Churn'
  | 'Margin';

export interface KpiKnowledge {
  category: string;
  kpiName: string;
  formula: string;
  whatItJudges: string;
  keyDrivers: string;
  primaryMetric: PrimaryMetric;
  impactRatio: string;
}

export const kpiKnowledgeBase: KpiKnowledge[] = [
  {
    category: 'Sales & Revenue',
    kpiName: 'Sales per Square Foot',
    formula: 'Total Net Sales / Total Square Footage',
    whatItJudges: 'Physical space revenue efficiency.',
    keyDrivers:
      'Store layout, product placement, passing traffic, inventory selection.',
    primaryMetric: 'Revenue',
    impactRatio:
      '+10% increase yields +10% total facility revenue, assuming footprint remains static (1:1 Ratio, High Confidence).',
  },
  {
    category: 'Sales & Revenue',
    kpiName: 'Average Transaction Value (ATV)',
    formula: 'Total Revenue / Total Number of Transactions',
    whatItJudges: 'Average spend per purchase event.',
    keyDrivers: 'Cross-selling, upselling, promotional bundling.',
    primaryMetric: 'Revenue',
    impactRatio:
      '1:1 linear correlation; a +5% ATV increase yields a +5% Revenue increase if traffic and conversion remain constant (1:1 Ratio, High Confidence).',
  },
  {
    category: 'Sales & Revenue',
    kpiName: 'Units Per Transaction (UPT)',
    formula: 'Total Units Sold / Total Number of Transactions',
    whatItJudges: 'Average items bought per visit.',
    keyDrivers: 'Product affinity (complementary items), POS impulse buys.',
    primaryMetric: 'Revenue',
    impactRatio:
      'Positive but non-linear due to volume discounts; a +10% UPT increase typically yields an ~8% ATV/Revenue increase (0.8:1 Ratio, Medium Confidence).',
  },
  {
    category: 'Sales & Revenue',
    kpiName: 'Conversion Rate',
    formula: '(Number of Sales / Total Visitors) * 100',
    whatItJudges: 'Percentage of browsers who buy.',
    keyDrivers: 'Window displays, UX, staff helpfulness, product availability.',
    primaryMetric: 'Revenue',
    impactRatio:
      '1:1 linear multiplier; a +1% absolute increase in conversion directly scales total transactions and subsequent revenue (1:1 Ratio, High Confidence).',
  },
  {
    category: 'Sales & Revenue',
    kpiName: 'Foot & Digital Traffic',
    formula: 'Total Store Entrances OR Website Sessions',
    whatItJudges: 'Total sales opportunities / brand awareness.',
    keyDrivers: 'Location, ad campaigns, SEO, local events, weather.',
    primaryMetric: 'Traffic',
    impactRatio:
      '1:1 correlation to volume; however, only yields proportional revenue if the conversion rate holds steady as traffic scales (1:1 Ratio, Medium-High Confidence).',
  },
  {
    category: 'Inventory & Supply',
    kpiName: 'GMROI',
    formula: 'Total Gross Profit / Average Inventory Cost',
    whatItJudges: 'Profit per dollar invested in stock.',
    keyDrivers: 'Vendor pricing, markdown strategies, product demand.',
    primaryMetric: 'Profit',
    impactRatio:
      'Indicates capital efficiency; a +10% GMROI increase means inventory is generating 10% more gross profit per dollar tied up (1:1.1 Ratio, Medium Confidence).',
  },
  {
    category: 'Inventory & Supply',
    kpiName: 'Inventory Turnover',
    formula: 'COGS / Average Inventory Value',
    whatItJudges: 'Speed of stock replacement.',
    keyDrivers: 'Demand forecasting, seasonal trends, supply chain delays.',
    primaryMetric: 'Costs',
    impactRatio:
      'Inverse relationship; a +10% higher turnover rate proportionally decreases warehousing and markdown risk costs (-1:1 Ratio, Medium Confidence).',
  },
  {
    category: 'Inventory & Supply',
    kpiName: 'Sell-Through Rate',
    formula: '(Units Sold / Beginning Inventory) * 100',
    whatItJudges: 'Specific product/category performance.',
    keyDrivers: 'Trend forecasting, promotional pricing strategies.',
    primaryMetric: 'Revenue',
    impactRatio:
      'Proportional to realized seasonal revenue; lower sell-through directly forces future markdowns, eroding margin (1:1 Ratio, High Confidence).',
  },
  {
    category: 'Inventory & Supply',
    kpiName: 'Shrinkage Rate',
    formula: '((Recorded Value - Actual Value) / Retail Sales) * 100',
    whatItJudges: 'Loss from theft, fraud, or errors.',
    keyDrivers: 'Store security, warehouse handling, employee training.',
    primaryMetric: 'Shrinkage',
    impactRatio:
      '1:1 inverse impact on bottom line; a +1% increase in shrinkage destroys exactly 1% of total gross profit margin (1:-1 Ratio, High Confidence).',
  },
  {
    category: 'Customer & Marketing',
    kpiName: 'Customer Retention Rate',
    formula: '((End Customers - New Customers) / Start Customers) * 100',
    whatItJudges: 'Brand loyalty and return rate.',
    keyDrivers: 'Customer service, loyalty programs, post-purchase engagement.',
    primaryMetric: 'Retention',
    impactRatio:
      'Compounding long-term effect; a +5% retention increase can yield a +25% to +95% increase in total customer lifetime profitability (1:5+ Ratio, High Confidence).',
  },
  {
    category: 'Customer & Marketing',
    kpiName: 'Churn Rate',
    formula: '(Customers Lost / Start Customers) * 100',
    whatItJudges: 'Customer attrition.',
    keyDrivers: 'Aggressive competitor promotions, quality drops.',
    primaryMetric: 'Churn',
    impactRatio:
      'Exact mathematical inverse to retention; a +10% churn increase immediately shrinks the active recurring revenue base by 10% (1:-1 Ratio, High Confidence).',
  },
  {
    category: 'Customer & Marketing',
    kpiName: 'Customer Acquisition Cost (CAC)',
    formula: 'Total Sales & Marketing Expenses / New Customers',
    whatItJudges: 'Financial cost to acquire a buyer.',
    keyDrivers: 'Ad bidding costs, campaign efficiency, organic reach.',
    primaryMetric: 'Costs',
    impactRatio:
      '1:1 direct driver of marketing expenses; if CAC increases by $10 per user, net profit per user decreases by exactly $10 (1:-1 Ratio, High Confidence).',
  },
  {
    category: 'Customer & Marketing',
    kpiName: 'Customer Lifetime Value (CLV)',
    formula: 'Avg Purchase Value × Avg Purchase Frequency × Avg Lifespan',
    whatItJudges: 'Total projected revenue per customer.',
    keyDrivers: 'Retention rates, upselling, brand advocacy.',
    primaryMetric: 'Revenue',
    impactRatio:
      'Predictive ratio; CLV must ideally remain 3x higher than CAC to maintain sustainable long-term revenue growth (3:1 Target Ratio, Medium Confidence).',
  },
  {
    category: 'Customer & Marketing',
    kpiName: 'Return on Ad Spend (ROAS)',
    formula: 'Revenue from Ads / Cost of Ads',
    whatItJudges: 'Direct revenue per advertising dollar.',
    keyDrivers: 'Ad targeting accuracy, conversion friction.',
    primaryMetric: 'Margin',
    impactRatio:
      'Non-linear margin impact; a drop in ROAS from 4.0 to 2.0 effectively doubles the acquisition cost burden on gross margins (Exponential Ratio, Medium Confidence).',
  },
  {
    category: 'Financial Health',
    kpiName: 'Net Profit Margin',
    formula: '((Total Revenue - Total Expenses) / Total Revenue) * 100',
    whatItJudges: 'Bottom-line profitability.',
    keyDrivers: 'Supply chain efficiency, overhead control, pricing power.',
    primaryMetric: 'Profit',
    impactRatio:
      'Direct mathematical absolute; a +2% increase in the margin directly yields $0.02 more bottom-line profit for every dollar earned (1:1 Ratio, High/Absolute Confidence).',
  },
  {
    category: 'Financial Health',
    kpiName: 'Gross Profit Margin',
    formula: '(Total Gross Profit / Total Revenue) * 100',
    whatItJudges: 'Profitability before operating expenses.',
    keyDrivers: 'Vendor pricing, raw material costs, discount strategies.',
    primaryMetric: 'Margin',
    impactRatio:
      'Direct mathematical absolute; defines the maximum ceiling for net profit before overhead is calculated (1:1 Ratio, High/Absolute Confidence).',
  },
  {
    category: 'Financial Health',
    kpiName: 'Quick Ratio (Acid Test)',
    formula:
      '(Cash + Marketable Securities + Accounts Receivable) / Current Liabilities',
    whatItJudges: 'Immediate debt payoff ability (excluding inventory).',
    keyDrivers: 'Cash flow management, receivable delays, short-term debt.',
    primaryMetric: 'Costs',
    impactRatio:
      'Solvency risk indicator; a drop below 1.0 means the business cannot immediately cover short-term costs without liquidating inventory (Threshold Ratio, High Confidence).',
  },
  {
    category: 'Financial Health',
    kpiName: 'Current Ratio',
    formula: 'Current Assets / Current Liabilities',
    whatItJudges: 'Broader short-term liquidity (including inventory).',
    keyDrivers: 'Inventory buildup (cash trapped in unsold stock).',
    primaryMetric: 'Costs',
    impactRatio:
      'Operational risk indicator; highly affected by unsold inventory sitting on the balance sheet rather than converting to cash (Threshold Ratio, High Confidence).',
  },
];

export function kpisForPrimaryMetric(metric: PrimaryMetric): KpiKnowledge[] {
  return kpiKnowledgeBase.filter((k) => k.primaryMetric === metric);
}

export const primaryMetrics: PrimaryMetric[] = Array.from(
  new Set(kpiKnowledgeBase.map((k) => k.primaryMetric)),
);
