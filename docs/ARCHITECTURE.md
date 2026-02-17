# Architecture

## Overview

Pipeline is split into three layers with a FastAPI backend and Streamlit frontend:

- Layer 1: NCBI E-utilities search + abstract extraction + regex PK parsing + validation
- Layer 2: Rule-based CVintra range estimation (interpretable)
- Layer 3: Monte Carlo risk estimation using approximate TOST power on log-scale

## Data Flow

1. UI sends `POST /search_sources` with `inn`.
2. UI selects records and calls `POST /extract_pk`.
3. UI confirms CVintra manually (required).
4. UI calls `POST /select_design`.
5. UI calls `POST /calc_sample_size` (PowerTOST if available).
6. UI calls `POST /variability_estimate`.
7. UI calls `POST /risk_estimate`.
8. UI calls `POST /reg_check`.
9. UI calls `POST /build_docx` to create synopsis.

## Explainability

- Each numeric value includes evidence with source (PMID/URL) and a snippet.
- Rule IDs are returned for design and variability drivers.
- Risk model returns assumptions and drivers.
