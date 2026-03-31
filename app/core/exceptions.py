from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.responses import err


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content=err(str(exc)))
