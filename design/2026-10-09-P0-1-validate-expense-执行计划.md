# P0-1 validate_expense —— TDD 执行计划

> 日期：2026-10-09 · 分支：`feat/agent-digital-employee`
> 上游：`design/2026-10-09-P0-协议合规-实施方案.md` Phase 3
> 基线：`frd/Agent-Digital-Employee-Requirements-v1.1.md` §5.2（验证服务 MCP）+ §4.3（金额阈值）
> 预计：1 周（1 人）/ 3 天（2 人并行 budget + validate）
> 工程约束：TDD（红 → 绿 → 重构 → 提交），每个 task 闭环

## 文件结构总览

**新增**：
- `backend/src/invoicing/models/budget.py` —— Budget 表
- `backend/src/invoicing/workflow/budget_service.py` —— query_budget / check_available
- `backend/src/invoicing/mcp/validate.py` —— MCP validate_expense 工具（独立模块，避免 tools.py 膨胀）
- `backend/alembic/versions/<rev>_budgets.py` —— 迁移

**修改**：
- `backend/src/invoicing/workflow/services.py` —— 加 `validate_expense` 聚合函数
- `backend/src/invoicing/workflow/expenses.py` —— `submit_claim` 前置 validate
- `backend/src/invoicing/mcp/server.py` —— 注册 `validate_expense` / `query_budget` / `check_budget_available` 工具
- `backend/src/invoicing/mcp/identity.py` —— 加 `budget:read` scope（如未存在）
- `backend/src/invoicing/config.py` —— 加 `large_amount_threshold / over_threshold_tolerance`

**测试**：
- `test/test_budget_model.py`
- `test/test_budget_service.py`
- `test/test_mcp_budget.py`
- `test/test_validation_models.py`
- `test/test_validate_expense.py`
- `test/test_mcp_validate_expense.py`
- `test/test_expense_submit_validate.py`

## 全局约束

- Budget 数值用 `Decimal`，禁止 `float`
- 容差常量 `TAX_SUM_TOLERANCE = Decimal("0.01")` 硬编码（spec §4.1）
- 阈值常量走 `settings`，禁止硬编码在代码中（spec §7.2 ✅5）
- 所有新代码不引新依赖（用现有 pytest + pydantic + SQLAlchemy）
- 所有 MCP 工具走 `@requires("xxx:read")` 装饰器

---

## Task 1：Budget 模型 + 迁移

**Files**：
- Create: `backend/src/invoicing/models/budget.py`
- Create: `backend/alembic/versions/<rev>_budgets.py`（rev 由 alembic 自动生成）
- Test: `test/test_budget_model.py`

**Interfaces**：
- Consumes: 现有 `Base = declarative_base()` 来自 `invoicing.db`
- Produces: `class Budget(Base)` 表 `budgets`，列 `(id, tenant_id, dept, category, period, amount, note, created_at, updated_at)`，唯一约束 `(tenant_id, dept, category, period)`

### Step 1.1 写失败测试

```python
# test/test_budget_model.py
from decimal import Decimal
from datetime import date
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from invoicing.db import Base
from invoicing.models.budget import Budget
from invoicing.config import settings

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()

def test_budget_creation(db):
    b = Budget(tenant_id="default", dept="eng", category="travel",
               period="2026-10", amount=Decimal("50000.00"), note="Q4 budget")
    db.add(b); db.commit()
    assert b.id is not None
    assert b.amount == Decimal("50000.00")
    assert b.created_at is not None

def test_budget_unique_constraint(db):
    b1 = Budget(tenant_id="default", dept="eng", category="travel",
                period="2026-10", amount=Decimal("100"))
    db.add(b1); db.commit()
    b2 = Budget(tenant_id="default", dept="eng", category="travel",
                period="2026-10", amount=Decimal("200"))
    db.add(b2)
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db.commit()
```

Run: `cd backend && uv run pytest ../test/test_budget_model.py -v`
Expected: FAIL（`Budget` 类不存在，ImportError）

### Step 1.2 实现最小代码

```python
# backend/src/invoicing/models/budget.py
"""预算表：部门 × 类别 × 月（v1.1 §9.6.1 多租户 + 业务确认的三维模型）。"""
from decimal import Decimal
from sqlalchemy import Column, Integer, String, Numeric, DateTime, UniqueConstraint, func
from invoicing.db import Base


class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    dept = Column(String(64), nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM
    amount = Column(Numeric(18, 2), nullable=False)
    note = Column(String(256), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "dept", "category", "period", name="uq_budget_dim"),
    )
```

### Step 1.3 迁移生成

