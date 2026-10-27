"""CLI defaults and safe refusals without credentials or remote mutation."""

import json

from discord_bridge import __main__ as cli
from discord_bridge.provision import ProvisionError


def test_preview_is_offline_and_has_exact_plans(capsys):
    assert cli.main(["plan"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plans"]["starter"]["monthly_price_cents"] == 500
    assert result["plans"]["pro"]["monthly_price_cents"] == 2000
    assert result["plans"]["starter"]["delay_seconds"] == 300
    assert result["plans"]["free"]["delay_seconds"] == 86400


def test_expected_setup_refusal_is_safe_json(monkeypatch, capsys):
    class Client:
        def __init__(self, _token):
            pass

        def request(self, *_args):
            return {"id": "100000000000000002"}

    def refuse(*_args, **_kwargs):
        raise ProvisionError("Setup refused: untracked channel")

    monkeypatch.setattr(cli, "load_settings", lambda: None)
    monkeypatch.setattr(cli, "DiscordClient", Client)
    monkeypatch.setattr("discord_bridge.provision.provision", refuse)
    assert cli.main(["provision", "--guild", "100000000000000001"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["error"] == "Setup refused: untracked channel"
    assert "Traceback" not in output.err


def test_missing_bot_token_fails_before_network(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: None)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert cli.main(["audit"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "DISCORD_BOT_TOKEN is missing or invalid"
