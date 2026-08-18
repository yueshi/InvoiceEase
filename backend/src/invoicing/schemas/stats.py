from pydantic import BaseModel


class StatsOverviewOut(BaseModel):
    pending_review: int
    pending_submit: int
    today_new: int
    month_total: int


class TrustStatsOut(BaseModel):
    """数字员工信任仪表盘（M8）：观察期改判数据。"""

    days: int
    auto_count: int
    manual_count: int
    overturn_count: int
    overturn_rate: float
