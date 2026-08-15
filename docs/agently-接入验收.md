# Agently 接入验收记录

- 日期：2026-08-15
- 邮箱：aken123@agent.qq.com（本机 keychain 授权，未配置 token）
- 用例：收件箱「发票测试-数电票」（附件 dianzi.xml，2026-08-15 08:11:27Z 发出）
- 结果：真实收取 → 解析（XML，置信度 1.0）→ 验真（Mock passed）→ `pending_submit`，价税合计 1000.00，XML 原件已归档
- 命令：`cd backend && uv run python ../tmp/agently-e2e.py`（脚本执行后删除）
- 实测输出：
  - `收取结果: PollResult(received=1, rejected_images=0, ignored=0, duplicates=0, errors=0)`
  - `验收通过: invoice #1 24312000000012345678 pending_submit 1000.00`
- 备注：
  - 限流参数（批上限 8、请求间隔 8s）生效，一轮验收约 40 秒
  - 实测发现并修复 `attachment +download` 契约偏差：`--output` 必须为相对路径目录（CLI 以自身 cwd 解析，`saved_to` 返回绝对路径）；修复后子进程 cwd 指向临时目录、传 `--output .`
  - +reply 拒收回复需图片邮件用例另行验证
