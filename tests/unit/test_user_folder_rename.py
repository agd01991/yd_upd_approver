from types import SimpleNamespace

import pytest

from app.db.models import User, UserStatus
from app.services.user_folder_rename import rename_user_folder

pytestmark = pytest.mark.anyio


class ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, users):
        self.users = users
        self.executed = []

    async def scalars(self, stmt):
        text = str(stmt)
        if "SELECT DISTINCT upload_requests.target_folder" in text:
            return ScalarResult([])
        if "FROM upload_requests" in text:
            return ScalarResult([])
        return ScalarResult(self.users)

    async def execute(self, stmt):
        self.executed.append(stmt)
        return SimpleNamespace(all=lambda: [])

    async def flush(self):
        pass

    def add(self, _row):
        pass


class FakeClient:
    def __init__(self):
        self.moves = []

    async def move_resource(self, source, target, overwrite=False):
        self.moves.append((source, target, overwrite))


async def test_rename_rejects_target_folder_assigned_to_another_user_without_move() -> None:
    user = User(
        id=1,
        telegram_id=111,
        username="one",
        full_name="One",
        status=UserStatus.active,
        root_folder="disk:/root/one/",
        allowed_folders=["disk:/root/one/"],
    )
    other = User(
        id=2,
        telegram_id=222,
        username="two",
        full_name="Two",
        status=UserStatus.active,
        root_folder="disk:/root/two/",
        allowed_folders=["disk:/root/two/"],
    )
    client = FakeClient()
    with pytest.raises(ValueError, match="Папка уже назначена другому пользователю"):
        await rename_user_folder(
            FakeSession([user, other]), user, "disk:/root/one/", "two", 99, client
        )
    assert client.moves == []
