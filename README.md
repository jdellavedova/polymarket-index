# Della Vedova Prediction Market Indices (DV-PMI)

Weekly-updated behavioral and microstructure indices derived from on-chain prediction-market trade data. Current coverage: Polymarket.

Maintained by Joshua Della Vedova, Knauss School of Business, University of San Diego.

Live site: [jdellavedova.com](https://jdellavedova.com) (pending launch)

## Indices

1. Probability Weighting Index (PWI)
2. Market Calibration Curve
3. Execution Edge Monitor
4. Private Information Index (PII)
5. Bot Share of Volume
6. Longshot / Favorite Price Gap
7. Market Efficiency Trend

See `pipeline/` for aggregation scripts and `site/` for the static site.

## Reproducibility

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # fill in ALCHEMY_API_KEY
python pipeline/run_all.py
cd site && npm install && npm run dev
```

## Citation

See `CITATION.cff` for the canonical citation format. Each tagged release has a Zenodo DOI.

## License

Code: MIT. Data: CC BY 4.0. See `LICENSE`.
