import hashlib
from pathlib import Path


class TempStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, request_code: str, filename: str) -> Path:
        folder = self.root / request_code
        folder.mkdir(parents=True, exist_ok=True)
        return folder / filename

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def delete(path: str) -> None:
        Path(path).unlink(missing_ok=True)
