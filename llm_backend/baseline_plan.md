# Comprehensive Engineering Specification: Causal-Aware KPI Anomaly Detection & Intelligence Pipeline

This specification upgrades the architecture from simple point anomaly detection to a **causal-aware, multi-stage intelligence pipeline**. The system disentangles time-series components, detects point, collective, and structural anomalies, groups them into contextual **Event Windows**, and executes targeted causal inference across observational data.

---

### 1. Unified Pipeline Topology

```
[ Raw CSV Data (38 Columns, 10 Months) ]
                  │
                  ▼
┌────────────────────────────────────────────────────────┐
│ Stage 1: Ingestion, Validation & Metric Engine         │
│  - Strict Pandera Schema Contract                      │
│  - Deterministic Calculation: CAC, ROAS, Net Margin    │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: Time-Series Decomposition                     │
│  - Robust STL Decomposition (Trend, Seasonality, Rem.) │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 3: Hybrid Anomaly & Change-Point Engine          │
│  - Univariate: Robust Z-Score (MAD) on Forecast Errors │
│  - Change-Point: PELT / CUSUM on Mean & Variance       │
│  - Multivariate: Mahalanobis Distance on KPI Residuals │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 4: Event Window Aggregator                       │
│  - Temporal Clustering: Point vs Trend vs Structural   │
│  - Structured Event Payload Generation                 │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 5: Causal Inference & Driver Attribution Engine  │
│  - Quasi-Experiments: DiD & Synthetic Control (Region) │
│  - Interrupted Time Series / BSTS (Campaign Shifts)    │
│  - SCM / Do-Calculus & Shapley Attribution (DAG Levers)│
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 6: Persona & Action Synthesis Interface          │
│  - Confidence Scoring & Abstention Logic               │
│  - Governed JSON Payload for LLM Narration             │
└────────────────────────────────────────────────────────┘

```

---

### 2. Detailed Component Specifications

#### Stage 1: Ingestion, Validation & Deterministic Metric Derivation

* **Input:** Multi-channel daily retail records containing marketing, traffic, inventory, sales, and financial ledger data.
* **Deterministic KPI Calculations:**

$$\text{CAC}_t = \frac{\text{Sales Marketing Expenses}_t}{\text{New Customers}_t}$$


$$\text{ROAS}_t = \frac{\text{Revenue From Ads}_t}{\text{Cost Of Ads}_t}$$


$$\text{Net Margin}_t = \left(\frac{\text{Total Revenue}_t - \text{Total Expenses}_t}{\text{Total Revenue}_t}\right) \times 100$$


* **Sanitization & Edge Cases:**
* Guard against division-by-zero (`Cost Of Ads = 0`, `New Customers = 0`, `Total Revenue = 0`) by replacing invalid quotients with `NaN` followed by bounded rolling medians.
* Temporal sorting by `Date` and multi-index alignment across `[Region, Channel, Product category]`.



---

#### Stage 2: Time-Series Signal Decomposition

Raw KPI movements blend calendar cycles with real performance shifts. To prevent false positives on expected cycles (e.g., weekend ad spikes), each KPI is decomposed:

$$Y_t = T_t + S_t + C_t + \epsilon_t$$

* **Trend ($T_t$):** Long-term progression (e.g., quarterly growth in CAC).
* **Seasonality ($S_t$):** Recurring intra-week patterns (e.g., Sunday retail footfall).
* **Calendar Effects ($C_t$):** Paydays, holiday surges, or promotional days.
* **Irregular Remainder ($\epsilon_t$):** Unexplained noise and true candidate anomaly signals.
* **Methodology:** Use **LOESS-based Robust STL (Seasonal-Trend decomposition using LOESS)** with a 7-day periodicity window to isolate $\epsilon_t$ from normal weekly oscillations.

---

#### Stage 3: Hybrid Anomaly & Change-Point Engine

The system evaluates three distinct anomaly modalities simultaneously:

```
                  ┌──────────────────────────────────────────────┐
                  │          Decomposed Remainder Signal         │
                  └───────┬──────────────┬──────────────┬────────┘
                          │              │              │
                          ▼              ▼              ▼
                   [ Point Engine ] [ Change-Point ] [ Cross-KPI ]

```

##### 1. Univariate Point Anomalies (Forecast Residuals)

* **Forecasting Layer:** Train an autoregressive model (SARIMAX or LightGBM with lag features: $t-1, t-7, t-14$, rolling 7-day mean/std) strictly on an expanding historical window (preventing lookahead bias).
* **Residual Extraction:** $e_t = Y_t - \hat{Y}_t$
* **Robust Z-Score (MAD):**

