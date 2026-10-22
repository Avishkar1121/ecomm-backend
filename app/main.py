from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session



from .core.config import settings
from .core.errors import register_error_handlers
from .core.database import get_db
from .deps import require_role
from .routers import auth, catlog ,cart, wishlist, orders

app = FastAPI(title="MarketHub API", version="0.2.0",
              docs_url="/docs" if settings.is_dev else None,
              redoc_url="/redoc" if settings.is_dev else None,
              openapi_url="/openapi.json" if settings.is_dev else None)
register_error_handlers(app)

# Angular (localhost:4200) calls this API with cookies, so credentials must be allowed
# and origins must be listed explicitly (a wildcard is not allowed with credentials).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1")
api.include_router(auth.router)
api.include_router(catlog.router)
api.include_router(cart.router)
api.include_router(wishlist.router)
api.include_router(orders.router)

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs" if settings.is_dev else "/health")
 
@api.get("/admin/ping", tags=["admin"])
def admin_ping(user: dict = Depends(require_role("admin"))):
    return {"hello": user["email"]}


app.include_router(api)


@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}