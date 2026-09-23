"""只读报表服务：试算平衡表。

数据来源是过账时在同一事务内维护的物化余额表 ``balances`` (按
科目 × 期间记录借贷累计)，因此试算平衡只需把截止期间及此前各期间的
余额行按科目汇总，不触碰凭证/分录，也不写入任何数据。

复式记账下每张凭证借贷相等，所以同一币种下：

    Σ 各科目累计借方发生额 == Σ 各科目累计贷方发生额
    Σ 各科目期末借方余额   == Σ 各科目期末贷方余额

后者作为 ``balanced`` 直接返回，可用于发现数据损坏。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import unprocessable
from ..models import Account, Balance
from ..money import minor_to_amount
from ..schemas import TrialBalanceGroup, TrialBalanceLine, TrialBalanceOut
from .master_data import PERIOD_KEY_RE


def trial_balance(db: Session, period: str) -> TrialBalanceOut:
    """返回截止 ``period`` (含，格式 YYYY-MM) 的试算平衡表，按币种分组。"""
    if not PERIOD_KEY_RE.match(period):
        raise unprocessable(f"非法期间 {period!r}，格式应为 YYYY-MM", period_key=period)

    # 汇总该期间及此前各期间的物化余额；INNER JOIN 自动排除无余额科目
    rows = db.execute(
        select(
            Account,
            Balance.debit_minor,
            Balance.credit_minor,
        )
        .join(Balance, Balance.account_id == Account.id)
        .where(Balance.period_key <= period)
        .order_by(Account.currency, Account.code, Account.id)
    ).all()

    # 同一科目可能跨多个期间有多行余额，先按科目累加 (整数分)
    accum: dict[int, dict] = {}
    for account, debit_minor, credit_minor in rows:
        slot = accum.setdefault(account.id, {
            "account": account, "debit": 0, "credit": 0,
        })
        slot["debit"] += int(debit_minor)
        slot["credit"] += int(credit_minor)

    # 按币种分组；无期末余额 (借/贷净额为 0) 的科目不列出
    grouped: dict[str, list[tuple[TrialBalanceLine, int]]] = {}
    for slot in accum.values():
        account: Account = slot["account"]
        debit, credit = slot["debit"], slot["credit"]
        if debit == credit:
            continue
        if debit > credit:
            direction, ending = "debit", debit - credit
        else:
            direction, ending = "credit", credit - debit
        line = TrialBalanceLine(
            account_id=account.id,
            account_code=account.code,
            account_name=account.name,
            account_type=account.type,
            total_debit=minor_to_amount(debit),
            total_credit=minor_to_amount(credit),
            balance_direction=direction,
            ending_balance=minor_to_amount(ending),
        )
        grouped.setdefault(account.currency, []).append((line, ending))

    groups: list[TrialBalanceGroup] = []
    for currency in sorted(grouped):
        entries = grouped[currency]
        total_debit = sum(
            ending for line, ending in entries if line.balance_direction == "debit"
        )
        total_credit = sum(
            ending for line, ending in entries if line.balance_direction == "credit"
        )
        groups.append(TrialBalanceGroup(
            currency=currency,
            lines=[line for line, _ in entries],
            total_debit=minor_to_amount(total_debit),
            total_credit=minor_to_amount(total_credit),
            balanced=total_debit == total_credit,
        ))

    return TrialBalanceOut(
        period=period,
        generated_at=datetime.now(timezone.utc),
        groups=groups,
    )
