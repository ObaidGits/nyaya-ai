"""API v1 router aggregation.

Later phases add their routers (chat, documents, search, forms, feedback,
metrics) here without restructuring the application.
"""

from fastapi import APIRouter

from app.api.v1 import health

api_router = APIRouter()
api_router.include_router(health.router)
