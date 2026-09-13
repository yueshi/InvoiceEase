"""银行回单模板库：把「适配一家银行」从改代码变成加模板。

每份模板声明四类信息：
- detect_keywords：识别该行回单（文本命中任一即判定）
- block_head_markers / block_end_markers：分块标记（多值，兼容一页一张/多张）
- field_labels：字段标签别名表（date/party/amount/abstract）
- direction：方向关键词（收/付/中性表单类型）

解析器（receipt.py）按「命中模板 → 模板驱动规则；无模板 → LLM 块级兜底」工作。
接入新银行：在 BUILTIN_TEMPLATES 增加一份（或后续由 DB 覆盖），并补语料测试
（test/fixtures/receipts/<bank>/）。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BankTemplate:
    code: str  # 银行代码（入库/展示用）
    name: str  # 展示名
    detect_keywords: tuple[str, ...]
    block_head_markers: tuple[str, ...] = ()
    block_end_markers: tuple[str, ...] = ()
    # 字段标签别名（正则片段，直接拼接进分组正则）
    date_labels: tuple[str, ...] = ()
    party_labels: tuple[str, ...] = ()  # 对方（收款方/税务机关/国库）
    party_self_labels: tuple[str, ...] = ()  # 本方（账户持有人）——命中即判定本司账户行，不当对方
    amount_labels: tuple[str, ...] = ()
    abstract_labels: tuple[str, ...] = ()
    # 摘要的「表头标签」（值在下一行/同一行的表体）
    abstract_table_labels: tuple[str, ...] = ()
    direction_in: tuple[str, ...] = ()
    direction_out: tuple[str, ...] = ()
    direction_label_in: tuple[str, ...] = ()
    direction_label_out: tuple[str, ...] = ()
    extras: dict = field(default_factory=dict)


# 各银行常见措辞的**通用兜底**（模板未覆盖时的宽松识别，避免误判为「无模板」）
_GENERIC_BLOCK_END = ("此回单以客户真实交易为依据", "回单以客户真实交易为依据", "本回单仅供查询")
_GENERIC_BLOCK_HEAD = ("单位客户专用回单", "电子回单", "回单凭证", "客户回单", "业务回单")

# 建行（实测版式：一页 3 张，块尾免责声明行 + 单位客户专用回单头）
CCB = BankTemplate(
    code="ccb",
    name="中国建设银行",
    detect_keywords=("中国建设银行", "建行", "ccb.com"),
    block_head_markers=("单位客户专用回单",),
    block_end_markers=("此回单以客户真实交易为依据",),
    date_labels=("转账日期", "交易日期", "付款日期", "日期"),
    party_labels=(
        "对方户名", "收款方名称", "对方名称", "收款人全称", "收款人户名",
        "收款国库", "征收机关", "户名",
    ),
    party_self_labels=("付款人全称", "付款人户名", "账户名称"),
    amount_labels=("交易金额", "付款金额", "支付金额", "金额"),
    abstract_labels=("摘要", "用途", "备注"),
    abstract_table_labels=("项目名称", "计息项目", "收费项目", "税（费）种名称"),
    direction_in=("利息", "结息", "存入"),
    direction_out=("缴款书", "税票号码", "手续费", "工本费"),
    direction_label_in=("贷方回单", "收款人回单", "收款回单"),
    direction_label_out=("借方回单", "付款人回单", "付款回单"),
)

# 其他银行的骨架（先只做识别 + 通用分块，字段标签待真实样本沉淀）
# 登记在此即可被识别并归入正确银行；无标签时字段走 LLM 兜底 + needs_review。
ICBC = BankTemplate(
    code="icbc",
    name="中国工商银行",
    detect_keywords=("中国工商银行", "工商银行", "icbc"),
    block_head_markers=("电子回单", "业务回单"),
    block_end_markers=("回单以客户真实交易为依据", "回单仅供查询"),
    date_labels=("交易日期", "转账日期", "日期"),
    party_labels=("收款人户名", "对方户名", "收款方户名", "收款方名称"),
    party_self_labels=("付款人户名", "付款人名称", "申请人户名"),
    amount_labels=("交易金额", "转账金额", "金额"),
    abstract_labels=("摘要", "用途", "备注"),
)

ABC = BankTemplate(
    code="abc",
    name="中国农业银行",
    detect_keywords=("中国农业银行", "农业银行", "abchina"),
    block_head_markers=("电子回单", "客户回单"),
    block_end_markers=("回单以客户真实交易为依据",),
)

CMB = BankTemplate(
    code="cmb",
    name="招商银行",
    detect_keywords=("招商银行", "cmbchina"),
    block_head_markers=("电子回单", "回单凭证"),
    block_end_markers=("回单以客户真实交易为依据",),
)

BOC = BankTemplate(
    code="boc",
    name="中国银行",
    detect_keywords=("中国银行", "bank of china", "boc.cn"),
    block_head_markers=("电子回单", "客户回单"),
    block_end_markers=("回单以客户真实交易为依据",),
)

BUILTIN_TEMPLATES: tuple[BankTemplate, ...] = (CCB, ICBC, ABC, CMB, BOC)


def detect_bank(text: str) -> BankTemplate | None:
    """按检测关键词识别银行；未命中返回 None（走通用兜底 + LLM）。"""
    flat = (text or "").lower()
    for tpl in BUILTIN_TEMPLATES:
        if any(k.lower() in flat for k in tpl.detect_keywords):
            return tpl
    return None


def get_template(code: str | None) -> BankTemplate | None:
    for tpl in BUILTIN_TEMPLATES:
        if tpl.code == code:
            return tpl
    return None


def block_markers(tpl: BankTemplate | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """分块标记：模板缺失时用通用兜底。"""
    heads = (tpl.block_head_markers if tpl and tpl.block_head_markers else _GENERIC_BLOCK_HEAD)
    ends = (tpl.block_end_markers if tpl and tpl.block_end_markers else _GENERIC_BLOCK_END)
    return heads, ends
