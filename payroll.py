"""Effective-dated base-pay schedules and one-time credit for completed periods.

Fixed pay is contractual, not inferred from attendance. Accounts confirms a full
completed period before credit; delivery bonuses and payout review stay separate.
"""
from calendar import monthrange
from datetime import date, timedelta
from typing import Literal
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field


def core():
    import secure_backend
    return secure_backend


class PayRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_pay: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    peak_bonus: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    peak_hours: list[int] = Field(max_length=24)
    pay_basis: Literal['none', 'per_delivery', 'daily', 'weekly', 'monthly'] = 'per_delivery'
    effective_from: date | None = None


class PeriodCredit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    period_start: date
    confirmed: bool = Field(strict=True)


def history(state, driver_id):
    stored = state.get('driver_pay_rules', {}).get(driver_id, {})
    if stored.get('history'):
        return stored['history']
    # Existing v11 pay rules remain in force until the first dated replacement.
    return [dict(stored, pay_basis='per_delivery', effective_from='1970-01-01', version=0)] if stored else []


def rule_for(state, driver_id, day=None):
    day = day or core().today()
    return next((r for r in reversed(history(state, driver_id)) if r['effective_from'] <= day), {})


def boundary(rule, index):
    start = date.fromisoformat(rule['effective_from'])
    if rule['pay_basis'] == 'monthly':
        months = start.year * 12 + start.month - 1 + index
        year, month = divmod(months, 12)
        return date(year, month + 1, min(start.day, monthrange(year, month + 1)[1]))
    return start + timedelta(days=index * (7 if rule['pay_basis'] == 'weekly' else 1))


def on_boundary(rule, day):
    start = date.fromisoformat(rule['effective_from'])
    if rule['pay_basis'] == 'monthly':
        return boundary(rule, (day.year - start.year) * 12 + day.month - start.month) == day
    return (day - start).days % (7 if rule['pay_basis'] == 'weekly' else 1) == 0


def periods_due(state, driver_id):
    today = date.fromisoformat(core().today())
    rules = history(state, driver_id)
    paid = {e['source'] for e in state.get('driver_earnings', {}).values() if e['driver_id'] == driver_id}
    result = []
    for i, rule in enumerate(rules):
        if rule['pay_basis'] not in {'daily', 'weekly', 'monthly'} or not rule['base_pay_paise']:
            continue
        until = min(today, date.fromisoformat(rules[i + 1]['effective_from'])) if i + 1 < len(rules) else today
        index = 0
        # Keep the source rule unchanged; monthly dates always use its anniversary.
        while boundary(rule, index + 1) <= until:
            start, end = boundary(rule, index), boundary(rule, index + 1)
            source = 'salary:' + driver_id + ':' + start.isoformat()
            if source not in paid:
                result.append(dict(period_start=start.isoformat(), period_end=(end - timedelta(days=1)).isoformat(),
                    end_exclusive=end.isoformat(), amount=rule['base_pay_paise'] / 100,
                    amount_paise=rule['base_pay_paise'], pay_basis=rule['pay_basis'], version=rule['version'], source=source))
            index += 1
    return result


def schedule_view(state, driver_id):
    today = core().today()
    return dict(pay_rule=rule_for(state, driver_id),
        scheduled_pay_rules=[r for r in history(state, driver_id) if r['effective_from'] > today],
        due_pay_periods=periods_due(state, driver_id))


def install_payroll(app):
    c = core()
    from workforce import staff, driver_record, add_earning
    app.router.routes[:] = [r for r in app.routes if getattr(r, 'path', '') != '/admin/drivers/{driver_id}/pay-rule']

    @app.patch('/admin/drivers/{driver_id}/pay-rule', tags=['Driver earnings'])
    def save_rule(driver_id: str, request: Request, body: PayRule):
        with c.transaction() as state:
            user = staff(state, request, True)
            driver_record(state, driver_id)
            c.require(all(type(h) is int and 0 <= h <= 23 for h in body.peak_hours), 'Use whole peak hours from 0 to 23.', 422)
            day = body.effective_from or date.fromisoformat(c.today())
            c.require(day >= date.fromisoformat(c.today()), 'Pay rules cannot be backdated. Use a referenced adjustment for corrections.', 422)
            rules = history(state, driver_id)
            values = dict(base_pay_paise=c.money(body.base_pay) if body.pay_basis != 'none' else 0,
                peak_bonus_paise=c.money(body.peak_bonus), peak_hours=sorted(set(body.peak_hours)),
                pay_basis=body.pay_basis, effective_from=day.isoformat())
            if rules:
                old = rules[-1]
                if all(old.get(k) == v for k, v in values.items()):
                    return old
                c.require(day.isoformat() > old['effective_from'], 'Choose a later effective date; a saved pay schedule is immutable.', 409)
                if old['pay_basis'] in {'daily', 'weekly', 'monthly'}:
                    c.require(on_boundary(old, day), 'Start the new rule on a boundary of the existing pay period to avoid partial or overlapping salary.', 409)
            item = dict(values, version=(rules[-1].get('version', 0) + 1) if rules else 1,
                updated_at=c.now(), updated_by=user['email'])
            state.setdefault('driver_pay_rules', {})[driver_id] = dict(item, history=rules + [item])
            c.notify(state, driver_record(state, driver_id)['email'], 'Base pay schedule updated',
                body.pay_basis.replace('_', ' ') + ' from ' + day.isoformat(), portal='driver')
            c.audit(state, user['email'], 'DRIVER_PAY_RULE_UPDATED', driver_id)
            return item

    @app.post('/admin/drivers/{driver_id}/pay-periods', tags=['Driver earnings'])
    def credit_period(driver_id: str, request: Request, body: PeriodCredit):
        with c.transaction() as state:
            user = staff(state, request, True)
            driver_record(state, driver_id)
            c.require(body.confirmed, 'Confirm that accounts has checked this completed contractual pay period.', 422)
            source = 'salary:' + driver_id + ':' + body.period_start.isoformat()
            existing = next((e for e in state.get('driver_earnings', {}).values() if e['source'] == source), None)
            if existing:
                return existing
            period = next((p for p in periods_due(state, driver_id) if p['source'] == source), None)
            c.require(period, 'Choose an uncredited completed period in the saved schedule.', 409)
            item = add_earning(state, driver_id, 'base_pay', period['amount_paise'],
                period['pay_basis'].title() + ' base pay · ' + period['period_start'] + ' to ' + period['period_end'], source, user['email'])
            item.update(period_start=period['period_start'], period_end=period['period_end'], pay_basis=period['pay_basis'], pay_rule_version=period['version'])
            c.notify(state, driver_record(state, driver_id)['email'], 'Base pay credited', item['reference'], portal='driver')
            return item