$$\text{Modified } Z_t = \frac{e_t - \operatorname{Median}(e)}{1.4826 \times \operatorname{MAD}(e)}$$



*Flag as a Point Anomaly if $\vert{}\text{Modified } Z_t\vert{} > 2.5$.*

##### 2. Structural & Trend Anomalies (Change-Point Detection)

* **Objective:** Detect persistent distribution shifts (e.g., ad channel cost structures permanently shifting upward).
* **Methodology:** Run **PELT (Pruned Exact Linear Time)** with a Radial Basis Function (RBF) cost function or **CUSUM (Cumulative Sum)** on rolling 30-day windows of mean and variance:

$$\text{CUSUM}^+_t = \max(0, \text{CUSUM}^+_{t-1} + (Y_t - \mu_0 - k))$$


$$\text{CUSUM}^-_t = \max(0, \text{CUSUM}^-_{t-1} - (Y_t - \mu_0 + k))$$


* When $\text{CUSUM} > h$ (decision threshold), flag a **Structural Break $\tau$**.

##### 3. Cross-KPI Multivariate Anomalies

* **Objective:** Detect anomalous combinations where individual KPIs appear normal, but their joint distribution breaks (e.g., `Revenue From Ads` remains steady while `Cost Of Ads` doubles and `New Customers` drops).
* **Methodology:** Calculate the **Mahalanobis Distance** on the multivariate residual vector $E_t = [e_{\text{CAC}}, e_{\text{ROAS}}, e_{\text{Margin}}]^T$:

$$D_t = \sqrt{(E_t - \mu_E)^T \Sigma_E^{-1} (E_t - \mu_E)}$$


* Compare $D_t^2$ against a Chi-Square distribution critical value ($\chi^2_k(\alpha)$ with $k=3$ degrees of freedom).

---

#### Stage 4: Causal-Aware Event Windowing

The anomaly detector does not simply output binary flags. It aggregates contiguous or related anomalies into an **Event Window Payload**:

* **Temporal Clustering:** Groups consecutive anomalous points ($t_{\text{start}} \to t_{\text{end}}$) or structural break intervals.
* **Classification Tag:** Tags the event as `Point Deviation`, `Sustained Trend Drift`, `Structural Break`, or `Multivariate Divergence`.
* **Payload Structure:**

```json
{
  "event_id": "EV-2026-03-14",
  "anomaly_type": "Structural Break & Multivariate Divergence",
  "window": {"start": "2026-03-14", "end": "2026-03-21"},
  "primary_kpis_affected": ["CAC", "ROAS"],
  "observed_deviations": {
    "CAC": {"expected": 42.50, "actual": 78.10, "pct_delta": 83.7},
    "ROAS": {"expected": 3.80, "actual": 1.95, "pct_delta": -48.6}
  },
  "candidate_covariates": [
    "Cost Of Ads", "Sales Marketing Expenses", "Traffic source", 
    "Website Sessions", "Store Entrances", "COGS"
  ]
}

```

---

#### Stage 5: Multi-Method Causal Inference & SCM Attribution

Upon receiving an Event Window, the causal engine selects the appropriate analytical method based on available controls and graph structure:

```
                                  [ Event Window Received ]
                                              │
               ┌──────────────────────────────┼──────────────────────────────┐
               ▼                              ▼                              ▼
    [ Multi-Entity Available ]     [ Known Launch Timestamp ]      [ Multi-Driver DAG ]
               │                              │                              │
               ▼                              ▼                              ▼
    Difference-in-Differences     Interrupted Time Series /       SCM (Do-Calculus &
       / Synthetic Control               CausalImpact            Shapley Attribution)

```

##### Method A: Difference-in-Differences (DiD) & Synthetic Control

* **Condition:** When an anomaly is isolated to a specific `Region` or `Channel` while other entities remain unaffected.
* **Mechanism:** Use unaffected regions/channels as the control group $C$ against treated entity $T$:

$$\hat{\delta}_{\text{DiD}} = (\bar{Y}_{T,\text{post}} - \bar{Y}_{T,\text{pre}}) - (\bar{Y}_{C,\text{post}} - \bar{Y}_{C,\text{pre}})$$


* **Synthetic Control:** If parallel trends fail, build a convex combination of donor regions to construct the counterfactual path $Y_{T,t}^{\text{synthetic}} = \sum w_j Y_{j,t}$.

##### Method B: Interrupted Time Series (ITS) / Bayesian Structural Time Series (BSTS)