Run:
```bash
cd backend && uv run alembic revision --autogenerate -m "budgets 表（部门×类别×月）"
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: 迁移文件生成；upgrade/downgrade 双测无报错。

### Step 1.4 跑测试

Run: `cd backend && uv run pytest ../test/test_budget_model.py -v`
Expected: 2 PASS

### Step 1.5 提交

```bash
git add backend/src/invoicing/models/budget.py backend/alembic/versions/*_budgets.py test/test_budget_model.py
git commit -m "feat(budget): Budget 表 + 迁移（部门×类别×月三维，唯一约束）"
```

---

## Task 2：budget_service.query_budget

**Files**：
- Create: `backend/src/invoicing/workflow/budget_service.py`
- Test: `test/test_budget_service.py`

**Interfaces**：
- Consumes: `Budget` 模型、`ExpenseClaim` 模型（已存在）、`Decimal`
- Produces: `BudgetView` dataclass、`query_budget(db, *, tenant_id, dept, category, period)` 函数

### Step 2.1 写失败测试

```python
# test/test_budget_service.py
from decimal import Decimal
import pytest
from invoicing.db import SessionLocal
from invoicing.models.budget import Budget
from invoicing.workflow.budget_service import query_budget, BudgetView

def test_query_budget_basic(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("50000")))
    db_setup.commit()
    v = query_budget(db_setup, tenant_id="t1", dept="eng",
                     category="travel", period="2026-10")
    assert v.allocated == Decimal("50000")
    assert v.used == Decimal("0")
    assert v.available == Decimal("50000")

def test_query_budget_no_data_returns_zero(db_setup):
    v = query_budget(db_setup, tenant_id="t1", dept="eng",
                     category="travel", period="2026-10")
    assert v.allocated == Decimal("0")
    assert v.used == Decimal("0")
    assert v.available == Decimal("0")

def test_query_budget_aggregates_used_from_approved_claims(db_setup, make_claim):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("50000")))
    make_claim(tenant_id="t1", dept="eng", category="travel",
               period="2026-10", amount=Decimal("3000"), status="approved")
    make_claim(tenant_id="t1", dept="eng", category="travel",
               period="2026-10", amount=Decimal("2000"), status="submitted")
    db_setup.commit()
    v = query_budget(db_setup, tenant_id="t1", dept="eng",
                     category="travel", period="2026-10")
    # 已批准 + 待提交都计入 used（避免双计）
    assert v.used == Decimal("5000")
    assert v.available == Decimal("45000")
```

### Step 2.2 跑测试确认失败

Run: `cd backend && uv run pytest ../test/test_budget_service.py -v`
Expected: FAIL（query_budget 不存在）

### Step 2.3 实现

```python
# backend/src/invoicing/workflow/budget_service.py
"""预算 service：query_budget / check_available（v1.1 §9.6 配套）。"""
from dataclasses import dataclass
from decimal import Decimal
from sqlalchemy import select, func
from invoicing.models.budget import Budget
# ExpenseClaim 已有；status 在枚举里有 approved/submitted/paid 三态都计入 used
from invoicing.models.expense import ExpenseClaim, ExpenseStatus


@dataclass
class BudgetView:
    allocated: Decimal
    used: Decimal
    available: Decimal


def query_budget(db, *, tenant_id: str, dept: str, category: str, period: str) -> BudgetView:
    """查询 (dept, category, period) 预算；used = 该维度下 submitted/approved/paid 的报销单金额合计。"""
    budget = db.execute(
        select(Budget).where(
            Budget.tenant_id == tenant_id,
            Budget.dept == dept,
            Budget.category == category,
            Budget.period == period,
        )
    ).scalar_one_or_none()

    # used 计算：跨周期要按 claim 自身的 occurred_at 解析 period（暂简化：仅按 dept/category 过滤，不强绑 period）
    used = db.execute(
        select(func.coalesce(func.sum(ExpenseClaim.total_amount), 0)).where(
            ExpenseClaim.tenant_id == tenant_id,
            ExpenseClaim.dept == dept,
            ExpenseClaim.expense_type == category,
            ExpenseClaim.status.in_([
                ExpenseStatus.SUBMITTED,
                ExpenseStatus.APPROVED,
                ExpenseStatus.PAID,
            ]),
        )
    ).scalar()

    allocated = budget.amount if budget else Decimal("0")
    used_d = Decimal(used)
    return BudgetView(
        allocated=allocated,
        used=used_d,
        available=allocated - used_d,
    )
```

### Step 2.4 跑测试

Run: `cd backend && uv run pytest ../test/test_budget_service.py -v`
Expected: 3 PASS

### Step 2.5 提交

```bash
git add backend/src/invoicing/workflow/budget_service.py test/test_budget_service.py
git commit -m "feat(budget): query_budget service + BudgetView（聚合已用）"
```

---

## Task 3：budget_service.check_available

**Files**：
- Modify: `backend/src/invoicing/workflow/budget_service.py`
- Modify: `test/test_budget_service.py`

### Step 3.1 写失败测试（追加到 test_budget_service.py）

```python
def test_check_available_within_budget(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("5000")))
    db_setup.commit()
    r = check_available(db_setup, tenant_id="t1", dept="eng",
                        category="travel", period="2026-10", amount=Decimal("3000"))
    assert r.available is True
    assert r.remaining == Decimal("5000")
    assert r.overage == Decimal("0")

def test_check_available_exceeds_budget(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("1000")))
    db_setup.commit()
    r = check_available(db_setup, tenant_id="t1", dept="eng",
                        category="travel", period="2026-10", amount=Decimal("3000"))
    assert r.available is False
    assert r.overage == Decimal("2000")

def test_check_available_exact_boundary(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("3000")))
    db_setup.commit()
    r = check_available(db_setup, tenant_id="t1", dept="eng",
                        category="travel", period="2026-10", amount=Decimal("3000"))
    assert r.available is True
    assert r.overage == Decimal("0")
```

### Step 3.2 跑测试确认失败

Expected: FAIL（check_available 不存在）

### Step 3.3 实现

```python
# budget_service.py 追加
@dataclass
class BudgetCheck:
    available: bool
    remaining: Decimal
    overage: Decimal  # 负数表示未超


def check_available(db, *, tenant_id: str, dept: str, category: str,
                   period: str, amount: Decimal) -> BudgetCheck:
    view = query_budget(db, tenant_id=tenant_id, dept=dept,
                        category=category, period=period)
    remaining = view.available - amount
    return BudgetCheck(
        available=remaining >= 0,
        remaining=view.available,
        overage=-remaining if remaining < 0 else Decimal("0"),
    )
```

### Step 3.4 跑测试

Expected: 6 PASS（前 3 + 新 3）

### Step 3.5 提交

```bash
git add backend/src/invoicing/workflow/budget_service.py test/test_budget_service.py
git commit -m "feat(budget): check_available service + BudgetCheck（含边界）"
```

---

## Task 4：config.py 加阈值常量

**Files**：
- Modify: `backend/src/invoicing/config.py`
- Test: `test/test_config_thresholds.py`（新建）

### Step 4.1 写失败测试

```python
# test/test_config_thresholds.py
from invoicing.config import settings

def test_large_amount_threshold_default():
    assert settings.large_amount_threshold == 5000

def test_over_threshold_tolerance_default():
    assert settings.over_threshold_tolerance == 50

def test_tax_sum_tolerance_in_settings():
    # spec §4.1 硬编码 0.01，但暴露为常量便于 audit
    assert settings.tax_sum_tolerance == 0.01

def test_amount_floor_in_settings():
    # 单笔报销必须 > 0
    assert settings.amount_floor == 0
```

### Step 4.2 跑测试确认失败

Expected: FAIL（settings 没有这些字段）

### Step 4.3 实现

```python
# backend/src/invoicing/config.py 追加
class Settings(BaseSettings):
    # ... 既有字段 ...

    # P0-1 阈值（v1.1 §4.3 金额阈值节点 + §4.1 容差）
    large_amount_threshold: Decimal = Decimal("5000")  # 单笔 ≥ 此值触发人工审批
    over_threshold_tolerance: Decimal = Decimal("50")  # 超标准容忍值
    tax_sum_tolerance: Decimal = Decimal("0.01")  # 价税合计容差
    amount_floor: Decimal = Decimal("0")  # 单笔金额下限（必须 > 此值）

    model_config = SettingsConfigDict(
        env_prefix="INVOICING_",
        env_file=".env",
        # ... 既有配置 ...
    )
```

### Step 4.4 跑测试

Expected: 4 PASS

### Step 4.5 提交

```bash
git add backend/src/invoicing/config.py test/test_config_thresholds.py
git commit -m "feat(config): 阈值常量 large_amount_threshold / over_threshold_tolerance / tax_sum_tolerance"
```

---

## Task 5：ValidationOutcome / ValidationError / ValidationResult 数据类

**Files**：
- Create: `backend/src/invoicing/workflow/validation.py`
- Test: `test/test_validation_models.py`

### Step 5.1 写失败测试

```python
# test/test_validation_models.py
from invoicing.workflow.validation import (
    ValidationOutcome, ValidationError, ValidationResult
)

def test_validation_outcome_enum():
    assert ValidationOutcome.PASS.value == "PASS"
    assert ValidationOutcome.FAIL.value == "FAIL"
    assert ValidationOutcome.NEEDS_REVIEW.value == "NEEDS_REVIEW"

def test_validation_error_dataclass():
    e = ValidationError(code="AMOUNT_TOO_LARGE", message="金额 8000 超阈值",
                        severity="error")
    assert e.code == "AMOUNT_TOO_LARGE"
    assert e.severity == "error"

def test_validation_result_pass():
    r = ValidationResult(outcome=ValidationOutcome.PASS, errors=[], warnings=[])
    assert r.outcome == ValidationOutcome.PASS
    assert r.is_passing() is True

def test_validation_result_fail_when_any_error():
    r = ValidationResult(
        outcome=ValidationOutcome.FAIL,
        errors=[ValidationError(code="X", message="y", severity="error")],
        warnings=[],
    )
    assert r.is_passing() is False

def test_validation_result_needs_review_when_only_warnings():
    r = ValidationResult(
        outcome=ValidationOutcome.NEEDS_REVIEW,
        errors=[],
        warnings=[ValidationError(code="W", message="w", severity="warning")],
    )
    assert r.outcome == ValidationOutcome.NEEDS_REVIEW
```

### Step 5.2 跑测试确认失败

Expected: FAIL（validation 模块不存在）

### Step 5.3 实现

```python
# backend/src/invoicing/workflow/validation.py
"""v1.1 §5.2 验证服务 MCP 出参：3 类结果 + error/warning 二级。"""
from dataclasses import dataclass, field
from enum import Enum


class ValidationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class ValidationError:
    code: str
    message: str
    severity: str  # "error" / "warning"


@dataclass
class ValidationResult:
    outcome: ValidationOutcome
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    def is_passing(self) -> bool:
        return self.outcome in (ValidationOutcome.PASS, ValidationOutcome.NEEDS_REVIEW)
```

### Step 5.4 跑测试

Expected: 5 PASS

### Step 5.5 提交

```bash
git add backend/src/invoicing/workflow/validation.py test/test_validation_models.py
git commit -m "feat(workflow): ValidationOutcome/Error/Result 数据类（v1.1 §5.2 出参）"
```

---

## Task 6：validate_expense check_amount（金额合规）

**Files**：
- Modify: `backend/src/invoicing/workflow/services.py`
- Test: `test/test_validate_expense.py`

### Step 6.1 写失败测试

```python
# test/test_validate_expense.py（仅 check_amount 部分）
from decimal import Decimal
from invoicing.workflow.services import _check_amount
from invoicing.workflow.validation import ValidationError

def test_check_amount_positive_ok():
    errs = _check_amount(Decimal("100"), Decimal("5000"))
    assert errs == []

def test_check_amount_zero_fails():
    errs = _check_amount(Decimal("0"), Decimal("5000"))
    assert len(errs) == 1
    assert errs[0].code == "AMOUNT_NOT_POSITIVE"

def test_check_amount_negative_fails():
    errs = _check_amount(Decimal("-100"), Decimal("5000"))
    assert errs[0].code == "AMOUNT_NOT_POSITIVE"

def test_check_amount_exceeds_threshold_fails():
    errs = _check_amount(Decimal("8000"), Decimal("5000"))
    assert any(e.code == "AMOUNT_TOO_LARGE" for e in errs)

def test_check_amount_at_threshold_needs_review():
    """金额正好等于阈值：spec §4.2 边界处理 → warning 转人工"""
    errs = _check_amount(Decimal("5000"), Decimal("5000"))
    assert any(e.severity == "warning" and e.code == "AMOUNT_AT_THRESHOLD" for e in errs)
```

### Step 6.2 跑测试确认失败

Expected: FAIL（_check_amount 不存在）

### Step 6.3 实现（追加到 services.py）

```python
# backend/src/invoicing/workflow/services.py
from invoicing.workflow.validation import ValidationError, ValidationOutcome
from invoicing.config import settings


def _check_amount(amount: Decimal, threshold: Decimal | None = None) -> list[ValidationError]:
    """金额合规：> 0、≤ 阈值；阈值边界给 warning。"""
    threshold = threshold if threshold is not None else settings.large_amount_threshold
    errs = []
    if amount <= settings.amount_floor:
        errs.append(ValidationError(
            code="AMOUNT_NOT_POSITIVE",
            message=f"金额 {amount} 必须大于 {settings.amount_floor}",
            severity="error",
        ))
        return errs
    if amount > threshold:
        errs.append(ValidationError(
            code="AMOUNT_TOO_LARGE",
            message=f"金额 {amount} 超过大额阈值 {threshold}",
            severity="error",
        ))
    elif amount == threshold:
        errs.append(ValidationError(
            code="AMOUNT_AT_THRESHOLD",
            message=f"金额正好等于大额阈值 {threshold}，需人工复核",
            severity="warning",
        ))
    return errs
```

### Step 6.4 跑测试

Expected: 5 PASS（仅 check_amount 部分；其他测试还会失败但暂不管）

### Step 6.5 提交

```bash
git add backend/src/invoicing/workflow/services.py test/test_validate_expense.py
git commit -m "feat(workflow): _check_amount 金额合规检查（边界给 warning）"
```

---

## Task 7：validate_expense check_tax_sum（价税合计）

**Files**：
- Modify: `backend/src/invoicing/workflow/services.py`
- Modify: `test/test_validate_expense.py`

### Step 7.1 追加失败测试

```python
from invoicing.workflow.services import _check_tax_sum

def test_check_tax_sum_balanced():
    errs = _check_tax_sum(total=Decimal("110"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert errs == []

def test_check_tax_sum_within_tolerance():
    errs = _check_tax_sum(total=Decimal("110.005"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert errs == []  # 0.005 < 0.01 容差

def test_check_tax_sum_outside_tolerance():
    errs = _check_tax_sum(total=Decimal("110.50"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert any(e.code == "TAX_SUM_MISMATCH" for e in errs)

def test_check_tax_sum_exact_match():
    errs = _check_tax_sum(total=Decimal("0"), without_tax=Decimal("0"), tax=Decimal("0"))
    assert errs == []
```

### Step 7.2 跑测试确认失败

Expected: FAIL

### Step 7.3 实现

```python
def _check_tax_sum(*, total: Decimal, without_tax: Decimal, tax: Decimal) -> list[ValidationError]:
    """价税合计勾稽：total = without_tax + tax；容差 0.01（v1.1 §4.1）。"""
    diff = abs(total - (without_tax + tax))
    if diff > settings.tax_sum_tolerance:
        return [ValidationError(
            code="TAX_SUM_MISMATCH",
            message=f"价税合计 {total} ≠ 不含税 {without_tax} + 税额 {tax}，差 {diff}",
            severity="error",
        )]
    return []
```

### Step 7.4 跑测试

Expected: 4 PASS（tax_sum 部分）

### Step 7.5 提交

```bash
git add backend/src/invoicing/workflow/services.py test/test_validate_expense.py
git commit -m "feat(workflow): _check_tax_sum 价税合计勾稽（容差 settings）"
```

---

## Task 8：validate_expense check_cross_duplicate（跨票号重复）

**Files**：
- Modify: `backend/src/invoicing/workflow/services.py`
- Modify: `test/test_validate_expense.py`

### Step 8.1 追加失败测试

```python
from invoicing.workflow.services import _check_cross_duplicate
from invoicing.models.expense import ExpenseClaim, ExpenseEntry, ExpenseItem

def test_check_cross_duplicate_no_duplicates(db_setup, make_claim_with_items):
    claim = make_claim_with_items(invoice_numbers=["INV001", "INV002"])
    errs = _check_cross_duplicate(db_setup, claim)
    assert errs == []

def test_check_cross_duplicate_duplicate_invoice_numbers(db_setup, make_claim_with_items):
    claim = make_claim_with_items(invoice_numbers=["INV001", "INV001"])
    errs = _check_cross_duplicate(db_setup, claim)
    assert any(e.code == "DUPLICATE_INVOICE_NUMBER" for e in errs)
```

### Step 8.2 跑测试确认失败

Expected: FAIL

### Step 8.3 实现

```python
from collections import Counter
from invoicing.models.expense import ExpenseItem

def _check_cross_duplicate(db, claim: ExpenseClaim) -> list[ValidationError]:
    """同报销单内发票号查重（v1.1 §4.1 跨票号）。"""
    nums = [
        item.invoice.invoice_number
        for entry in claim.entries
        for item in entry.items
        if item.invoice_id is not None and item.invoice is not None
        and item.invoice.invoice_number
    ]
    counts = Counter(nums)
    dups = [n for n, c in counts.items() if c > 1]
    if not dups:
        return []
    return [ValidationError(
        code="DUPLICATE_INVOICE_NUMBER",
        message=f"同报销单内发票号重复：{dups}",
        severity="error",
    )]
```

### Step 8.4 跑测试

Expected: 2 PASS

### Step 8.5 提交

```bash
git add backend/src/invoicing/workflow/services.py test/test_validate_expense.py
git commit -m "feat(workflow): _check_cross_duplicate 同报销单跨票号查重"
```

---

## Task 9：validate_expense check_vouchers（凭证齐全）

**Files**：
- Modify: `backend/src/invoicing/workflow/services.py`
- Modify: `test/test_validate_expense.py`

### Step 9.1 追加失败测试

```python
from invoicing.workflow.services import _check_vouchers

def test_check_vouchers_all_present(make_claim_with_items):
    claim = make_claim_with_items(entry_voucher_counts=[2, 3])
    errs = _check_vouchers(claim)
    assert errs == []

def test_check_vouchers_missing(make_claim_with_items):
    claim = make_claim_with_items(entry_voucher_counts=[0])
    errs = _check_vouchers(claim)
    assert any(e.code == "MISSING_VOUCHER" for e in errs)
```

### Step 9.2 跑测试确认失败

Expected: FAIL

### Step 9.3 实现

```python
def _check_vouchers(claim: ExpenseClaim) -> list[ValidationError]:
    """每个 entry 至少 1 张凭证（v1.1 §4.4 凭证齐全）。"""
    errs = []
    for e in claim.entries:
        count = sum(len(item.vouchers) for item in e.items)
        if count == 0:
            errs.append(ValidationError(
                code="MISSING_VOUCHER",
                message=f"事项 {e.title or e.id} 缺少凭证",
                severity="error",
            ))
    return errs
```

### Step 9.4 跑测试

Expected: 2 PASS

### Step 9.5 提交

```bash
git add backend/src/invoicing/workflow/services.py test/test_validate_expense.py
git commit -m "feat(workflow): _check_vouchers 凭证齐全检查"
```

---

## Task 10：validate_expense check_budget（预算余额）

**Files**：
- Modify: `backend/src/invoicing/workflow/services.py`
- Modify: `test/test_validate_expense.py`

### Step 10.1 追加失败测试

```python
from invoicing.workflow.services import _check_budget
from invoicing.models.budget import Budget
from decimal import Decimal

def test_check_budget_within(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("5000")))
    db_setup.commit()
    errs = _check_budget(db_setup, tenant_id="t1", dept="eng",
                         category="travel", period="2026-10", amount=Decimal("3000"))
    assert errs == []

def test_check_budget_over(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("1000")))
    db_setup.commit()
    errs = _check_budget(db_setup, tenant_id="t1", dept="eng",
                         category="travel", period="2026-10", amount=Decimal("3000"))
    assert any(e.code == "OVER_BUDGET" for e in errs)

def test_check_budget_no_budget_configured_warning(db_setup):
    errs = _check_budget(db_setup, tenant_id="t1", dept="eng",
                         category="travel", period="2026-10", amount=Decimal("100"))
    # 无预算配置 → warning 而非 error（spec §4.2 边界）
    assert any(e.severity == "warning" and e.code == "NO_BUDGET_CONFIGURED" for e in errs)
```

### Step 10.2 跑测试确认失败

Expected: FAIL

### Step 10.3 实现

```python
from invoicing.workflow.budget_service import check_available

def _check_budget(db, *, tenant_id: str, dept: str, category: str,
                  period: str, amount: Decimal) -> list[ValidationError]:
    """预算余额检查：超额 FAIL；无预算配置 warning（允许走但要审计）。"""
    view = query_budget(db, tenant_id=tenant_id, dept=dept,
                        category=category, period=period)
    if view.allocated == 0:
        return [ValidationError(
            code="NO_BUDGET_CONFIGURED",
            message=f"未配置 (dept={dept}, category={category}, period={period}) 预算，建议复核",
            severity="warning",
        )]
    check = check_available(db, tenant_id=tenant_id, dept=dept,
                            category=category, period=period, amount=amount)
    if not check.available:
        return [ValidationError(
            code="OVER_BUDGET",
            message=f"金额 {amount} 超预算剩余 {check.remaining}，超出 {check.overage}",
            severity="error",
        )]
    return []
```

### Step 10.4 跑测试

Expected: 3 PASS

### Step 10.5 提交

```bash
git add backend/src/invoicing/workflow/services.py test/test_validate_expense.py
git commit -m "feat(workflow): _check_budget 预算余额检查（超额 FAIL，无预算 warning）"
```

---

## Task 11：validate_expense 聚合函数

**Files**：
- Modify: `backend/src/invoicing/workflow/services.py`
- Modify: `test/test_validate_expense.py`

### Step 11.1 追加失败测试

```python
from invoicing.workflow.services import validate_expense

def test_validate_expense_all_pass(db_setup, make_claim_with_items):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("10000")))
    claim = make_claim_with_items(
        total=Decimal("110"), without_tax=Decimal("100"), tax=Decimal("10"),
        invoice_numbers=["INV001"], amount=Decimal("110"),
    )
    db_setup.commit()
    r = validate_expense(db_setup, claim)
    assert r.outcome == ValidationOutcome.PASS

def test_validate_expense_single_fail_blocks(db_setup, make_claim_with_items):
    # 金额超阈值
    claim = make_claim_with_items(
        total=Decimal("8000"), without_tax=Decimal("7272.73"), tax=Decimal("727.27"),
        invoice_numbers=["INV001"], amount=Decimal("8000"),
    )
    db_setup.commit()
    r = validate_expense(db_setup, claim)
    assert r.outcome == ValidationOutcome.FAIL
    assert any(e.code == "AMOUNT_TOO_LARGE" for e in r.errors)

def test_validate_expense_warning_only_needs_review(db_setup, make_claim_with_items):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("10000")))
    # 金额正好等于大额阈值 5000
    claim = make_claim_with_items(
        total=Decimal("5000"), without_tax=Decimal("4545.45"), tax=Decimal("454.55"),
        invoice_numbers=["INV001"], amount=Decimal("5000"),
    )
    db_setup.commit()
    r = validate_expense(db_setup, claim)
    assert r.outcome == ValidationOutcome.NEEDS_REVIEW

def test_validate_expense_multiple_errors_aggregated(db_setup, make_claim_with_items):
    # 价税不平 + 凭证缺失
    claim = make_claim_with_items(
        total=Decimal("110"), without_tax=Decimal("100"), tax=Decimal("20"),
        invoice_numbers=["INV001"], amount=Decimal("110"),
        entry_voucher_counts=[0],
    )
    db_setup.commit()
    r = validate_expense(db_setup, claim)
    assert r.outcome == ValidationOutcome.FAIL
    codes = {e.code for e in r.errors}
    assert "TAX_SUM_MISMATCH" in codes
    assert "MISSING_VOUCHER" in codes
```

### Step 11.2 跑测试确认失败

Expected: FAIL（validate_expense 不存在）

### Step 11.3 实现

```python
def validate_expense(db, claim: ExpenseClaim) -> ValidationResult:
    """v1.1 §5.2 验证服务 MCP 出参；聚合 6 项检查（spec §4.4 自动化决策清单）。

    规则（v1.1 §2.x）：
    - 任一 error → outcome=FAIL
    - 仅 warning → outcome=NEEDS_REVIEW
    - 全通过 → outcome=PASS
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    # 1. Schema 校验（pydantic；调用方已 validate，这里再过一遍兜底）
    try:
        from invoicing.schemas.expense import ExpenseClaimOut
        ExpenseClaimOut.model_validate(claim, from_attributes=True)
    except Exception as e:
        errors.append(ValidationError(
            code="SCHEMA_INVALID", message=str(e), severity="error"
        ))

    # 2. 金额合规
    for e in _check_amount(claim.total_amount):
        (errors if e.severity == "error" else warnings).append(e)

    # 3. 价税合计
    for e in _check_tax_sum(total=claim.total_amount,
                             without_tax=claim.amount_without_tax,
                             tax=claim.tax_amount):
        (errors if e.severity == "error" else warnings).append(e)

    # 4. 跨票号重复
    errors.extend(_check_cross_duplicate(db, claim))

    # 5. 凭证齐全
    errors.extend(_check_vouchers(claim))

    # 6. 预算余额（按 claim 的 dept/category/period）
    period = claim.occurred_at.strftime("%Y-%m") if claim.occurred_at else None
    if period and claim.dept and claim.expense_type:
        for e in _check_budget(db, tenant_id=claim.tenant_id,
                               dept=claim.dept, category=claim.expense_type,
                               period=period, amount=claim.total_amount):
            (errors if e.severity == "error" else warnings).append(e)

    if errors:
        outcome = ValidationOutcome.FAIL
    elif warnings:
        outcome = ValidationOutcome.NEEDS_REVIEW
    else:
        outcome = ValidationOutcome.PASS
    return ValidationResult(outcome=outcome, errors=errors, warnings=warnings)
```

### Step 11.4 跑测试

Expected: 4 PASS

### Step 11.5 提交

```bash
git add backend/src/invoicing/workflow/services.py test/test_validate_expense.py
git commit -m "feat(workflow): validate_expense 聚合 6 项检查（PASS/FAIL/NEEDS_REVIEW）"
```

---

## Task 12：MCP validate_expense 工具

**Files**：
- Create: `backend/src/invoicing/mcp/validate.py`
- Modify: `backend/src/invoicing/mcp/server.py`
- Test: `test/test_mcp_validate_expense.py`

### Step 12.1 写失败测试

```python
# test/test_mcp_validate_expense.py
from decimal import Decimal
import pytest
from invoicing.mcp.validate import validate_expense_mcp
from invoicing.workflow.validation import ValidationOutcome

def test_validate_expense_mcp_returns_pass(db_setup, make_claim_with_items):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("10000")))
    claim = make_claim_with_items(amount=Decimal("110"), total=Decimal("110"),
                                  without_tax=Decimal("100"), tax=Decimal("10"),
                                  invoice_numbers=["INV001"])
    db_setup.commit()
    r = validate_expense_mcp(db_setup, claim.id)
    assert r["outcome"] == "PASS"

def test_validate_expense_mcp_returns_fail(db_setup, make_claim_with_items):
    claim = make_claim_with_items(amount=Decimal("8000"), total=Decimal("8000"),
                                  without_tax=Decimal("7272.73"), tax=Decimal("727.27"),
                                  invoice_numbers=["INV001"])
    db_setup.commit()
    r = validate_expense_mcp(db_setup, claim.id)
    assert r["outcome"] == "FAIL"
    assert any(e["code"] == "AMOUNT_TOO_LARGE" for e in r["errors"])

def test_validate_expense_mcp_claim_not_found(db_setup):
    with pytest.raises(ValueError, match="不存在或无权访问"):
        validate_expense_mcp(db_setup, 99999)
```

### Step 12.2 跑测试确认失败

Expected: FAIL（validate_expense_mcp 不存在）

### Step 12.3 实现

```python
# backend/src/invoicing/mcp/validate.py
"""MCP 验证服务：报销单级校验工具（v1.1 §5.2 验证服务MCP）。"""
from invoicing.db import SessionLocal
from invoicing.workflow import services
from invoicing.workflow.validation import ValidationError, ValidationOutcome
from invoicing.models.expense import ExpenseClaim
from invoicing.mcp.identity import requires


@requires("expense:read")
def validate_expense_mcp(claim_id: int) -> dict:
    """报销单级校验聚合：6 项检查（v1.1 §4.4 + §5.2）。

    返回结构：
    {
        "outcome": "PASS" | "FAIL" | "NEEDS_REVIEW",
        "errors": [{"code": str, "message": str, "severity": "error"}],
        "warnings": [{"code": str, "message": str, "severity": "warning"}]
    }
    """
    with SessionLocal() as db:
        claim = db.get(ExpenseClaim, claim_id)
        if claim is None:
            raise ValueError(f"报销单不存在或无权访问: {claim_id}")
        result = services.validate_expense(db, claim)
        return {
            "outcome": result.outcome.value,
            "errors": [{"code": e.code, "message": e.message, "severity": e.severity}
                      for e in result.errors],
            "warnings": [{"code": e.code, "message": e.message, "severity": e.severity}
                        for e in result.warnings],
        }
```

### Step 12.4 注册到 server.py

修改 `backend/src/invoicing/mcp/server.py`：

```python
# 在 build_server() 内追加
from invoicing.mcp import validate as mcp_validate

@server.tool(
    description="报销单级校验：聚合 6 项检查（金额合规/价税合计/跨票号重复/预算/凭证/Schema），返回 PASS/FAIL/NEEDS_REVIEW。",
)
def validate_expense(claim_id: int) -> dict:
    return mcp_validate.validate_expense_mcp(claim_id)
```

### Step 12.5 跑测试

Expected: 3 PASS

### Step 12.6 提交

```bash
git add backend/src/invoicing/mcp/validate.py backend/src/invoicing/mcp/server.py test/test_mcp_validate_expense.py
git commit -m "feat(mcp): validate_expense 工具（v1.1 §5.2 验证服务MCP）"
```

---

## Task 13：MCP budget 工具

**Files**：
- Modify: `backend/src/invoicing/mcp/server.py`
- Test: `test/test_mcp_budget.py`

### Step 13.1 写失败测试

```python
# test/test_mcp_budget.py
from decimal import Decimal
from invoicing.models.budget import Budget
from invoicing.mcp.server import build_server  # 不直接调 tool，调 server 实例

def test_query_budget_mcp_returns_view(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("5000")))
    db_setup.commit()
    from invoicing.mcp.tools import query_budget_mcp  # 后续实现
    v = query_budget_mcp(dept="eng", category="travel", period="2026-10")
    assert v["allocated"] == "5000"
    assert v["available"] == "5000"

def test_check_budget_available_mcp_within(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("5000")))
    db_setup.commit()
    from invoicing.mcp.tools import check_budget_available_mcp
    r = check_budget_available_mcp(dept="eng", category="travel",
                                    amount=Decimal("3000"), period="2026-10")
    assert r["available"] is True

def test_check_budget_available_mcp_over(db_setup):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("1000")))
    db_setup.commit()
    from invoicing.mcp.tools import check_budget_available_mcp
    r = check_budget_available_mcp(dept="eng", category="travel",
                                    amount=Decimal("3000"), period="2026-10")
    assert r["available"] is False
    assert Decimal(r["overage"]) == Decimal("2000")
```

### Step 13.2 跑测试确认失败

Expected: FAIL

### Step 13.3 实现（在 tools.py 末尾追加）

```python
# backend/src/invoicing/mcp/tools.py 追加
from invoicing.workflow import budget_service

@requires("budget:read")
def query_budget_mcp(dept: str, category: str, period: str | None = None) -> dict:
    """查询预算（v1.1 §5.2 预算服务MCP）。"""
    with SessionLocal() as db:
        v = budget_service.query_budget(
            db, tenant_id=settings.default_tenant_id,
            dept=dept, category=category, period=period or _current_period(),
        )
    return {"allocated": str(v.allocated), "used": str(v.used), "available": str(v.available)}


@requires("budget:read")
def check_budget_available_mcp(dept: str, category: str, amount: Decimal,
                                period: str | None = None) -> dict:
    """检查预算是否可承担（v1.1 §5.2）。"""
    with SessionLocal() as db:
        r = budget_service.check_available(
            db, tenant_id=settings.default_tenant_id,
            dept=dept, category=category, period=period or _current_period(),
            amount=Decimal(str(amount)),
        )
    return {
        "available": r.available,
        "remaining": str(r.remaining),
        "overage": str(r.overage),
    }


def _current_period() -> str:
    from datetime import date
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"
```

### Step 13.4 注册到 server.py

```python
# server.py build_server 内
from invoicing.mcp import tools as mcp_tools

@server.tool(description="查询部门/类别/期间预算。")
def query_budget(dept: str, category: str, period: str | None = None) -> dict:
    return mcp_tools.query_budget_mcp(dept=dept, category=category, period=period)

@server.tool(description="检查预算是否可承担金额。")
def check_budget_available(dept: str, category: str, amount: Decimal,
                            period: str | None = None) -> dict:
    return mcp_tools.check_budget_available_mcp(dept=dept, category=category,
                                                 amount=amount, period=period)
```

### Step 13.5 检查 identity.py 是否有 budget:read scope

```bash
grep -n "budget:read" backend/src/invoicing/mcp/identity.py
```

如果没有，加：
```python
# identity.py scopes 列表追加
"budget:read",
```

### Step 13.6 跑测试

Expected: 3 PASS

### Step 13.7 提交

```bash
git add backend/src/invoicing/mcp/tools.py backend/src/invoicing/mcp/server.py backend/src/invoicing/mcp/identity.py test/test_mcp_budget.py
git commit -m "feat(mcp): query_budget + check_budget_available 工具（v1.1 §5.2 预算服务MCP）"
```

---

## Task 14：expense_submit 前置 validate

**Files**：
- Modify: `backend/src/invoicing/workflow/expenses.py`
- Modify: `test/test_expense_submit_validate.py`

### Step 14.1 写失败测试

```python
# test/test_expense_submit_validate.py
from decimal import Decimal
import pytest
from invoicing.db import SessionLocal
from invoicing.models.budget import Budget
from invoicing.workflow import expenses as svc

def test_submit_passes_validate(db_setup, make_claim_with_items, finance_user):
    db_setup.add(Budget(tenant_id="t1", dept="eng", category="travel",
                        period="2026-10", amount=Decimal("10000")))
    claim = make_claim_with_items(amount=Decimal("110"), total=Decimal("110"),
                                  without_tax=Decimal("100"), tax=Decimal("10"),
                                  invoice_numbers=["INV001"])
    db_setup.commit()
    # submit 应该成功
    result = svc.submit_claim(db_setup, finance_user, claim.id)
    assert result.status.value == "submitted"

def test_submit_blocked_by_validate_fail(db_setup, make_claim_with_items, finance_user):
    # 不配预算 + 金额超大 → 触发 FAIL
    claim = make_claim_with_items(amount=Decimal("8000"), total=Decimal("8000"),
                                  without_tax=Decimal("7272.73"), tax=Decimal("727.27"),
                                  invoice_numbers=["INV001"])
    db_setup.commit()
    with pytest.raises(ValueError, match="validate_expense FAIL"):
        svc.submit_claim(db_setup, finance_user, claim.id)

def test_submit_audit_blocked(db_setup, make_claim_with_items, finance_user):
    """FAIL 提交必须写审计（v1.1 §9.5.3 outcome=blocked）。"""
    claim = make_claim_with_items(amount=Decimal("8000"), total=Decimal("8000"),
                                  without_tax=Decimal("7272.73"), tax=Decimal("727.27"),
                                  invoice_numbers=["INV001"])
    db_setup.commit()
    from invoicing.models.audit import AuditLog
    with pytest.raises(ValueError):
        svc.submit_claim(db_setup, finance_user, claim.id)
    # 找最新的 audit log
    log = db_setup.query(AuditLog).filter(
        AuditLog.action == "SUBMIT_BLOCKED"
    ).order_by(AuditLog.created_at.desc()).first()
    assert log is not None
    assert log.outcome == "blocked"
    assert "AMOUNT_TOO_LARGE" in str(log.detail)
```

### Step 14.2 跑测试确认失败

Expected: FAIL（submit_claim 没接 validate）

### Step 14.3 实现

```python
# backend/src/invoicing/workflow/expenses.py 的 submit_claim 函数最前面追加

def submit_claim(db, user, claim_id, *, channel: str = "web"):
    """v1.1 §5.2 + §4.4：提交前先 validate_expense；FAIL 抛错 + 写 SUBMIT_BLOCKED 审计。"""
    claim = db.get(ExpenseClaim, claim_id)
    if claim is None:
        raise ValueError(f"报销单不存在: {claim_id}")

    # 前置 validate（v1.1 §5.2 验证服务 MCP 必走）
    result = services.validate_expense(db, claim)
    if result.outcome == ValidationOutcome.FAIL:
        write_audit(
            db, action="SUBMIT_BLOCKED", actor_type="user", actor_id=user.id,
            channel=channel, target_type="expense_claim", target_id=claim.id,
            outcome="blocked",
            detail={
                "errors": [{"code": e.code, "message": e.message} for e in result.errors],
            },
        )
        db.commit()
        raise ValueError(
            f"validate_expense FAIL: {[e.code for e in result.errors]}"
        )

    # 原有 submit 逻辑（status 推进 + 审计）
    ...
```

### Step 14.4 跑测试

Expected: 3 PASS

### Step 14.5 提交

```bash
git add backend/src/invoicing/workflow/expenses.py test/test_expense_submit_validate.py
git commit -m "fix(workflow): expense_submit 前置 validate_expense，FAIL 抛错 + SUBMIT_BLOCKED 审计"
```

---

## Task 15：端到端集成测试

**Files**：
- Create: `test/test_p0_1_e2e.py`

### Step 15.1 写测试

```python
# test/test_p0_1_e2e.py
"""P0-1 端到端：一张报销从 validate 到 submit 完整路径。"""
from decimal import Decimal
from invoicing.db import SessionLocal
from invoicing.models.budget import Budget
from invoicing.workflow import services, expenses as svc
from invoicing.workflow.validation import ValidationOutcome


def test_e2e_full_pass(make_claim_with_items, finance_user):
    with SessionLocal() as db:
        db.add(Budget(tenant_id="t1", dept="eng", category="travel",
                      period="2026-10", amount=Decimal("10000")))
        claim = make_claim_with_items(
            total=Decimal("110"), without_tax=Decimal("100"), tax=Decimal("10"),
            invoice_numbers=["INV001"], amount=Decimal("110"),
        )
        db.commit()

        # 1) 直接调 validate
        r = services.validate_expense(db, claim)
        assert r.outcome == ValidationOutcome.PASS

        # 2) submit 走通
        result = svc.submit_claim(db, finance_user, claim.id)
        assert result.status.value == "submitted"


def test_e2e_blocked_with_audit(make_claim_with_items, finance_user):
    with SessionLocal() as db:
        # 不配预算 + 超大额 → AMOUNT_TOO_LARGE FAIL
        claim = make_claim_with_items(
            total=Decimal("8000"), without_tax=Decimal("7272.73"), tax=Decimal("727.27"),
            invoice_numbers=["INV001"], amount=Decimal("8000"),
        )
        db.commit()

        r = services.validate_expense(db, claim)
        assert r.outcome == ValidationOutcome.FAIL

        import pytest
        with pytest.raises(ValueError):
            svc.submit_claim(db, finance_user, claim.id)

        # 审计
        from invoicing.models.audit import AuditLog
        log = db.query(AuditLog).filter(
            AuditLog.action == "SUBMIT_BLOCKED",
            AuditLog.target_id == claim.id,
        ).first()
        assert log is not None
        assert log.outcome == "blocked"
```

### Step 15.2 跑测试

Run: `cd backend && uv run pytest ../test/test_p0_1_e2e.py -v`
Expected: 2 PASS

### Step 15.3 跑全套测试确认无回归

Run: `cd backend && uv run pytest ../test -v`
Expected: 全部通过；新测试 ~35 例 + 既有测试无回归

### Step 15.4 提交

```bash
git add test/test_p0_1_e2e.py
git commit -m "test: P0-1 validate_expense 端到端集成（PASS 路径 + FAIL 阻断 + 审计）"
```

---

## Review Focus（按 writing-plans 自检清单）

按 spec v1.1 自审：

| spec 条款 | 任务 | 测试覆盖 |
|---|---|---|
| §5.2 验证服务MCP `validate_expense` | Task 12 | ✅ 3 例 |
| §5.2 预算服务MCP `query_budget` | Task 13 | ✅ 1 例 |
| §5.2 预算服务MCP `check_budget_available` | Task 13 | ✅ 2 例 |
| §4.1 完全固化（价税合计/查重/状态机） | Task 7、8、14 | ✅ 各覆盖 |
| §4.2 条件固化（金额阈值边界） | Task 6 | ✅ 边界 warning |
| §4.3 金额阈值节点 | Task 6、Task 14 | ✅ |
| §7.2 ✅1 所有 AI 输出过 schema | Task 11 | ✅（pydantic 兜底） |
| §7.2 ✅3 决策记录审计 | Task 14 | ✅ SUBMIT_BLOCKED 审计 |
| §7.2 ✅5 阈值可配置 | Task 4 | ✅ settings 暴露 |

**Review Focus（可能漏掉的输入）**：
1. **跨 tenant_id 的 budget 查询**：当前 plan 假设 `settings.default_tenant_id`，未做跨租户隔离校验 → P0-5 之后补
2. **claim 跨周期（occurred_at 跨月）**：当前按 claim.occurred_at 解析 period，但若 claim 没设 occurred_at，budget check 跳过 → spec §4.2 应给 warning（待 P0-5 加 audit 时一起补）
3. **历史数据迁移**：现有报销单没 dept/expense_type 字段 → validate 失败 → 应在 Task 14 前加一次性数据 backfill（脚本）

---

## 执行时间预算

| Task | 预估 | 累计 |
|---|---|---|
| 1 Budget 模型 | 0.5 天 | 0.5 |
| 2 query_budget | 0.5 天 | 1.0 |
| 3 check_available | 0.5 天 | 1.5 |
| 4 config 阈值 | 0.5 天 | 2.0 |
| 5 Validation 模型 | 0.5 天 | 2.5 |
| 6 check_amount | 0.5 天 | 3.0 |
| 7 check_tax_sum | 0.5 天 | 3.5 |
| 8 check_cross_duplicate | 0.5 天 | 4.0 |
| 9 check_vouchers | 0.5 天 | 4.5 |
| 10 check_budget | 0.5 天 | 5.0 |
| 11 validate_expense 聚合 | 1 天 | 6.0 |
| 12 MCP validate_expense | 0.5 天 | 6.5 |
| 13 MCP budget 工具 | 0.5 天 | 7.0 |
| 14 expense_submit 集成 | 1 天 | 8.0 |
| 15 端到端测试 | 0.5 天 | 8.5 |

**合计**：约 9 个工作日（1 人）或 5 天（2 人并行：一人 budget/validate + 一人 MCP/集成）。

---

## 下一步

执行方式：
- **Subagent-driven**：每个 Task 派一个 subagent 实施 + 单独 reviewer 验证。最稳。
- **Native**：本会话直接做，按 Task 顺序。快但没有逐 Task 独立 review。

按 writing-plans skill，**等你拍板执行方式**。我的建议：**Native**（你已经做完 P0-5/3 的范围调整，整体规模可控；subagent 适合 4+ 小时的 task，这个 plan 中最长 task 也就 1 天）。