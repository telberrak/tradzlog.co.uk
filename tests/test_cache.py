from decimal import Decimal

from tradzlog_api.services.cache import JsonCache, decimalize


def test_json_cache_uses_memory_fallback(monkeypatch) -> None:
    cache = JsonCache()
    monkeypatch.setattr(cache, "_client", lambda: None)
    cache.set("analytics:test", {"net_pnl": Decimal("12.34")}, ttl_seconds=60)
    assert cache.get("analytics:test") == {"net_pnl": "12.34"}


def test_json_cache_deletes_prefix_from_memory(monkeypatch) -> None:
    cache = JsonCache()
    monkeypatch.setattr(cache, "_client", lambda: None)
    cache.set("analytics:a", {"value": 1}, ttl_seconds=60)
    cache.set("other:b", {"value": 2}, ttl_seconds=60)
    cache.delete_prefix("analytics:")
    assert cache.get("analytics:a") is None
    assert cache.get("other:b") == {"value": 2}


def test_decimalize_restores_numeric_strings() -> None:
    result = decimalize({"rows": [{"netPnl": "42.50", "key": "ORB"}]})
    assert result["rows"][0]["netPnl"] == Decimal("42.50")
    assert result["rows"][0]["key"] == "ORB"
