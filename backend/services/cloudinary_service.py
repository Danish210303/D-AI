import cloudinary
import cloudinary.uploader
import logging
from config import settings

logger = logging.getLogger(__name__)

# Configure Cloudinary
if settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY and settings.CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True
    )
    logger.info("Cloudinary configured successfully.")
else:
    logger.warning("Cloudinary environment keys are missing. Standard uploads will fail.")

async def upload_file_to_cloudinary(file_bytes: bytes, file_name: str) -> dict:
    """
    Upload file bytes to Cloudinary.
    Uses 'raw' resource type for tabular/text/pdf files to prevent image processing failure.
    """
    import asyncio
    
    ext = file_name.split(".")[-1].lower()
    resource_type = "image" if ext in ("jpg", "jpeg", "png", "webp", "gif") else "raw"
    
    def _upload():
        return cloudinary.uploader.upload(
            file_bytes,
            public_id=file_name,
            resource_type=resource_type
        )
        
    result = await asyncio.to_thread(_upload)
    return {
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id"),
    }
