from pydantic import BaseModel

from app.modules.users.schemas import UserOut


class SyncUserResponse(BaseModel):
    user: UserOut
    linked: bool
