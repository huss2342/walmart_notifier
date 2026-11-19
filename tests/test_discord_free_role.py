from discord_bridge.free_role import FreeRoleSync, plan_changes

GUILD, FREE, PRO, TRIAL = "1" * 18, "2" * 18, "3" * 18, "4" * 18


def member(uid, *roles, bot=False):
    return {"user": {"id": uid, "bot": bot}, "roles": list(roles)}


def test_free_role_follows_paid_roles():
    rows = [
        member("10" * 9),                    # free, missing role -> add
        member("11" * 9, FREE),              # free, has role -> keep
        member("12" * 9, PRO, FREE),         # bought Pro -> remove
        member("13" * 9, TRIAL),             # trial counts as paid -> nothing
        member("14" * 9, bot=True),          # bots ignored
    ]
    assert plan_changes(rows, free_role=FREE, paid_roles={PRO, TRIAL}) == (["10" * 9], ["12" * 9])


def test_scan_applies_role_writes():
    class Client:
        def __init__(self):
            self.calls = []

        def guild_members(self, guild, limit, after):
            return [member("10" * 9), member("12" * 9, PRO, FREE)]

        def request(self, method, path):
            self.calls.append((method, path))

    client = Client()
    sync = FreeRoleSync(client, guild_id=GUILD, free_role=FREE, paid_roles=(PRO, None))
    assert sync.scan() == (1, 1)
    assert client.calls == [
        ("DELETE", f"/guilds/{GUILD}/members/{'12' * 9}/roles/{FREE}"),
        ("PUT", f"/guilds/{GUILD}/members/{'10' * 9}/roles/{FREE}"),
    ]
