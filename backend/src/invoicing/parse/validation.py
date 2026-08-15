from decimal import ROUND_HALF_UP, Decimal

from invoicing.parse.schemas import ParseError, ParsedInvoice

CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
CN_UNITS = ["", "拾", "佰", "仟"]
CN_SECTIONS = ["", "万", "亿", "万亿"]


def _section_to_cn(n: int) -> str:
    """0 <= n <= 9999 → 中文（无单位尾部补零）"""
    out, zero = "", False
    for pos in (3, 2, 1, 0):
        unit = 10**pos
        d, n = divmod(n, unit)
        if d == 0:
            if out and n > 0:
                zero = True
            continue
        if zero:
            out += "零"
            zero = False
        out += CN_DIGITS[d] + CN_UNITS[pos]
    return out


def amount_to_cn(amount: Decimal) -> str:
    if amount < 0:
        raise ValueError("金额不能为负")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_cents = int(amount * 100)
    yuan, cents = divmod(total_cents, 100)
    jiao, fen = divmod(cents, 10)
    if yuan == 0 and jiao == 0 and fen == 0:
        return "零元整"
    result = ""
    if yuan > 0:
        secs = []
        n = yuan
        while n > 0:
            secs.append(n % 10000)
            n //= 10000
        for i in range(len(secs) - 1, -1, -1):
            seg = _section_to_cn(secs[i])
            if not seg:
                if (
                    result
                    and i > 0
                    and not result.endswith("零")
                    and any(s != 0 for s in secs[:i])
                ):
                    result += "零"
                continue
            if result and secs[i] < 1000 and not result.endswith("零"):
                result += "零"
            result += seg + CN_SECTIONS[i]
        result += "元"
    if jiao == 0 and fen == 0:
        result += "整"
    else:
        if jiao:
            result += CN_DIGITS[jiao] + "角"
        elif result and fen:
            result += "零"
        if fen:
            result += CN_DIGITS[fen] + "分"
    return result


def validate(parsed: ParsedInvoice) -> list[ParseError]:
    errors: list[ParseError] = []
    expected_total = parsed.amount_without_tax + parsed.tax_amount
    if expected_total != parsed.total_amount:
        errors.append(
            ParseError(
                code="TOTAL_MISMATCH",
                message=(
                    f"价税合计不一致: 不含税{parsed.amount_without_tax} + "
                    f"税额{parsed.tax_amount} != 合计{parsed.total_amount}"
                ),
            )
        )
    if parsed.total_amount_cn:
        cn = parsed.total_amount_cn.replace(" ", "").replace("　", "")
        if amount_to_cn(parsed.total_amount) != cn:
            errors.append(
                ParseError(
                    code="CN_MISMATCH",
                    message=f"大小写金额不一致: {parsed.total_amount} 对应 '{amount_to_cn(parsed.total_amount)}'，实际 '{cn}'",
                )
            )
    return errors
