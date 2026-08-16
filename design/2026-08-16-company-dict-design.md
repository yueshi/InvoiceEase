# 常用税号及公司信息功能设计（V0.1 草案，待评审）

| 项目 | 内容 |
|------|------|
| 文档版本 | V0.1 |
| 编制日期 | 2026-08-16 |
| 状态 | 待评审 |
| 需求 | 增加「设置常用税号及公司信息」工具及接口；Web UI 同步到系统设置 |
| 用途（已确认） | ① OCR 纠错字典 ② 购买方归属校验 ③ 纯预存管理 |

---

## 一、数据模型（company_infos 表，新迁移）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | |
| name | String(256) NOT NULL | 公司名称 |
| tax_id | String(64) NOT NULL UNIQUE | 纳税人识别号（18 位） |
| kind | String(16) NOT NULL default `'other'` | `self`（本司）/ `supplier`（供应商）/ `other` |
| is_default | Boolean NOT NULL default False | 默认本司（kind=self 中至多一个默认） |
| remark | String(256) NULL | 备注 |
| created_at / updated_at | timestamptz | 与既有模型一致（naive-UTC） |

## 二、三大用途的落地

### 1. 纯预存管理（CRUD）

- REST（admin 专属）：`GET/POST/PUT/DELETE /api/v1/company-infos`
- 校验：tax_id 18 位字母数字、name 非空、kind 枚举、is_default 与 kind=self 联动

### 2. OCR 纠错字典

- 集成点：解析路由的**文本/OCR 来源**（confidence < 1.0）产出后统一后处理
  `parse/company_dict.py: apply_company_dict(parsed) -> parsed`
- 算法：提取出的 buyer_name/seller_name 与预设公司名逐一比对：
  - 精确相等 → 跳过
  - `difflib.SequenceMatcher.ratio(name, preset) >= 0.75` 且长度差 ≤ 4 → 替换为预设名称
  - 税号完全匹配但名称不一致 → 以税号为准直接用预设名称
- 直接解决「铮」漏字类 OCR 误识；结构化来源（XML/XBRL，置信度 1.0）不做纠错

### 3. 购买方归属校验

- 解析结果 buyer_tax_id 与预设「本司」（kind=self）比对：
  - 配置了本司集合且均不匹配 → `validation_errors` 追加 `BUYER_MISMATCH`（走待复核，宁严勿松）
  - 未配置任何 kind=self → 跳过（向后兼容，零配置无影响）
  - 本司为多主体（分公司）场景：任一匹配即通过

## 三、MCP 工具（3 个）

| 工具 | 输入 | 输出 |
|------|------|------|
| `company_info_list` | `kind?` | 公司列表 |
| `company_info_save` | `name, tax_id, kind, is_default, remark?` | 保存后的记录 |
| `company_info_delete` | `id` | `{ok: true}` |

- 语义与 REST 共享 service；写操作审计 channel=mcp
- 纠错字典与归属校验在**服务端解析链路自动生效**（extract/ingest/收取全受益），无需 Agent 显式调用

## 四、Web UI

系统配置页新增「常用税号/公司」tab：表格（名称/税号/类型/默认/操作）+ 新建/编辑 modal + 删除确认。

## 五、实施任务草案（Plan K）

| # | 任务 | 内容 |
|---|------|------|
| K1 | 模型/迁移/Schema/API | company_infos + Alembic + CRUD + 校验 + 测试 |
| K2 | 纠错字典 + 归属校验 | company_dict.py + 路由集成 + 测试（漏字纠错/税号优先/本司不匹配 BUYER_MISMATCH/未配置跳过） |
| K3 | MCP 工具 | 3 工具 + 审计 + 测试 |
| K4 | Web UI tab + 文档 | SettingsView 新 tab + 测试 + docs |

## 六、决策点

1. **BUYER_MISMATCH 从严**（进待复核而非仅标注）——确认？
2. 纠错阈值 0.75 / 长度差 ≤4 为初值，真机验证后调参（如「澜鸿欣（上海）数字科技有限公司」vs「澜铮鸿欣（上海）数字科技有限公司」ratio ≈ 0.94 ✓ 会命中）
3. 结构化来源不做纠错（XML 原文为准，避免以字典覆盖原件数据）
