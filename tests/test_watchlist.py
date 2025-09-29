import tempfile

import yaml

from longport_quant.data.watchlist import WatchlistLoader


def test_watchlist_loader_reads_symbols(monkeypatch):
    payload = {
        "markets": {"hk": ["00005.HK"]},
        "symbols": [{"symbol": "AAPL", "market": "us"}],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yml") as tmp:
        yaml.safe_dump(payload, tmp)
        tmp.flush()
        monkeypatch.setenv("WATCHLIST_PATH", tmp.name)
        loader = WatchlistLoader()
        watchlist = loader.load()

    assert sorted(watchlist.symbols()) == ["00005.HK", "AAPL"]
    assert watchlist.symbols("hk") == ["00005.HK"]

