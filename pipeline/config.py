"""Shared paths and configuration for the dashboard pipeline."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

DATA_OUT = REPO_ROOT / "site" / "public" / "data"
DATA_OUT.mkdir(parents=True, exist_ok=True)

BLOCKCHAIN = Path("H:/Research/10. Prediction/data/blockchain")
PAPER4 = BLOCKCHAIN / "paper4"
INSIDER_OUT = Path("G:/My Drive/1. Research/1. Polymarket/2. Insider/output")

SOURCES = {
    "weekly_pwi": PAPER4 / "weekly_pwi.csv",
    "calibration_nonbot": PAPER4 / "calibration_nonbot_market.csv",
    "weekly_alpha_by_type": PAPER4 / "weekly_alpha_by_type.csv",
    "stage19_significant_wallets": INSIDER_OUT / "stage19_significant_wallets.csv",
}


def require_source(key: str) -> Path:
    path = SOURCES[key]
    if not path.exists():
        raise FileNotFoundError(f"Source file missing: {path}")
    return path


def alchemy_key() -> str:
    key = os.getenv("ALCHEMY_API_KEY")
    if not key:
        raise RuntimeError("ALCHEMY_API_KEY not set. Copy .env.example to .env and fill it in.")
    return key
