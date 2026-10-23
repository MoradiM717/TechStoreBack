"""
Product image upload.

Uses S3 when credentials are configured (`S3_ACCESS_KEY` / `S3_SECRET_KEY`), and
otherwise writes to `settings.media_root` and serves the file from `/media`, so a
fresh clone can upload images without any cloud setup.
"""
import mimetypes
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_role
from app.models.product import Product, ProductImage
from app.schemas.product import ProductImageOut
from app.services.search_service import index_product

router = APIRouter(prefix="/api/admin/uploads", tags=["admin-uploads"])

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/gif": ".gif",
}
MAX_BYTES = 8 * 1024 * 1024  # 8 MB


def _safe_stem(filename: str) -> str:
    stem = Path(filename or "image").stem
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-.")
    return (stem or "image")[:60]


def _store_local(data: bytes, key: str) -> str:
    root = Path(settings.media_root)
    destination = root / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return f"{settings.public_base_url.rstrip('/')}/media/{key}"


def _store_s3(data: bytes, key: str, content_type: str) -> str:
    import boto3  # imported lazily so local dev doesn't need AWS config

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl="public, max-age=31536000, immutable",
    )
    base = settings.s3_public_base_url or f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}"
    return f"{base.rstrip('/')}/{key}"


@router.post("/images", status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    _admin=Depends(require_role("admin", "editor")),
):
    """Returns `{url}` — the caller then attaches it to a product via the product form."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="That file type isn't supported. Upload a JPEG, PNG, WebP, AVIF or GIF.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file was empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That image is {len(data) // 1024 // 1024} MB. The limit is 8 MB.",
        )

    extension = ALLOWED_CONTENT_TYPES[content_type] or mimetypes.guess_extension(content_type) or ""
    key = f"products/{secrets.token_hex(8)}-{_safe_stem(file.filename)}{extension}"

    try:
        url = _store_s3(data, key, content_type) if settings.s3_enabled else _store_local(data, key)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't save the image: {exc}")

    return {"url": url, "storage": "s3" if settings.s3_enabled else "local"}


@router.post("/products/{product_id}/images", response_model=ProductImageOut, status_code=201)
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin=Depends(require_role("admin", "editor")),
):
    """Upload and attach in one step, used by the product form's image dropzone."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    result = await upload_image(file=file, _admin=admin)

    next_order = max((img.sort_order for img in product.images), default=-1) + 1
    image = ProductImage(
        product_id=product.id,
        url=result["url"],
        alt_text=product.name,
        sort_order=next_order,
    )
    db.add(image)
    db.commit()
    db.refresh(image)

    db.refresh(product)
    if product.is_published:
        index_product(product)

    return image
