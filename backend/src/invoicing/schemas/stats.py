from pydantic import BaseModel


class StatsOverviewOut(BaseModel):
    pending_review: int
    pending_submit: int
    today_new: int
    month_total: int
