"""修改範圍檢查的測試。

這支機器人守的是「五套程式互不相認」，所以它放行的條件要寫死在測試裡 ——
放寬一次沒人發現，那條規矩就等於不存在了。
"""

from __future__ import annotations

from tools.check_ownership import _attributed_to_owner, _is_owner, check

C = {"github": "a24209422", "name": "a24209422", "module_dir": "packages/modules/c_tbd"}
A = {"github": "Sinediesoft", "name": "Sinediesoft", "module_dir": "packages/modules/a_tbd"}


def test_認得出_github_的_noreply_email():
    """GitHub 建立的 commit 長這樣，帳號就在 email 裡 —— 最可靠的一條。

    C 的 git 作者名是「Anton」，跟 team.yml 的 name 欄位對不上，
    所以只靠名字比對會認不出他。
    """
    assert _is_owner("Anton <298539133+a24209422@users.noreply.github.com>", C) is True
    assert _is_owner("Anton <a24209422@users.noreply.github.com>", C) is True


def test_認得出真實_email_與名字():
    assert _is_owner("留詩迪 <sinediesoft@gmail.com>", A) is True
    assert _is_owner("Sinediesoft <somebody@example.com>", A) is True


def test_不是主人就不認():
    assert _is_owner("留詩迪 <sinediesoft@gmail.com>", C) is False
    assert _is_owner("Anton <298539133+a24209422@users.noreply.github.com>", A) is False
    # 帳號只是別人 email 的一部分不算 —— 不然 a24209422x@… 也會過
    assert _is_owner("x <xa24209422@users.noreply.github.com>", C) is False


def test_主人自己改的才放行(monkeypatch):
    import tools.check_ownership as mod

    monkeypatch.setattr(
        mod, "_authors_of", lambda *_: ["Anton <298539133+a24209422@users.noreply.github.com>"]
    )
    note = _attributed_to_owner("packages/modules/c_tbd/module.py", C, "main...HEAD")
    assert note and "整合型 PR" in note


def test_混了別人的改動就不放行(monkeypatch):
    """整合型 PR 裡「主人也有改」不等於「都是主人改的」。

    只要有一個 commit 不是主人寫的，那就是真的動到別人的資料夾，要擋。
    """
    import tools.check_ownership as mod

    monkeypatch.setattr(
        mod,
        "_authors_of",
        lambda *_: [
            "Anton <298539133+a24209422@users.noreply.github.com>",
            "留詩迪 <sinediesoft@gmail.com>",
        ],
    )
    assert _attributed_to_owner("packages/modules/c_tbd/module.py", C, "main...HEAD") is None


def test_查不出作者時維持擋下來(monkeypatch):
    """fail closed：工具的盲點讓人多解釋一次還好，放行一次真的越界就不好了。"""
    import tools.check_ownership as mod

    monkeypatch.setattr(mod, "_authors_of", lambda *_: None)
    assert _attributed_to_owner("packages/modules/c_tbd/module.py", C, "main...HEAD") is None
    # 沒給 --range（例如本機手動跑）也一樣擋
    monkeypatch.setattr(
        mod, "_authors_of", lambda *_: ["Anton <a24209422@users.noreply.github.com>"]
    )
    assert _attributed_to_owner("packages/modules/c_tbd/module.py", C, None) is None


def test_沒有_range_時照原本的規則擋():
    errors, _ = check("Sinediesoft", ["packages/modules/c_tbd/module.py"])
    assert errors and "不是你的模組資料夾" in errors[0]


def test_動到別人資料夾又不是主人改的_照樣擋(monkeypatch):
    import tools.check_ownership as mod

    monkeypatch.setattr(mod, "_authors_of", lambda *_: ["路人甲 <nobody@example.com>"])
    errors, _ = check("Sinediesoft", ["packages/modules/c_tbd/module.py"], "main...HEAD")
    assert errors and "不是你的模組資料夾" in errors[0]
