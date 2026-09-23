"""只读报表路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_session
from ..schemas import TrialBalanceOut
from ..services import report as report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/trial-balance",
    response_model=TrialBalanceOut,
    summary="试算平衡表 (只读)：截止期间按币种汇总累计发生额与期末余额",
)
def get_trial_balance(
    period: str,
    db: Session = Depends(get_session),
) -> TrialBalanceOut:
    """截止 ``period`` (YYYY-MM，含当月及此前各期间) 的试算平衡表。

    纯查询：只读余额物化表，不创建期间、不改动任何账务数据。
    无期末余额的科目不列出；结果按币种分组，逐组给出期末借贷合计
    与是否平衡。
    """
    return report_service.trial_balance(db, period)
