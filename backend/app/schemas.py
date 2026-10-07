from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

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
    horizons: list[Horizon] = Field(
        default_factory=lambda: ["next_day", "one_month", "three_months"], min_length=1, max_length=3
    )
    families: list[Literal["naive", "arima", "sarima", "ridge", "lightgbm", "garch", "nhits", "patchtst"]] = (
        Field(default_factory=lambda: ["arima", "ridge"], min_length=1, max_length=8)
    )
    max_trials: int = Field(default=8, ge=1, le=30)
    time_budget_seconds: int = Field(default=600, ge=10, le=21600)
    evaluation_mode: Literal["cross_validation", "holdout", "research"] = "cross_validation"
    match_threshold: float = Field(default=0.95, ge=0.5, le=1)
    trials_per_family: int | None = Field(default=None, ge=1, le=40)
    evaluation_samples: int = Field(default=128, ge=32, le=512)
    search_profile: Literal["standard", "expanded"] = "standard"
    ridge_alpha_min: float = Field(default=0.01, gt=0, le=1e6)
    ridge_alpha_max: float = Field(default=1000, gt=0, le=1e6)
    arima_max_order: int = Field(default=2, ge=1, le=3)
    coverage: float = Field(default=0.9, ge=0.7, le=0.99)
    min_improvement: float = Field(default=0.02, ge=0, le=0.5)
    seed: int = 42

    @model_validator(mode="after")
    def ordered_range(self):
        if self.ridge_alpha_min > self.ridge_alpha_max:
            raise ValueError("Ridge参数下限不能大于上限")
        return self


class PredictionRequest(SnapshotRequest):
    data_snapshot_id: str | None = None
    horizon: Horizon
    model_ids: list[str] = Field(min_length=1, max_length=3)
    with_ai: bool = False
    allow_unvalidated: bool = False
    reference_price: float | None = Field(default=None, gt=0)
    observation_start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
