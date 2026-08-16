# Phase 2 待办清单（增强版 Roadmap 输入）

> 来源：MVP（Plan A-F）实施与真机测试中发现的功能缺口与增强项。
> 状态：持续更新。执行 Phase 2 规划时以此为输入。

## 解析引擎

1. ~~出行/服务类电子发票 XML 变体解析~~ **已完成（Plan G，2026-08-16）**：铁路客票 rai XBRL 解析 + 版式 PDF/OFD 文本层规则提取（真机 6 文件 5 成功）。剩余：① 数字粘连文本层——Plan H 已用「% 锚定+后缀试税率」规则解决（曹操 OFD 实测通过）② 扫描件/图片——Plan H 本地 PaddleOCR 已落地（全页图片实测通过）③ 破碎版式购销方名称提取（税号可提、名称留空）→ Phase 2 优化 ④ OCR 精度优化（更复杂版式）⑤ ~~字体转曲 OFD~~ **已解决（Plan J，2026-08-16）**：自研 cairocffi 矢量渲染器（AbbreviatedData 路径解析 + DrawParam 颜色 → 整页 PNG → PaddleOCR），及时用车 OFD 实测 67.44 通过。剩余：OCR 个别字形误识（如「铮」漏字）→ OCR 精度优化。
2. **OCR + 大模型引擎**（FRD Phase 2 核心）：纯版式 PDF / 模糊件走 OCR+LLM 兜底，接入分级路由的 `PDF_UNSTRUCTURED` 路径。含字段缺口补全：checkCode、明细项 items[]、购销方地址电话/开户行账号。
3. 图片识别决策：FRD 合规「只收原件」与 design.html 的 OCR 图片路线冲突——按 FRD 合规优先执行（extract 工具已对图片返回拒收文案）；是否开放图片 OCR 需产品确认。

## Agently / 邮件

4. `+reply --confirmed` 参数实测（固定模板跳过两步确认）。
5. 大附件 `download_url` 分支真实验收（当前走 attachment_id 路径）。
6. 分页内 `created_at` 缺失守卫（终审 re-review 发现的 minor）。
7. 进程内限流计数器的多 worker/多进程部署语义（Plan D 时解决）。

## MCP / WorkBuddy

8. WorkBuddy 自定义 MCP 配置格式已实测可用（streamable-http + headers，2026-08-15 ✓）——关闭设计 §七风险 1。
9. stdio 适配器备选（若其他平台仅支持 stdio）。
10. `_mcp_admin_user` id=0 哨兵：Phase 2 写工具（verify/submit/stats）接入前必须解决（审计外键语义）。
11. SKILL.md 的 agent-mail 工具名大小写与平台实测对齐。

## 部署 / 生产对齐

12. 见 `design/2026-08-15-plan-d-checklist.md`（redis 模式实测、received 状态叙事、PG timestamptz、S3 流式、zip bomb 等）。

## 业务功能（FRD Phase 2）

13. 多邮箱支持、批量提交（金蝶/用友 API）、RBAC 完整实现、报表导出、WorkBuddy 深度集成（6 Tool + 自然语言）、OCR 引擎（= 第 2 条）。

## 常用税号及公司信息（Plan K，2026-08-16 已合并）遗留项

14. ~~常用税号及公司信息功能~~ **已完成（Plan K，2026-08-16）**：company_infos 表 + REST CRUD（admin）+ MCP 3 工具 + OCR 纠错字典 + BUYER_MISMATCH 归属校验 + Web UI 系统设置 tab。真机验收：预存「澜铮鸿欣」后及时用车 OFD（字体转曲→OCR）名称自动纠错 ✓。遗留 Minor：① worker BUYER_MISMATCH 用例未断言审计日志写入 ② REST PUT 显式传 null 字段（name/tax_id/is_default）→ IntegrityError 500，应归一化或 422 ③ 前端测试 a-button 断言依赖未注册 antd 的隐式条件 ④ 纠错阈值 0.75/长度差≤4 待真机样本积累后调参。

## OCR + 大模型引擎（Plan L，2026-08-16 已合并）遗留项

15. ~~OCR + 大模型引擎~~ **已完成（Plan L，2026-08-16，真机验收已完成 ✓）**：策略链重构（parse/pipeline.py，接口兼容）+ LLM 双通道（parse/llm.py，openai SDK 兼容协议，MIME 嗅探 + sha1 缓存 + 观测日志）+ 质量门三触发点（LLM_TEXT=0.9/VLM=0.85，落入纠错字典范围）。**验收结果**：字段级准确率 35/36=97.2% 达标（DeepSeek 服务商实测；唯一 fail「铮」字由 Plan K 纠错字典在生产链路修正→实际 100%；LLM 通道本次未触发，能力由 215 测试+真实调用背书）。**待办**：① 渲染缺陷：曹操 OFD 的 render_ofd_page_to_png 输出纯白页（同票 PDF 正常）③ llm_vlm 渲染失败时用原始 PDF/OFD 字节——加守卫（PDF/OFD 且 ctx.image None → return None）④ extract IMAGE 分支改走 parse_file 策略链（现为 Plan F 独立 OCR 路径，无 LLM 兜底）⑤ HEIC 魔数未覆盖 ⑥ 质量门 total_amount==Decimal("0") 误判缺失 ⑦ docs「未启用时行为完全一致」措辞应改为「基本一致」（质量门使缺字段半成品由后续策略兜底）。
