# AuditMind 前端「助手」技术栈调研

- **日期**：2026-10-01
- **对象**：`/Users/james/AuditMind-0928/frontend/`（`src/agent/` 助手模块 + app 级依赖）
- **用途**：作为发票易 Web Agent（Vue 版）的对照参考，评估可借鉴项

## 一、技术栈总览

| 层 | 选型 | 版本 |
|---|---|---|
| 框架 | React + react-dom | 18.3 |
| 语言 | TypeScript（strict、ES2020、jsx: react-jsx） | 5.5 |
| 构建 | Vite + @vitejs/plugin-react | 5.3 |
| 样式 | TailwindCSS + PostCSS/autoprefixer（组件主用 className，个别内联 style） | 3.4 |
| 路由 | react-router-dom（app 级，助手本身无独立路由） | 6.26 |
| 状态 | **zustand**（无 Provider，`create()` 全局 store） | 5.0 |
| SSE | 原生 `fetch` + `ReadableStream` 手解析（EventSource 不支持 POST） | — |
| Markdown | react-markdown + remark-gfm + remark-breaks | 10.1 / 4.0 |
| 图表 | recharts（+ react-dom createPortal 全屏） | 2.12 |
| 图标 | lucide-react | 0.400 |
| 测试 | Vitest + @testing-library/react + jest-dom（jsdom 环境，setup 补 ResizeObserver polyfill） | 4.1 / 16.3 |
| 其他 app 级 | vis-data / vis-timeline（图谱时间线页，与助手无关） | 8.x |

## 二、`src/agent/` 模块结构

```
src/agent/
├── api.ts            # fetch SSE：postChat(req, handlers) → abort；parseSSEChunk 纯函数（buffer→events+remainder）
├── types.ts          # SSEEvent / Message / AgentContext / Intent
├── store.ts          # zustand：消息、流式态、抽屉宽度、悬浮球位置、按 scope 分桶会话
├── hooks/
│   ├── useAgentStream.ts   # send/abort；生命周期 user msg → beginAssistant → 消费流 → finalize
│   ├── streamFlusher.ts    # 流式 markdown「安全点」flush（防重排，见下）
│   └── useAgentContext.ts
├── components/
│   ├── AgentDrawer.tsx      # 可拖拽宽度 + Pin + 清空
│   ├── FloatingButton.tsx   # 可拖拽位置（localStorage 持久化）
│   ├── MessageList / MessageInput
│   ├── AssistantMarkdown.tsx# react-markdown + 自定义渲染（复制按钮、chart-* fence → AgentChart）
│   ├── AgentChart.tsx       # recharts：pie / bar / grouped-bar / radar + 全屏 portal
│   └── ContextChip.tsx
└── __tests__/        # 11 个测试文件（store/api/两个 hook/流 flusher/各组件/ChartFence）
```

## 三、三个值得关注的技术点

1. **`streamFlusher` —— 流式 Markdown 安全点渲染**（本模块最有价值的自研件）
   token 不逐条 push 给 UI（react-markdown 全量重解析会导致未闭合 ``` / `**` 反复重排）。策略：缓冲到「安全点」才 flush——`\n\n` 段落边界 / 中文句末+换行 / 代码块闭合 / 200 字水位；尾部处于开放语法块（未闭合 ```、`**`、链接）则等待。**例外**：`chart-*` 图表 fence 允许渐进 flush，配合 Loading 骨架先渲染。
2. **`parseSSEChunk` 纯函数 + remainder 残帧缓冲**：SSE 解析与传输解耦，可单测（api.test.ts）。
3. **会话持久化在客户端**：zustand + localStorage 分桶（按 scope），24h 无活动开新会话；session_id 客户端生成，后端无会话表——与发票易「服务端 agent_sessions + 审计」路线不同。

## 四、与发票易 Web Agent（Vue 版，已实现）对照

| 维度 | AuditMind（React） | 发票易（Vue，本仓） |
|---|---|---|
| 框架/状态 | React 18 + zustand | Vue 3 + Pinia |
| Markdown | react-markdown + 安全点流式 flush | 纯文本 `pre-wrap`（刻意零依赖简化） |
| 图表 | recharts（chart-* fence 协议） | 无 |
| SSE 解析 | `parseSSEChunk` 纯函数 | 同思路手解析（`api.ts`，含跨 chunk 边界处理） |
| 会话持久化 | localStorage 分桶（24h） | 服务端 `agent_sessions/agent_messages` 表 + `AGENT_CHAT` 审计 |
| 鉴权 | X-API-Key 单密钥 | JWT 多用户 + RBAC + SUSPENDED 闸门 + scope 工具守门 |

**可借鉴项（若后续迭代）**：
- 若引入 markdown 渲染 → 直接移植 `streamFlusher` 的「安全点 flush + 开放块等待」思路（框架无关，纯字符串逻辑）。
- 若做图表 → chart-* fence 协议 + ECharts（Vue 生态等价物）；骨架渐进渲染同理。
- 抽屉可拖拽宽度 / 悬浮球拖拽位置持久化是小巧的 UX 增强，实现成本低。
