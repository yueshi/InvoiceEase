"""城市档映射必须可配置（spec §7.2 ✅5 条件不得硬编码）+ 名称归一化。"""
from invoicing.config import settings
from invoicing.workflow.validators import city_tier_of


def test_city_tier_builtin_defaults_when_unset():
    assert city_tier_of("北京") == "tier1"
    assert city_tier_of("西安") == "default"  # 未收录 → default


def test_city_tier_from_settings_json(monkeypatch):
    monkeypatch.setattr(settings, "city_tiers", '{"杭州": "tier2", "成都": "tier2"}')
    assert city_tier_of("杭州") == "tier2"
    assert city_tier_of("成都") == "tier2"
    assert city_tier_of("西安") == "default"


def test_city_tier_settings_replaces_defaults(monkeypatch):
    """配置非空 = 全量替换（可预测：运营改了映射不会与内置残留混合）。"""
    monkeypatch.setattr(settings, "city_tiers", '{"西安": "tier2"}')
    assert city_tier_of("西安") == "tier2"
    assert city_tier_of("北京") == "default"


def test_city_tier_normalizes_suffix():
    assert city_tier_of("北京市") == "tier1"
    assert city_tier_of(" 上海 ") == "tier1"


def test_city_tier_malformed_json_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(settings, "city_tiers", "{不是合法 JSON")
    assert city_tier_of("北京") == "tier1"  # 不炸，回落内置
