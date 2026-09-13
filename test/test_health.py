from fastapi.testclient import TestClient

from invoicing.main import create_app


def test_health(db):
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "invoicing", "version": "0.1.0"}


def test_lifespan_preloads_ocr_engine(db, monkeypatch):
    """启动时预热 OCR 引擎（模型首次加载移出请求路径）。

    未装 ocr extra 时 preload 内部同步判空 → 空转，所以这里用打桩确认
    「调用发生了」而不是「引擎热了」。测试环境默认用 INVOICING_OCR_PRELOAD=false
    关掉，此处显式打开。
    """
    from invoicing.config import settings
    from invoicing.parse import ocr as ocr_mod

    calls: list = []
    monkeypatch.setattr(settings, "ocr_preload", True)
    monkeypatch.setattr(ocr_mod, "preload_ocr_engine", lambda *a, **kw: calls.append(1))

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
    assert calls == [1]


def test_lifespan_skips_ocr_preload_when_disabled(db, monkeypatch):
    """INVOICING_OCR_PRELOAD=false（测试/轻量部署）→ 不预热。"""
    from invoicing.config import settings
    from invoicing.parse import ocr as ocr_mod

    calls: list = []
    monkeypatch.setattr(settings, "ocr_preload", False)
    monkeypatch.setattr(ocr_mod, "preload_ocr_engine", lambda *a, **kw: calls.append(1))

    with TestClient(create_app()):
        pass
    assert calls == []
