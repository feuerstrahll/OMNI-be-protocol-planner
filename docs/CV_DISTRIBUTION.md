---

# =========================
# FILE: docs/CV_DISTRIBUTION.md
# =========================

`markdown
# CV Distribution for Risk Modelling

## Input

CV_range = [CV_min, CV_max]
confidence ∈ {high, medium, low}

---

## Base Model

Triangular distribution:
- min = CV_min
- mode = (CV_min + CV_max) / 2
- max = CV_max

---

## Confidence Adjustment

| Confidence | Range Expansion |
|------------|----------------|
| high       | none           |
| medium     | ±3%            |
| low        | ±5%            |

---

## Alternative (Production-Level)

Lognormal distribution:
- μ = ln(mean_CV)
- σ proportional to range width
- σ increases if confidence decreases

---

## Monte Carlo Simulation

- 10,000 simulations
- Sample CV from distribution
- Compute N using PowerTOST
- Estimate probability of BE success

Outputs:
- expected_N
- p_success
- sensitivity