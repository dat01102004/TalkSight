from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.deps import get_current_user_required


router = APIRouter(prefix="/settings", tags=["settings"])


def _get_or_create_settings(db: Session, user: models.User) -> models.Setting:
    settings = db.query(models.Setting).filter(models.Setting.user_id == user.id).first()
    if settings:
        return settings

    settings = models.Setting(user_id=user.id)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


@router.get("/me", response_model=schemas.SettingResponse)
def get_my_settings(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user_required),
):
    return _get_or_create_settings(db, user)


@router.put("/me", response_model=schemas.SettingResponse)
def update_my_settings(
    req: schemas.SettingUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user_required),
):
    settings = _get_or_create_settings(db, user)
    data = req.model_dump(exclude_unset=True)

    for field, value in data.items():
        setattr(settings, field, value)

    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings
