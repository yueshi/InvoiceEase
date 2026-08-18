"""通知机器人测试（M7：企微 webhook 封装与事件接线）。"""
from invoicing import notify as mod


def test_notify_noop_when_url_unset(monkeypatch):
    """URL 未配置 → no-op 不报错（通知绝不阻断主流程）。"""
    monkeypatch.setattr(mod.settings, "notify_webhook_url", "")
    assert mod.notify("测试") is False


def test_notify_posts_markdown(monkeypatch):
    """配置 URL → POST 企微 markdown 格式。"""
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"errcode": 0}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(mod.settings, "notify_webhook_url", "https://qyapi.weixin.qq.com/webhook?key=test")
    monkeypatch.setattr(mod.httpx, "post", _fake_post)
    assert mod.notify("待复核测试") is True
    assert captured["json"]["msgtype"] == "markdown"
    assert "待复核测试" in captured["json"]["markdown"]["content"]


def test_notify_http_failure_degrades(monkeypatch):
    """webhook 失败 → 返回 False 且不抛异常。"""
    import httpx

    monkeypatch.setattr(mod.settings, "notify_webhook_url", "https://qyapi.weixin.qq.com/webhook?key=x")
    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert mod.notify("x") is False
