# 发票易 OCR 支持设计（V0.1 草案，待评审）

| 项目 | 内容 |
|------|------|
| 文档版本 | V0.1 |
| 编制日期 | 2026-08-16 |
| 状态 | 待评审 |
| 决策（已确认） | ① 本地 PaddleOCR（离线合规优先）② 范围：扫描 PDF + 粘连 OFD + 本地工具的 JPG/PNG；邮箱收取仍拒收图片附件（FRD G-03 原件策略不动） |
| 背景 | Plan G 真机复验：曹操 OFD 文本层数字粘连不可规则提取（5/6 成功中的唯一 FAIL）→ OCR 兜底 |

---

## 一、架构

**OcrProvider 抽象 + 可选依赖**（沿用 VerifyProvider 的插拔模式）：

```
OCR 路由（第五级兜底）：
PDF → 内嵌 XML → 文本层规则 → 【OCR：pymupdf 渲染页面 → PaddleOCR → 复用 text_rules】→ 待复核
OFD → 内嵌 XML → Content.xml 文本 → 【OCR：Res 下页面图片 → PaddleOCR → text_rules】→ 待复核
图片(JPG/PNG，仅本地工具) → 【直接 PaddleOCR → text_rules】→ 失败报「OCR 不可用」
```

**关键组件**：
- `parse/ocr.py`：`OcrProvider(ABC).ocr_image(image_bytes) -> OcrText(文本, 置信度均值) | None`；`PaddleOCRProvider`（**惰性初始化**——import 与模型加载在首次调用，避免启动成本与依赖缺失时的硬失败）；`get_ocr_provider()` 按可用性返回实现或 None
- 渲染/取图：`pymupdf`（fitz）渲染 PDF 页为 PNG（纯 pip，无系统 poppler 依赖）；OFD 取 `Doc_*/Res/*.png` 中面积最大的页面图
- **依赖为 optional extra**（`uv sync --extra ocr` 才装 paddlepaddle/paddleocr/pymupdf）；未安装时 OCR 路径优雅降级为待复核（现状行为），服务不因缺依赖而故障

## 二、置信度与校验（FRD 阈值语义）

- `confidence_score` = PaddleOCR 各文本行置信度**均值**（0-1）
- 校验（价税合计/大小写）照常执行：OCR 文本经 text_rules 提取 + validate——**校验失败 → 待复核**（现状不变）
- 「置信度 < 0.8 需人工核对」按 FRD 语义落在**展示层**：Web/MCP 详情已展示置信度，前端加 <0.8 的橙色高亮标注（Plan C 已预留展示位）
- 新 parse_source 枚举：`PDF_OCR` / `OFD_OCR` / `IMAGE_OCR`（String 列，无迁移）

## 三、MCP 本地工具图片语义变更

`extract_invoice / invoice_ingest` 对 IMAGE 输入：从「合规拒收」改为：
- OCR 可用 → 识别（IMAGE_OCR），成功返回结构化数据
- OCR 不可用 → 错误文案改为 `"OCR 引擎未安装（pip install invoicing[ocr] 或 uv sync --extra ocr）"`
- **邮箱收取的图片拒收不变**（fetch 层 G-03 合规文案原样保留，含拒收回复邮件）

## 四、合规与数据安全

- 原件/XML 归档、验真不自动放行、审计、邮箱拒收图片——全部不变
- OCR 全程本地计算，影像不出内网 ✓（离线部署合规）
- 模型文件：PaddleOCR 首次运行自动下载（联网一次）；纯离线环境预置模型目录（部署文档注明路径）

## 五、实施任务草案（Plan H，评审后细化）

| # | 任务 | 内容 |
|---|------|------|
| H1 | OCR 基础设施 | OcrProvider 抽象 + PaddleOCRProvider（惰性）+ pymupdf 渲染 + OFD 取图 + optional extra 依赖 + FakeOCR 单测（路由/置信度/降级） |
| H2 | 路由与 MCP 集成 | 第五级路由 + ParseSource 枚举 + extract/ingest 图片放开（OCR 不可用文案）+ 测试 |
| H3 | 真机复验 | 本机安装 ocr extra：曹操 OFD + 一张扫描件 PDF + 一张图片实测；前端置信度 <0.8 高亮；文档与 Phase 2 待办更新 |

## 六、风险与决策点

1. **PaddleOCR 安装体积**（约数百 MB，含模型）：作为 optional extra 隔离；CI/轻量环境不装，测试用 FakeOcrProvider。
2. **模型下载需联网一次**：纯离线部署需预置模型目录——H3 复验时实测下载路径并写进部署文档。
3. **OCR 文本布局与 text_rules 的适配度**：PaddleOCR 输出按行（自上而下），与现有规则（标签锚定+兜底）预期兼容；不兼容场景落待复核（宁缺毋滥）。
4. macOS x86_64 的 paddlepaddle wheel 可用性——H3 安装阶段验证；不可用则退而记录（不影响代码结构，Provider 可换引擎）。
