from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

Horizon = Literal["next_day", "one_month", "three_months"]
PriceBasis = Literal["raw", "qfq", "hfq"]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StockRequest(StrictRequest):
    exchange: Literal["SSE", "SZSE", "BSE"]
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=80)


class CommitRequest(StrictRequest):
    policy: Literal["skip", "replace"] = "skip"


class SnapshotRequest(StrictRequest):
    stock_id: str
    price_basis: PriceBasis = "raw"


class TrainingRequest(SnapshotRequest):
    data_snapshot_id: str | None = None
    horizons: list[Horizon] = Field(default_factory=lambda: ["next_day", "one_month", "three_months"], min_length=1, max_length=3)
    families: list[Literal["naive", "arima", "sarima", "ridge", "lightgbm", "garch", "nhits", "patchtst"]] = Field(default_factory=lambda: ["arima", "ridge"], min_length=1, max_length=8)
    max_trials: int = Field(default=8, ge=1, le=30)
    time_budget_seconds: int = Field(default=600, ge=10, le=3600)
    coverage: float = Field(default=0.9, ge=0.7, le=0.99)
    min_improvement: float = Field(default=0.02, ge=0, le=0.5)
    seed: int = 42


class PredictionRequest(SnapshotRequest):
    data_snapshot_id: str | None = None
    horizon: Horizon
    model_ids: list[str] = Field(min_length=1, max_length=3)
    with_ai: bool = False
    reference_price: float | None = Field(default=None, gt=0)
    observation_start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
