"""费用标准查询（v1.1 §4.2/§4.3）。

只读服务：`get_policy` 按 (租户, 类别, 标准项, 城市档) 取标准；
未命中具体城市档时回落 "default"——没配 tier9 不等于没有标准。
"""
from sqlalchemy import select

from invoicing.models.expense_policy import ExpensePolicy


def get_policy(db, *, tenant_id: str, category: str, item_key: str,
               city_tier: str | None = None) -> ExpensePolicy | None:
    """取启用中的标准；精确档优先，其次 default，都没有则 None。"""
    wanted = {city_tier, "default"} if city_tier else {"default"}
    rows = db.execute(
        select(ExpensePolicy).where(
            ExpensePolicy.tenant_id == tenant_id,
            ExpensePolicy.category == category,
            ExpensePolicy.item_key == item_key,
            ExpensePolicy.city_tier.in_(wanted),
            ExpensePolicy.enabled.is_(True),
        )
    ).scalars().all()
    if not rows:
        return None
    if city_tier:
        for r in rows:
            if r.city_tier == city_tier:
                return r
    return next((r for r in rows if r.city_tier == "default"), None)


def list_policies(db, *, tenant_id: str, category: str | None = None) -> list[ExpensePolicy]:
    q = select(ExpensePolicy).where(ExpensePolicy.tenant_id == tenant_id)
    if category:
        q = q.where(ExpensePolicy.category == category)
    return list(db.execute(
        q.order_by(ExpensePolicy.category, ExpensePolicy.item_key,
                   ExpensePolicy.city_tier)
    ).scalars().all())