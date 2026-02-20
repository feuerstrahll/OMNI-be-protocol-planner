- **2×2 Crossover:** Use within-subject CV (`CV`) and total `n` (per sequence). In PowerTOST, set `design = "2x2"`.  
  Example: `sampleN.TOST(CV=0.20)` produces "Study design: 2x2 crossover":contentReference[oaicite:13]{index=13}.

- **Full Replicate (4-period, 2×2×4):** Use intra-subject CV (`CV`) and total `n`. Set `design = "2x2x4"`.  
  Example: `sampleN.TOST(CV=0.40, theta0=0.90, design="2x2x4")` yields "Study design: 2x2x4 (4 period full replicate)":contentReference[oaicite:14]{index=14}.

- **Partial Replicate (3-period, 2×2×3):** Similar to full replicate but with `design = "2x2x3"`. Ensure correct sequence (e.g. TRR/RTR).  
  Example output indicates "Study design: 2x2x3 (3 period full replicate)":contentReference[oaicite:15]{index=15}.

- **Parallel:** Use total (inter-subject) CV (`CV`) and group sample sizes `n = c(n_T, n_R)`. Set `design = "parallel"`.  
  Example: `power.TOST(CV=0.35, n=c(52,49), design="parallel")`:contentReference[oaicite:16]{index=16} computes power for a parallel design.

- **Additional Parameters:** Always set `alpha = 0.05` (95% CI), BE limits 0.80–1.25 (unless NTI adjusted), and provide `theta0` if expecting non-unity ratio.
