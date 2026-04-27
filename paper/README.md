# DV-PMI Methodology Paper

Four-to-six-page working paper describing the construction, validation, and intended use of the Della Vedova Prediction Market Indices.

## Status

Skeleton drafted. User action required before SSRN upload:

1. Resolve `\TODO{...}` markers in `methodology.tex`:
   - Sample counts as of the refresh date
   - Validation table with precise numbers against the underlying working papers
   - Out-of-sample stability table (index values at end of original sample vs current)

2. Review each section for voice and substance. Cut or expand as needed.

3. Decide whether to include figures (e.g., the calibration scatter, the 7 index time series). The current skeleton is prose-only; figures would push it to 6-8 pages.

## Build

```
cd paper
pdflatex methodology
bibtex methodology
pdflatex methodology
pdflatex methodology
```

Target output: `methodology.pdf`. Copy to `../site/public/dvpmi-methodology.pdf` so the dashboard's `/methodology` page can link it.

## Submission path

1. Polish the skeleton to final.
2. Upload to SSRN in the Behavioral & Experimental Finance eJournal (or Journal of Financial Data Science replication track).
3. Link the SSRN abstract from `/methodology` on the dashboard.
4. Tag the next dashboard release (`v0.2.0`) once the paper is live; Zenodo will archive the full release including the PDF.

## License

CC BY 4.0 for the paper. MIT for any code excerpts.
