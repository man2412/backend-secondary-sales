import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.redis import close_redis


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
# Configure the root logger early (before app/router import side effects log
# anything). uvicorn runs with its own access/error loggers — those are left
# alone; this only configures our own `app.*` loggers.
def _configure_logging() -> None:
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    # Clear any pre-existing handlers (e.g. uvicorn --reload re-imports this
    # module) so we don't accumulate duplicates.
    app_logger.handlers.clear()
    app_logger.addHandler(handler)
    app_logger.propagate = False


_configure_logging()
from app.modules.allocations.router import router as allocations_router
from app.modules.auth.router import router as auth_router
from app.modules.doctors.router import router as doctors_router
from app.modules.entity_import.router import router as entity_import_router
from app.modules.master.router import router as master_router
from app.modules.reports.router import router as reports_router
from app.modules.sales.router import router as sales_router
from app.modules.stockists.router import router as stockists_router
from app.modules.users.router import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_redis()


app = FastAPI(title="APTUS API", lifespan=lifespan)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

v1 = settings.API_V1_PREFIX
app.include_router(auth_router, prefix=v1)
app.include_router(users_router, prefix=v1)
app.include_router(master_router, prefix=v1)
app.include_router(stockists_router, prefix=v1)
app.include_router(doctors_router, prefix=v1)
app.include_router(allocations_router, prefix=v1)
app.include_router(sales_router, prefix=v1)
app.include_router(reports_router, prefix=v1)
app.include_router(entity_import_router, prefix=v1)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
