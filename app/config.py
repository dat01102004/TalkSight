import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./talksight.db")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "8"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

MOCK_AI = os.getenv("MOCK_AI", "true").lower() == "true"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

IMAGE_CACHE_ENABLED = os.getenv("IMAGE_CACHE_ENABLED", "true").lower() == "true"
IMAGE_CACHE_DIR = os.getenv("IMAGE_CACHE_DIR", ".cache/image_hash")
IMAGE_CACHE_TTL_SECONDS = int(os.getenv("IMAGE_CACHE_TTL_SECONDS", str(3 * 24 * 60 * 60)))
IMAGE_CACHE_NEAR_DUP_MAX_DISTANCE = int(os.getenv("IMAGE_CACHE_NEAR_DUP_MAX_DISTANCE", "6"))
IMAGE_CACHE_MAX_ENTRIES = int(os.getenv("IMAGE_CACHE_MAX_ENTRIES", "2000"))