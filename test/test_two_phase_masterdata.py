"""P0-2 company_info_* / bank_account_* 两段握手测试（v1.1 §7.5）。"""
from decimal import Decimal

import pytest

from invoicing.mcp import tools as mt
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY


def _confirm(tool, proposal):
    return mt.confirm_execute(token=proposal["proposal_token"],
                              tool_name=tool, human_ack=True)


def test_masterdata_tools_registered():
    expected = {"company_info_save", "company_info_delete",
                "bank_account_save", "bank_account_delete"}
    assert expected <= set(PROPOSAL_REGISTRY)


# ---- company_info_save -----------------------------------------------------

def test_company_info_save_proposal_then_confirm(db, mcp_admin_auth):
    prop = mt.company_info_save_proposal(name="澜铮鸿欣", tax_id="91310101MAELA36R35",
                                          kind="self", is_default=True)
    assert "澜铮鸿欣" in prop["preview"]["description"]
    assert mt.company_info_list() == []  # 提案零副作用

    out = _confirm("company_info_save", prop)
    assert out["tax_id"] == "91310101MAELA36R35"
    assert out["is_default"] is True


# ---- company_info_delete ---------------------------------------------------

def test_company_info_delete_proposal_then_confirm(db, mcp_admin_auth):
    saved = _confirm("company_info_save",
                     mt.company_info_save_proposal(name="A", tax_id="91310101MAELA36R35"))
    prop = mt.company_info_delete_proposal(saved["id"])
    assert "删除常用公司" in prop["preview"]["description"]

    out = _confirm("company_info_delete", prop)
    assert out == {"ok": True}
    assert mt.company_info_list() == []


# ---- bank_account_save -----------------------------------------------------

def test_bank_account_save_proposal_masks_account(db, mcp_admin_auth):
    """预览对账号做部分遮蔽（显示末 4 位），避免完整账号落入对话上下文。"""
    prop = mt.bank_account_save_proposal(account_no="6222-0212-3456-7890",
                                          account_name="本司基本户",
                                          bank_name="工商银行")
    desc = prop["preview"]["description"]
    assert desc.endswith("（本司基本户）")
    assert "7890" in desc          # 末 4 位可见
    assert "62220212" not in desc  # 前段不出现


def test_bank_account_save_confirm_persists(db, mcp_admin_auth):
    out = _confirm("bank_account_save",
                   mt.bank_account_save_proposal(account_no="6222021234567890",
                                                 account_name="本司基本户"))
    assert out["account_no"] == "6222021234567890"
    assert out["account_name"] == "本司基本户"


def test_bank_account_save_invalid_account_rejected_at_proposal(db, mcp_admin_auth):
    with pytest.raises(ValueError, match="6-32 位数字"):
        mt.bank_account_save_proposal(account_no="abc")


# ---- bank_account_delete ---------------------------------------------------

def test_bank_account_delete_proposal_then_confirm(db, mcp_admin_auth):
    saved = _confirm("bank_account_save",
                     mt.bank_account_save_proposal(account_no="6222021234567891"))
    prop = mt.bank_account_delete_proposal(saved["id"])
    assert "不可撤销" in prop["preview"]["description"]

    out = _confirm("bank_account_delete", prop)
    assert out == {"ok": True}
    assert mt.bank_account_list() == []