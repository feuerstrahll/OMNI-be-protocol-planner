# Assumptions

- PK extraction is regex-based over abstracts only (no full-text parsing).
- CVintra must be manually confirmed in UI before any sample size calculation.
- If CVintra not found, conservative presets (20/30/40/50%) are suggested.
- PowerTOST is preferred but optional; approximate formula used when Rscript is unavailable.
- Risk model uses approximate log-scale TOST power for Monte Carlo; intended for MVP transparency.
- Evidence for calculated values uses synthetic `calc://` URLs.
- The case PDF is not parsed programmatically; rules are seeded manually for MVP.
