from app.livekit_phone import LiveKitPhoneConfig, checklist, normalize_kr_phone


def test_normalize_kr_phone_strips_and_localizes():
    assert normalize_kr_phone("010-1234-5678") == "01012345678"
    assert normalize_kr_phone("+82 10-1234-5678") == "01012345678"


def test_checklist_marks_missing_credentials(monkeypatch):
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    cfg = LiveKitPhoneConfig.from_env()
    items = {item["id"]: item for item in checklist(cfg)}
    assert items["livekit_credentials"]["done"] is False
