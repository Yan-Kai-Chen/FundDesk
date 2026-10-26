"""Codex-normalized single-product JSON; strings preserve financial precision."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator
from app.domain.finance import dec

TRANSACTION_METADATA = ('time_precision', 'original_category', 'reference_nav_date', 'reference_nav_date_basis')


def transaction_metadata(row):
    """Optional evidence annotations. They never represent a channel confirmation."""
    result = {k: row[k] for k in TRANSACTION_METADATA if row.get(k) is not None}
    precision = result.get('time_precision')
    if precision not in {None, 'SECOND', 'MINUTE'}:
        raise ValueError('交易时间精度无效')
    if precision == 'MINUTE' and row['displayed_at'][-2:] != '00':
        raise ValueError('分钟精度的兼容秒位须为00，不代表截图提供了秒')
    category = result.get('original_category')
    if category not in {None, 'BUY', 'SELL', 'ACTIVITY'}:
        raise ValueError('原始活动类别无效')
    if category and category != 'ACTIVITY' and category != row['side']:
        raise ValueError('原始类别与买卖方向不符')
    if category == 'ACTIVITY' and (row['side'] != 'BUY' or row['unit'] != 'CNY'):
        raise ValueError('活动仅可按人民币买入登记')
    nav_date, basis = result.get('reference_nav_date'), result.get('reference_nav_date_basis')
    if nav_date or basis:
        if category != 'ACTIVITY' or not nav_date or not isinstance(basis, str) or not basis.strip() or len(basis) > 300:
            raise ValueError('活动参考净值日须明确记录还原依据')
        nav_date = date.fromisoformat(str(nav_date))
        if nav_date.isoformat() > row['displayed_at'][:10]:
            raise ValueError('活动不得借用未来净值')
        result['reference_nav_date'] = nav_date.isoformat()
        result['reference_nav_date_basis'] = basis.strip()
    return result


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Source(StrictModel):
    sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    filename: StrictStr = Field(min_length=1, max_length=150)
    label: StrictStr = Field(default='', max_length=80)

    @field_validator('filename')
    @classmethod
    def plain_name(cls, value):
        if value in {'.','..'} or any(x in value for x in ['/', '\\', '\x00', '\r', '\n']):
            raise ValueError('只填写原始资料文件名')
        return value


class SourceRef(StrictModel):
    sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    row: int = Field(ge=1, le=1000, strict=True)


class Asset(StrictModel):
    kind: Literal['FUND', 'GOLD_FUND', 'GOLD_GRAM']
    currency: Literal['CNY']
    name: StrictStr = Field(min_length=1, max_length=80)
    channel: StrictStr = Field(min_length=1, max_length=60)
    fund_code: StrictStr | None = Field(default=None, pattern=r'^[0-9]{6}$')
    share_class: StrictStr | None = Field(default=None, min_length=1, max_length=12)
    product_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    fund_id: StrictStr | None = Field(default=None, max_length=100)

    @field_validator('name', 'channel', 'share_class', 'product_code')
    @classmethod
    def not_blank(cls, value):
        if value is not None and not value.strip(): raise ValueError('不能为空白')
        return value.strip() if value else value

    @model_validator(mode='after')
    def identity(self):
        if self.kind == 'GOLD_GRAM':
            if not self.product_code or self.fund_code or self.share_class or self.fund_id:
                raise ValueError('按克黄金填写独立 product_code，不填写基金代码/类别/ID')
        elif not self.fund_code or not self.share_class or self.product_code:
            raise ValueError('基金须填写六位代码及份额类别，不填写黄金产品代码')
        return self


class Transaction(StrictModel):
    displayed_at: StrictStr = Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
    side: Literal['BUY', 'SELL']
    unit: Literal['CNY', 'FUND_UNIT', 'GRAM']
    value: StrictStr = Field(min_length=1, max_length=40)
    status: Literal['SHOWN', 'PENDING_SETTLEMENT'] = 'SHOWN'
    source_refs: list[SourceRef] = Field(min_length=1, max_length=30)
    expected_settlement_date: date | None = None
    time_precision: Literal['SECOND', 'MINUTE'] | None = None
    original_category: Literal['BUY', 'SELL', 'ACTIVITY'] | None = None
    reference_nav_date: date | None = None
    reference_nav_date_basis: StrictStr | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def evidence_metadata(self):
        transaction_metadata(self.model_dump())
        return self

    @field_validator('displayed_at')
    @classmethod
    def valid_at(cls, value):
        datetime.fromisoformat(value)
        return value

    @field_validator('value')
    @classmethod
    def positive(cls, value):
        dec(value, positive=True)
        return value


class Nav(StrictModel):
    effective_date: date
    value: StrictStr = Field(min_length=1, max_length=40)
    source_refs: list[SourceRef] = Field(min_length=1, max_length=30)

    @field_validator('value')
    @classmethod
    def positive(cls, value):
        dec(value, positive=True)
        return value


class Position(StrictModel):
    snapshot_date: date
    units: StrictStr = Field(min_length=1, max_length=40)
    unit: Literal['FUND_UNIT', 'GRAM']
    remaining_cost_cny: StrictStr | None = Field(default=None, max_length=40)
    reported_value_cny: StrictStr | None = Field(default=None, max_length=40)
    base_units: StrictStr | None = Field(default=None, max_length=40)
    acquired_date: date | None = None
    available_from: date | None = None
    source_refs: list[SourceRef] = Field(min_length=1, max_length=30)

    @field_validator('units', 'remaining_cost_cny', 'reported_value_cny', 'base_units')
    @classmethod
    def nonnegative(cls, value):
        if value is not None: dec(value)
        return value


class AssetDataBatch(StrictModel):
    schema_version: Literal[1] = 1
    client_reference: StrictStr = Field(min_length=8, max_length=120, pattern=r'^[A-Za-z0-9_.:-]+$')
    asset: Asset
    sources: list[Source] = Field(min_length=1, max_length=100)
    transactions: list[Transaction] = Field(default_factory=list, max_length=1000)
    official_navs: list[Nav] = Field(default_factory=list, max_length=1000)
    position: Position | None = None
    history_complete: bool = Field(default=False, strict=True)
    history_completeness_basis: StrictStr = Field(default='', max_length=300)
    notes: StrictStr = Field(default='', max_length=1000)


class BatchCommit(StrictModel):
    source_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    preview_token: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool = Field(strict=True)
    initialize_position: bool = Field(default=False, strict=True)
