from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
from zoneinfo import ZoneInfo

ZERO = Decimal("0")
CENT = Decimal("0.01")
SHANGHAI = ZoneInfo("Asia/Shanghai")


class Problem(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(ok, code, message):
    if not ok:
        raise Problem(code, message)


def dec(value, *, positive=False, nonnegative=True) -> Decimal:
    require(isinstance(value, (str, Decimal, int)) and not isinstance(value, bool),
            "INVALID_DECIMAL", "金额、份额、净值和费率须用十进制字符串，不能传入浮点数")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise Problem("INVALID_DECIMAL", "请输入有效的十进制数")
    require(result.is_finite() and abs(result) < Decimal("1000000000000000")
            and result.as_tuple().exponent >= -12, "INVALID_DECIMAL", "数字无效、过大或精度超过 12 位小数")
    require(not nonnegative or result >= 0, "INVALID_AMOUNT", "数值不能为负")
    require(not positive or result > 0, "INVALID_AMOUNT", "数值必须大于零")
    return result


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def amount(value, *, positive=False) -> Decimal:
    value = dec(value, positive=positive)
    require(value == money(value), "AMOUNT_PRECISION", "人民币金额最多两位小数")
    return value


def text(value) -> str:
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise Problem("INVALID_TIME", "时间必须是含时区的 ISO8601 格式")
    require(parsed.tzinfo is not None, "INVALID_TIME", "时间必须包含时区")
    return parsed.astimezone(timezone.utc)


def day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise Problem("INVALID_DATE", "日期格式应为 YYYY-MM-DD")


def trade_date(at: str, rule: dict) -> str:
    require(rule.get("verified"), "RULES_UNVERIFIED", "交易日历和截止规则尚未核实")
    local = stamp(at).astimezone(ZoneInfo(rule.get("timezone", "Asia/Shanghai")))
    dates = sorted(rule.get("open_dates", []))
    cutoff = time.fromisoformat(rule.get("cutoff", "15:00:00"))
    inclusive = rule.get("cutoff_inclusive", False)
    for candidate in dates:
        date_ = day(candidate)
        if date_ > local.date() or (date_ == local.date() and
                (local.time() < cutoff or (inclusive and local.time() == cutoff))):
            return candidate
    raise Problem("CALENDAR_MISSING", "已核实的交易日历没有覆盖下一开放日")


def purchase_preview(gross, nav, rule):
    gross, nav = amount(gross, positive=True), dec(nav, positive=True)
    require(rule.get("verified"), "RULES_UNVERIFIED", "申购费用规则尚未核实")
    rate = dec(rule.get("purchase_rate", "0"))
    net = money(gross / (1 + rate))
    step = dec(rule.get("unit_step", "0.01"), positive=True)
    units = (net / nav / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
    return {"gross": gross, "net": net, "fee": gross - net, "units": units}


def fee_rate(rule, acquired, on):
    require(rule.get("verified"), "RULES_UNVERIFIED", "赎回费用和批次顺序尚未核实")
    require(acquired is not None, "HISTORY_INCOMPLETE", "缺少批次持有期起算日期")
    days = (day(on) - day(acquired)).days
    require(days >= 0, "BEFORE_AVAILABLE_DATE", "交易日在持有期起算日前")
    for tier in rule.get("redemption_tiers", []):
        if days >= tier["min_days"] and (tier.get("max_days") is None or days < tier["max_days"]):
            return dec(tier["rate"])
    raise Problem("RULES_UNVERIFIED", "持有天数没有对应的已核实费率档位")


def redemption_preview(lots, units, nav, rule, on):
    units, nav = dec(units, positive=True), dec(nav, positive=True)
    require(rule.get("lot_method") == "FIFO", "RULES_UNVERIFIED", "首版费用预览仅支持已核实 FIFO 规则")
    left, allocations, gross, fee = units, [], ZERO, ZERO
    for lot in sorted(lots, key=lambda x: (x.get("acquired") or "9999", x["id"])):
        available = dec(lot["units"]) - dec(lot.get("reserved", "0"))
        if lot.get("available_from") and lot["available_from"] > on:
            continue
        take = min(left, available)
        if take <= 0:
            continue
        rate = fee_rate(rule, lot.get("acquired"), on)
        part_gross = money(take * nav)
        part_fee = money(part_gross * rate)
        cost = None if lot.get("cost") is None else (
            dec(lot["cost"]) if take == dec(lot["units"]) else money(dec(lot["cost"]) * take / dec(lot["units"])))
        allocations.append({"lot_id": lot["id"], "units": take, "fee": part_fee, "cost": cost, "rate": rate})
        gross += part_gross
        fee += part_fee
        left -= take
        if not left:
            break
    require(not left, "UNITS_RESERVED", "可赎回份额不足或尚未到可赎回日期")
    return {"units": units, "gross": gross, "fee": fee, "net": gross - fee, "allocations": allocations}


def solve_net_target(lots, target, nav, rule, on):
    target, step = amount(target, positive=True), dec(rule.get("unit_step", "0.01"), positive=True)
    total = sum((dec(l["units"]) - dec(l.get("reserved", "0")) for l in lots
                 if not l.get("available_from") or l["available_from"] <= on), ZERO)
    high = int(total / step)
    require(high > 0, "UNITS_RESERVED", "没有可赎回份额")
    require(redemption_preview(lots, high * step, nav, rule, on)["net"] >= target,
            "TARGET_UNREACHABLE", "目标净到账额超过当前可赎回份额的估计净额")
    # Supported proportional FIFO fee schedules have a nondecreasing net function.
    low = 1
    while low < high:
        mid = (low + high) // 2
        if redemption_preview(lots, mid * step, nav, rule, on)["net"] >= target:
            high = mid
        else:
            low = mid + 1
    return redemption_preview(lots, low * step, nav, rule, on)


def allocate_budget(proposals, spendable):
    left, result = max(ZERO, dec(spendable, nonnegative=False)), []
    for proposal in proposals:
        allocated = min(left, dec(proposal))
        allocated = money(allocated)
        result.append(allocated)
        left -= allocated
    return result
