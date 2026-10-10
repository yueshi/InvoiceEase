"""Skill 文档契约（P1：docs/skills/*/SKILL.md）。

守三件事：
1. frontmatter 完整（渐进式披露靠 description，缺了就等于没有广告位）
2. 教的是两段握手（写工具必须 proposal → confirm_execute）
3. 引用的工具名真实存在（防文档教 Agent 调不存在的工具）
"""
import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parents[1] / "docs" / "skills"
EXPECTED = [
    "travel-reimbursement",
    "meal-reimbursement",
    "transport-reimbursement",
    "invoice-supplement",
    "over-budget",
]


def _doc(slug: str) -> str:
    return (SKILLS_DIR / slug / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter(text: str) -> str:
    assert text.startswith("---\n"), "缺少 frontmatter"
    return text.split("---", 2)[1]


@pytest.mark.parametrize("slug", EXPECTED)
def test_skill_doc_exists_with_frontmatter(slug):
    fm = _frontmatter(_doc(slug))
    assert f"name: {slug}" in fm
    for field in ("description", "description_zh", "version", "display_name", "visibility"):
        assert f"{field}:" in fm, f"{slug} frontmatter 缺 {field}"


@pytest.mark.parametrize("slug", EXPECTED)
def test_skill_doc_teaches_two_phase_handshake(slug):
    text = _doc(slug)
    assert "confirm_execute" in text
    assert "human_ack" in text
    assert "_proposal" in text


@pytest.mark.parametrize("slug", EXPECTED)
def test_skill_doc_tool_names_exist(slug):
    """文档里**被调用的**工具名必须真实存在（防教 Agent 调不存在的工具）。

    只校验调用点（`name(` / `name_proposal(`）与 `*_proposal` 后缀名——
    表格名/字段名（如 `expense_policies`）不是工具，不做猜测式匹配。
    """
    import asyncio

    from invoicing.mcp.server import build_server

    names = {t.name for t in asyncio.run(build_server().list_tools())}
    text = _doc(slug)

    # 1) 调用点：`foo(` 或 `foo(`
    call_sites = set(re.findall(r"[`\s]([a-z][a-z_]{3,})\(", text))
    for m in call_sites:
        assert m in names, f"{slug} 调用了不存在的工具：{m}"

    # 2) *_proposal 名：基名必须存在
    for m in set(re.findall(r"`([a-z][a-z_]{3,}_proposal)`", text)):
        assert m in names, f"{slug} 引用了不存在的提案工具：{m}"

    # 3) 至少引用 2 个真实工具（防文档空转）—— 三种写法取并集
    bare = set(re.findall(r"`([a-z][a-z_]{3,})`", text))
    proposal_names = set(re.findall(r"`([a-z][a-z_]{3,}_proposal)`", text))
    known = {m for m in (call_sites | bare | proposal_names)
             if m in names or f"{m}_proposal" in names}
    assert len(known) >= 2, f"{slug} 未引用足够的真实工具名（仅 {known}）"


def test_advertisement_budget_under_500_tokens():
    """5 份广告（description_zh）合计 ≤ 500 token —— spec §5.3 渐进式披露预算。"""
    total = 0.0
    for slug in EXPECTED:
        fm = _frontmatter(_doc(slug))
        m = re.search(r"description_zh:\s*\"?([^\"\n]+)", fm)
        assert m, f"{slug} 缺 description_zh"
        total += len(m.group(1)) / 1.5  # 中文约 1.5 字/token 粗估
    assert total <= 500, f"广告预算超支：约 {total:.0f} token"