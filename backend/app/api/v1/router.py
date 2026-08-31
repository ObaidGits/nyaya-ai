"""API v1 router aggregation.

Later phases add their routers (chat, documents, search, forms, feedback,
metrics) here without restructuring the application.
"""

from fastapi import APIRouter

from app.api.v1 import admin, chat, documents, feedback, forms, health, metrics, search, speech

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(search.router)
api_router.include_router(forms.router)
api_router.include_router(metrics.router)
api_router.include_router(feedback.router)
api_router.include_router(speech.router)
api_router.include_router(admin.router)
