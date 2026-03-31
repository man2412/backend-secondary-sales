from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.redis import close_redis
from app.modules.allocations.router import router as allocations_router
from app.modules.auth.router import router as auth_router
from app.modules.doctors.router import router as doctors_router
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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
