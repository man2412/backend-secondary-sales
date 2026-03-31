import enum


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    SALES_DIRECTOR = "SALES_DIRECTOR"
    STATE_HEAD = "STATE_HEAD"
    RSM = "RSM"
    DEPUTY_RSM = "DEPUTY_RSM"
    ASM = "ASM"
    MR = "MR"
