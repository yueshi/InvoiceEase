# 出行/铁路类发票解析扩展设计（V0.1 草案，待评审）

| 项目 | 内容 |
|------|------|
| 文档版本 | V0.1 |
| 编制日期 | 2026-08-16 |
| 状态 | 待评审 |
| 背景 | WorkBuddy 真机测试发现：真实发票（12306 铁路电子客票、高德打车发票）的 PDF/OFD 均解析失败。实测诊断见下。 |
| 现状 | 解析引擎仅支持数电票 XML schema（`eInvoice`）与内嵌数电票 XBRL；其他格式 → 待复核。 |

---

## 一、实测诊断（真实文件，2026-08-16）

| 文件 | 内部结构 | 结论 |
|------|---------|------|
| 12306 铁路客票 PDF | 内嵌附件 `rai_issuer_*.xml`——XBRL 实例，`rai:` 命名空间（`http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai`），字段齐全 | **XBRL 变体，加解析器即可** |
| 12306 铁路客票 OFD | `Doc_0/Attachs/` 内嵌同一 rai XML（Attachments.xml 有声明） | 同上；现有 `extract_xml_from_ofd` 已能找到该 XML，但 `parse_invoice_xml` 不认 schema |
| 高德打车 PDF（3 份） | 无内嵌附件；**文本层完整**（发票号码 20 位/开票日期/购销方名称+税号/合计金额+税额） | **文本层规则提取即可，无需 OCR** |
| 高德打车 OFD | 版式 OFD：`Pages/*/Content.xml` 含 TextObject 文本 + 图片资源；无内嵌发票 XML | 同上（从 Content.xml 提取文本） |

**rai 元素清单（实测）**：`ElectronicInvoiceRailwayETicketNumber`（号码）/ `DateOfIssue`（开票日期）/ `TotalAmountExcludingTax`（不含税）/ `TaxAmount`（税额）/ `Fare`（价税合计，= 不含税+税额 ✓）/ `NameOfPurchaser` + `UnifiedSocialCreditCodeOfPurchaser`（购买方）/ `IssueParty`（销售方，实测为空）/ `TypeOfVoucher`（类型「电子发票（铁路电子客票）」）。**无大写金额字段** → CN 校验跳过。

## 二、方案（三个解析变体，接入既有分级路由）

### G1 铁路电子客票 XBRL（rai_parser）

- 识别：XML root 为 `{http://www.xbrl.org/2003/instance}xbrl` 且存在 `rai:ElectronicInvoiceRailwayETicketNumber`
- 映射 → ParsedInvoice：号码/日期/三金额（Decimal）/购买方（名称+税号）/销售方（`IssueParty`，空则空字符串）/`invoice_type=TypeOfVoucher`
- `parse_source`：沿内嵌提取路径（PDF_XBRL / OFD_XBRL），confidence 1.0；`total_amount_cn=None`（无大写字段，校验自动跳过 CN 项）
- 接入：`xml_parser.parse_invoice_xml` 内做变体分流（数电票优先，其次 rai）

### G2 版式 PDF 文本层规则提取（pdf_text_parser）

- `pypdf` `extract_text()` 全文 → 规则提取：
  - 发票号码：`发票号码\s*[:：]?\s*(\d{20})`（出行票）；8 位传统票号兜底
  - 开票日期：`开票日期\s*[:：]?\s*(\d{4})年(\d{1,2})月(\d{1,2})日`
  - 购买方：首个「名称[:：](\S+)」+ 其相邻「统一社会信用代码/纳税人识别号[:：]([0-9A-Z]{18})」；销售方：第二组同名模式（按出现序）
  - 金额：`合\s*计.*?¥\s*([\d.]+)\s*¥\s*([\d.]+)` → 不含税+税额，价税合计=二者之和；无合计行时 `价税合计.*?¥\s*([\d.]+)` 兜底
  - 大写：`价税合计\s*[（(]大写[)）]\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆整角分]+)`（存在才提取）
- `parse_source=PDF_TEXT`，**confidence 0.85**（高于 FRD 0.8 阈值 → 自动进验真；提取失败缺关键字段 → PDF_UNSTRUCTURED → 待复核，现状不变）

### G3 版式 OFD 文本提取（ofd_text）

- zip 遍历 `Doc_*/Pages/*/Content.xml`，lxml 收集全部 TextObject 的 TextCode 文本按序拼接 → 复用 G2 同一规则函数
- `parse_source=OFD_TEXT`，confidence 0.85；内嵌 XML（数电票/rai）优先级不变（现有路径先行）

### 路由集成（parse/router.py 调整）

```
XML → 数电票 schema → rai XBRL
PDF → 内嵌 XML（数电票/rai）→ 文本层规则提取 → PDF_UNSTRUCTURED
OFD → 内嵌 XML（数电票/rai）→ Content.xml 文本规则提取 → PDF_UNSTRUCTURED
```

MCP 的 extract/invoice_ingest、邮件收取、Web 上传全部走同一路由，自动受益。

## 三、合规与数据安全

- 原件存储、XML 归档、验真不自动放行、审计——全部复用现状，零改动
- **测试 fixture 用合成脱敏数据**（按实测 schema 构造 rai XML；按实测文本布局构造 PDF/OFD 文本片段），**真实发票文件不入库**（含企业税号/身份证号等敏感信息，仅作本地一次性验证，用后删除）

## 四、实施任务草案（Plan G，评审后细化）

| # | 任务 | 内容 |
|---|------|------|
| G1 | rai XBRL 解析变体 | rai_parser + xml_parser 分流 + 合成 rai fixture + 测试（含金额自洽校验） |
| G2 | PDF 文本层规则提取 | pdf_text_parser + 规则函数 + 合成文本 fixture + 测试（号码/日期/购销方/金额/大写/缺失兜底） |
| G3 | OFD 文本提取 + 路由集成 | ofd_text + router 三路调整 + 测试 + 回归 |
| G4 | 真机复验 | 用 WorkBuddy 已下载的真实文件本地跑 extract/ingest 验证（文件用完即删），更新 Phase 2 待办（关闭「打车票 XML 变体」条目，改记 OCR 扫描件） |

## 五、风险与决策点

1. **规则提取的误识别风险**：文本布局多样（实测 3 份高德 PDF 布局略有差异）——confidence 0.85 + 校验（合计自洽）+ 失败落待复核三层兜底；文本规则的匹配失败率需 G4 真机复验确认。
2. **OFD Content.xml 的文本顺序**可能与视觉顺序不一致——按文档顺序拼接，G4 验证。
3. OCR（扫描件/图片）仍留 Phase 2；本设计覆盖「有文本层/内嵌 XBRL」的版式文件。
