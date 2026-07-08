from pydantic import BaseModel


class UploadPatch(BaseModel):
    safe_filename: str | None = None
    target_folder: str | None = None


class RejectBody(BaseModel):
    reason: str = "Отклонено администратором"