* **Condition:** System-wide shocks or known operational events at time $\tau$ (e.g., ad vendor repricing or algorithm changes).
* **Mechanism:** Model the counterfactual trajectory using `CausalImpact` (BSTS), generating the estimated KPI path had the intervention not occurred:

$$\text{Causal Effect}_t = Y_t - \hat{Y}_t^{(\text{counterfactual})}$$



##### Method C: Structural Causal Model & Shapley Driver Attribution

* **Condition:** Multi-factor shifts requiring exact root-cause decomposition across the defined DAG.


* **Governed Structural Equations:**
1. $\text{CAC} = f(\text{Sales Marketing Expenses}, \text{New Customers})$
2. $\text{New Customers} = f(\text{Total Visitors}, \text{Traffic source}, \text{Website Sessions})$
3. $\text{ROAS} = f(\text{Revenue From Ads}, \text{Cost Of Ads})$
4. $\text{Net Margin} = f(\text{Total Revenue}, \text{Total Expenses})$
5. $\text{Total Expenses} = f(\text{COGS}, \text{Avg Inventory Cost}, \text{Sales Marketing Expenses})$


* **Attribution Formulation:** Estimate local treatment effects via Double Machine Learning (DML) and compute Shapley values $\phi_i$ to distribute the residual variance among candidate drivers:



$$\Delta \text{KPI} = \sum_{i \in \text{Drivers}} \phi_i + \epsilon$$



---

#### Stage 6: Verification, Abstention & Persona Interface

To ensure reliability, the pipeline enforces strict confidence thresholds and role-based outputs:

* **Confidence Scoring:** Computed from the signal-to-noise ratio, prediction interval width, and SCM standard errors.


* **Abstention Gate:** If data history is sparse ($<14$ records in a category) or confidence falls below $\theta = 0.70$, the pipeline outputs an explicit **Abstain Payload** requesting human analyst clarification rather than speculative insights.


* **Persona-Specific Delivery:**
* **Executive Persona:** Summarized net margin impact, high-level drivers (CAC vs ROAS), and financial exposure.
* **Marketing / Growth Persona:** Granular ad spend allocations, CPC shifts by traffic source, and campaign recommendations.
* **Supply / Operations Persona:** COGS variations, inventory holding cost changes, and supplier pricing impacts.



---

### 3. Technical Stack & Tooling

| Functional Layer | Recommended Libraries | Purpose |
| --- | --- | --- |
| **Data Schema & Quality** | `pandas`, `pandera`, `pydantic` | Declarative validation and typed metric transformations.

 |
| **Decomposition & Point Anomaly** | `statsmodels` (STL, SARIMAX), `scikit-learn` | Trend/seasonality extraction and robust residual forecasting.

 |
| **Change-Point & Multi-KPI** | `ruptures` (PELT), `scipy.spatial.distance` | Structural break localization and Mahalanobis distance calculation. |
| **Quasi-Experimental Causal** | `causalimpact`, `linearmodels` (DiD) | Counterfactual time-series synthesis and natural experiment evaluation. |
| **SCM & Variance Attribution** | `dowhy`, `econml`, `shap`, `networkx` | Do-calculus, Double Machine Learning, and Shapley attribution.

 |

---

### 4. Implementation Guardrails: What to Do vs. What to Avoid

#### What to Do

* **Use Expanding Window Validation:** Always train baseline forecasters strictly on past records ($< t$) to reflect true production constraints.
* **Preserve Parallel Trends in DiD:** Test for parallel pre-treatment trends between regions before trusting Difference-in-Differences estimates.
* **Normalize Residuals Prior to Distance Calculation:** Ensure all metrics in multivariate checks ($E_t$) are standardized by their historical interquartile ranges.
* **Separate Anomaly Detection from Root-Cause Analysis:** Keep Stage 3 (flagging deviations) mathematically independent from Stage 5 (identifying why deviations occurred).



#### What to Avoid

* **Avoid Flat Standard Deviation Thresholds:** Never use $3\sigma$ standard deviations on non-decomposed raw data; holiday and weekly seasonality will trigger false alarms.
* **Avoid Training Heavy Deep Neural Networks on Small Datasets:** On 10 months (~300 daily rows), avoid Transformers or deep LSTMs; they will overfit and create unstable baseline forecasts.
* **Avoid Unconstrained Causal Graphs:** Do not let automated discovery create impossible edges (e.g., Net Margin causing Ad Spend); enforce directional semantic constraints.


* **Avoid LLM Metric Calculations:** Never allow an LLM to compute statistics, residuals, or causal weights; restrict generative models strictly to rendering persona prose from the structured output payload.