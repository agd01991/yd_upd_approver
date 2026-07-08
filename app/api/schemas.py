from pydantic import BaseModel


class UploadPatch(BaseModel):
    safe_filename: str | None = None
    target_folder: str | None = None


class RejectBody(BaseModel):
    reason: str = "Отклонено администратором"


class AllowedFolder(BaseModel):
    path: str
    label: str


class AllowedFoldersResponse(BaseModel):
    items: list[AllowedFolder]
