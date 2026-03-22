from pathlib import Path

from config import load_config, read_json_file, save_config


def test_save_and_load_config_preserve_utf8(tmp_path):
    cfg = tmp_path / "config.json"
    monitored = [
        {
            "num": "1",
            "nome": "Canal São João",
            "url": "https://example.com/live",
            "active": True,
            "qual": "best",
            "hist": "Última live às 10:30:00 do dia 22/03/2026",
        }
    ]

    save_config(
        cfg,
        Path("E:/GRAVACOES MANUAIS"),
        Path("E:/MONITORAMENTO"),
        monitored,
        "token-á",
        "chat-ç",
    )

    raw = cfg.read_text(encoding="utf-8")
    assert "Última live às 10:30:00 do dia 22/03/2026" in raw
    assert "Canal São João" in raw

    out_manual, out_monitor, loaded, token, chat_id = load_config(cfg)

    assert out_manual == Path("E:/GRAVACOES MANUAIS")
    assert out_monitor == Path("E:/MONITORAMENTO")
    assert loaded == monitored
    assert token == "token-á"
    assert chat_id == "chat-ç"


def test_read_json_file_accepts_legacy_cp1252(tmp_path):
    cfg = tmp_path / "legacy.json"
    text = '{"hist": "Última live às 15:47:12 do dia 03/06/2025"}'
    cfg.write_bytes(text.encode("cp1252"))

    data = read_json_file(cfg)

    assert data["hist"] == "Última live às 15:47:12 do dia 03/06/2025"
