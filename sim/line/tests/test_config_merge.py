"""config.save() must not clobber edits made by another process between our load() and save()."""
from line import config


def test_save_merges_only_own_edits(tmp_path):
    p = tmp_path / "c.json"
    config.save(config.LineConfig(), p)

    panel = config.load(p)          # the panel loads…
    agent = config.load(p)          # …an agent loads the same state
    agent.gate.channel = "belt"     # agent edits its section and saves
    agent.gate.settle_ms = 150
    config.save(agent, p)

    panel.camera.px_per_mm = 4.3    # the panel edits an unrelated key from its stale copy and saves
    config.save(panel, p)

    disk = config.load(p)
    assert disk.gate.channel == "belt" and disk.gate.settle_ms == 150, "agent's edits survived the panel save"
    assert disk.camera.px_per_mm == 4.3, "panel's own edit was written"
    assert panel.gate.channel == "belt", "the saving copy picks up the others' edits"


def test_save_without_snapshot_writes_everything(tmp_path):
    p = tmp_path / "c.json"
    cfg = config.LineConfig()       # never loaded: no snapshot
    cfg.gate.open_deg = 115
    config.save(cfg, p)
    assert config.load(p).gate.open_deg == 115


def test_gate_channel_follows_config_on_disk(tmp_path, monkeypatch):
    import time

    from line import config as C
    from line.arduino_link import FakeArduino
    from line.gate import Gate

    p = tmp_path / "c.json"
    monkeypatch.setattr(C, "CONFIG_PATH", p)
    cfg = C.LineConfig()
    cfg.gate.channel = "belt"
    C.save(cfg, p)
    stale = C.LineConfig()          # what a stale panel copy looks like: channel base
    g = Gate(FakeArduino(), stale)
    assert g.channel() == "belt" and g.command(65) == "C -28" and stale.gate.channel == "belt"
    time.sleep(0.02)
    cfg.gate.channel = "base"
    C.save(cfg, p)
    assert g.channel() == "base" and g.command(65) == "S 65 90 75"
    g.close()
