from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProblemDetail(ApiModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class UserView(ApiModel):
    id: str
    username: str
    role: str
    auth_mode: str


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class InstrumentView(ApiModel):
    id: int
    code: str
    ts_code: str | None
    exchange: str | None
    name: str | None
    industry: str | None = None
    area: str | None
    status: str


class InstrumentPage(ApiModel):
    items: list[InstrumentView]
    total: int
    page: int
    page_size: int

class InstrumentFacetItem(ApiModel):
    value: str
    count: int


class InstrumentFacets(ApiModel):
    industries: list[InstrumentFacetItem]
    areas: list[InstrumentFacetItem]
    eligible_count: int



class BarView(ApiModel):
    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None
    turnover_rate: float | None


class BarSeries(ApiModel):
    instrument: InstrumentView
    timeframe: str
    adjustment: str
    items: list[BarView]


StockCode = Annotated[str, Field(pattern=r"^\d{6}$")]
SignalType = Literal["1", "1p", "2", "2s", "3a", "3b"]


class StrictPayload(ApiModel):
    model_config = ConfigDict(extra="forbid")


class EmptyPayload(StrictPayload):
    pass


class RefreshPayload(StrictPayload):
    dates: list[date] = Field(default_factory=list, max_length=3700)
    start_date: date | None = None
    end_date: date | None = None
    lookback_days: int | None = Field(default=None, ge=1, le=3650)
    lookahead_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def validate_range(self) -> "RefreshPayload":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class ScanPayload(StrictPayload):
    codes: list[StockCode] = Field(default_factory=list, max_length=6000)
    types: list[SignalType] = Field(default_factory=list)
    scan_side: Literal["buy", "sell"] = "buy"
    industries: list[str] = Field(default_factory=list, max_length=100)
    areas: list[str] = Field(default_factory=list, max_length=100)
    exclude_st: bool = True
    min_net_mf_amount: float | None = None
    min_main_net_amount: float | None = None
    rank_type: Literal[
        "top_gainers",
        "top_losers",
        "top_volume",
        "top_amount",
        "top_turnover",
        "dragon_tiger",
    ] = "top_gainers"
    top_n: int = Field(default=200, ge=1, le=500)


class BatchAnalysisPayload(StrictPayload):
    codes: list[StockCode] = Field(min_length=1, max_length=1000)
    trade_date: date | None = None
    analysis_date: date | None = None


class StockAnalysisPayload(StrictPayload):
    code: StockCode
    include_ai: bool = False


class DataRefreshPayload(StrictPayload):
    codes: list[StockCode] = Field(default_factory=list, max_length=6000)
    start_date: date | None = None
    end_date: date | None = None
    lookback_days: int = Field(default=10, ge=1, le=3650)
    timeframe: Literal["DAY", "WEEK", "MON", "1M", "5M", "15M", "30M"] = "DAY"
    adjustment: Literal["QFQ", "HFQ", "NONE"] = "QFQ"
    refresh_master_data: bool | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "DataRefreshPayload":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class EmptyJobCreate(ApiModel):
    kind: Literal["market_radar.refresh"]
    payload: EmptyPayload = Field(default_factory=EmptyPayload)
    force: bool = False


class RefreshJobCreate(ApiModel):
    kind: Literal["limit_up.refresh", "ipo.refresh"]
    payload: RefreshPayload = Field(default_factory=RefreshPayload)
    force: bool = False


class ScanJobCreate(ApiModel):
    kind: Literal["scan.buy", "scan.sell", "screen.hot", "screen.smart"]
    payload: ScanPayload = Field(default_factory=ScanPayload)
    force: bool = False


class BatchAnalysisJobCreate(ApiModel):
    kind: Literal["limit_up.analyze", "ipo.analyze"]
    payload: BatchAnalysisPayload
    force: bool = False


class StockAnalysisJobCreate(ApiModel):
    kind: Literal["stock.analyze"]
    payload: StockAnalysisPayload
    force: bool = False


class DataRefreshJobCreate(ApiModel):
    kind: Literal["data.refresh"]
    payload: DataRefreshPayload = Field(default_factory=DataRefreshPayload)
    force: bool = False


JobCreate = Annotated[
    EmptyJobCreate
    | RefreshJobCreate
    | ScanJobCreate
    | BatchAnalysisJobCreate
    | StockAnalysisJobCreate
    | DataRefreshJobCreate,
    Field(discriminator="kind"),
]


class JobView(ApiModel):
    id: str
    retry_of_id: str | None = None
    kind: str
    status: str
    progress: float
    total: int
    completed: int
    failed: int
    message: str | None
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobItemView(ApiModel):
    id: str
    subject_key: str | None
    instrument_id: int | None
    status: str
    attempts: int
    result: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class JobStageView(ApiModel):
    stage_key: str
    status: Literal["started", "progress", "completed", "failed"]
    processed: int | None = None
    coverage_rate: float | None = None
    provider_chain: list[dict[str, Any]] | None = None
    duration_ms: int | None = None
    error: str | None = None
    message: str | None = None
    updated_at: datetime


class JobStagesView(ApiModel):
    job_id: str
    status: str
    items: list[JobStageView]


class JobAccepted(ApiModel):
    job_id: str
    status: str
    deduplicated: bool = False


class ScanResultView(ApiModel):
    instrument_id: int | None
    code: str | None = None
    name: str | None = None
    scan_kind: str
    signal_type: str | None
    signal_date: date | None
    rank: int | None
    score: float | None
    payload: dict[str, Any] | None


class WatchlistItemInput(ApiModel):
    instrument_id: int
    note: str | None = None
    tag_names: list[str] = Field(default_factory=list)


class WatchlistItemUpdate(ApiModel):
    note: str | None = None
    tag_names: list[str] = Field(default_factory=list)
    position: int | None = Field(default=None, ge=0)


class WatchlistItemView(ApiModel):
    id: str
    instrument: InstrumentView
    position: int
    note: str | None
    tags: list[str]


class WatchlistView(ApiModel):
    id: str
    name: str
    is_default: bool
    items: list[WatchlistItemView]


class ScheduleUpdate(ApiModel):
    job_kind: Literal[
        "market_radar.refresh",
        "limit_up.refresh",
        "ipo.refresh",
        "data.refresh",
        "screen.hot",
        "screen.smart",
    ]
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    enabled: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class SecretUpdate(ApiModel):
    value: str = Field(min_length=1, max_length=4096)


class SecretStatus(ApiModel):
    key: str
    configured: bool
    source: str


class DataHealthFailure(ApiModel):
    job_id: str
    status: str
    error: str | None = None
    created_at: datetime


class DataHealthCategory(ApiModel):
    key: str
    status: Literal["fresh", "partial", "stale", "missing"]
    source: str | None = None
    data_time: datetime | date | None = None
    coverage_rate: float | None = None
    missing_fields: list[str]
    single_source: bool
    last_failure: DataHealthFailure | None = None
    recommendation: str | None = None
    affects_overall: bool
    eligible_count: int | None = None
    current_count: int | None = None
    lagging_count: int | None = None
    missing_count: int | None = None
    fresh_threshold: float | None = None


class DataHealthView(ApiModel):
    status: Literal["fresh", "partial", "stale", "missing"]
    checked_at: datetime
    expected_trade_date: date | None = None
    database_backend: str
    backup_mode: Literal["application", "external_required"]
    categories: list[DataHealthCategory]


class SystemStatus(ApiModel):
    status: str
    version: str
    auth_mode: str
    database: str
    worker_required: bool
    worker_status: Literal["healthy", "stale", "missing"]
    last_heartbeat: datetime | None = None
    active_job: str | None = None
    data_counts: dict[str, int]


class Catalog(ApiModel):
    timeframes: list[str]
    adjustments: list[str]
    buy_signal_types: list[str]
    sell_signal_types: list[str]
    analysis_kinds: list[str]
    algorithm_versions: dict[str, str]
