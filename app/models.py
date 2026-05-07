from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(120), nullable=True)
    phone = Column(String(20), unique=True, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    histories = relationship(
        "History",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    setting = relationship(
        "Setting",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class History(Base):
    __tablename__ = "histories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    input_data = Column(Text, nullable=False)
    result_text = Column(Text, nullable=False)
    image_sha256 = Column(String(64), nullable=True, index=True)
    image_dhash = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="histories")


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    voice = Column(String(255), nullable=True)
    rate = Column(Float, nullable=False, default=1.0, server_default="1.0")
    volume = Column(Float, nullable=False, default=1.0, server_default="1.0")
    language = Column(String(20), nullable=False, default="vi-VN", server_default="vi-VN")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="setting")
