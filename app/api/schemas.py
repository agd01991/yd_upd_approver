from pydantic import BaseModel


class UploadPatch(BaseModel):
    safe_filename: str | None = None
    filename_stem: str | None = None
    filename_extension: str | None = None
    target_folder: str | None = None


class RejectBody(BaseModel):
    reason: str = "Отклонено администратором"


class AllowedFolder(BaseModel):
    path: str
    label: str


class AllowedFoldersResponse(BaseModel):
    items: list[AllowedFolder]


class DiskRootUpdate(BaseModel):
    root: str


class FolderProfileBody(BaseModel):
    contract_number: str
    contract_date: str
    contract_full_name: str
    requested_folder_name: str | None = None


class FolderRenameRequestCreate(FolderProfileBody):
    requested_folder_name: str


class FolderRenameApproveBody(BaseModel):
    source_folder: str


class FolderRenameRejectBody(BaseModel):
    reason: str = "Отклонено администратором"


class AdminRenameFolderBody(BaseModel):
    source_folder: str
    new_folder_name: str
