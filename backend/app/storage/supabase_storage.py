"""
app/storage/supabase_storage.py
================================
Supabase Storage (S3-compatible) persistence layer.

Drop-in replacement for the old MinIO service.  Uses ``boto3`` to talk to
Supabase Storage's S3-compatible endpoint.

Function signatures are intentionally kept similar to the old MinIO
helpers so callers require minimal refactoring.

IMPORTANT
---------
- Supabase S3 requires ``region_name="auto"`` — any other value
  will cause signature validation failures.
- ``signature_version="s3v4"`` is mandatory.
- Buckets must already exist in the Supabase Dashboard (they are
  not auto-created at runtime like local MinIO).
"""

from __future__ import annotations

import asyncio
import io
from loguru import logger
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig

from app.config import settings

# ---------------------------------------------------------------------------
# S3 CLIENT SINGLETON
# ---------------------------------------------------------------------------

_s3_client = None


def _get_s3_client():
    """
    Return (or lazily create) the shared ``boto3`` S3 client configured
    for Supabase Storage.
    """
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    _s3_client = boto3.client(
        "s3",
        endpoint_url=settings.SUPABASE_STORAGE_ENDPOINT,
        aws_access_key_id=settings.SUPABASE_STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.SUPABASE_STORAGE_SECRET_KEY,
        region_name=settings.SUPABASE_STORAGE_REGION,   # MUST be "auto"
        config=BotoConfig(signature_version="s3v4"),
    )
    logger.info("supabase_storage: S3 client initialised (endpoint=%s)", settings.SUPABASE_STORAGE_ENDPOINT)
    return _s3_client


# ---------------------------------------------------------------------------
# PUBLIC API — mirrors old MinIO function signatures
# ---------------------------------------------------------------------------


def save_to_minio(
    bucket_name: str,
    object_name: str,
    file_bytes: bytes,
    content_type: str = "image/png",
) -> str:
    """
    Upload a file to Supabase Storage.

    Backward-compatible signature — keeps the old ``save_to_minio`` name
    so existing callers don't need changes.

    Returns
    -------
    str
        Storage reference in ``"bucket/object"`` format, identical to the
        old MinIO convention.
    """
    client = _get_s3_client()
    client.put_object(
        Bucket=bucket_name,
        Key=object_name,
        Body=file_bytes,
        ContentType=content_type,
    )
    logger.debug("supabase_storage: uploaded %s/%s (%d bytes)", bucket_name, object_name, len(file_bytes))
    return f"{bucket_name}/{object_name}"


def move_minio_object(
    src_bucket: str,
    src_object: str,
    dest_bucket: str,
    dest_object: str,
) -> str:
    """
    Copy an object between buckets and delete the source.

    Returns
    -------
    str
        New storage reference in ``"dest_bucket/dest_object"`` format.
    """
    client = _get_s3_client()
    client.copy_object(
        Bucket=dest_bucket,
        CopySource={"Bucket": src_bucket, "Key": src_object},
        Key=dest_object,
    )
    client.delete_object(Bucket=src_bucket, Key=src_object)
    logger.debug(
        "supabase_storage: moved %s/%s → %s/%s",
        src_bucket, src_object, dest_bucket, dest_object,
    )
    return f"{dest_bucket}/{dest_object}"


async def get_minio_file(file_url: str) -> bytes:
    """
    Retrieve file bytes from Supabase Storage given a stored URL path.

    Parameters
    ----------
    file_url:
        Path in the format ``"bucket_name/object_key"``
        (as stored in ``user_documents.file_url``).

    Returns
    -------
    bytes
        The raw file content.

    Raises
    ------
    ValueError
        If *file_url* cannot be parsed into bucket + object key.
    """
    if "/" not in file_url:
        raise ValueError(f"Invalid storage file URL (no '/' separator): {file_url}")

    bucket_name, object_key = file_url.split("/", 1)

    def _fetch() -> bytes:
        client = _get_s3_client()
        response = client.get_object(Bucket=bucket_name, Key=object_key)
        return response["Body"].read()

    return await asyncio.to_thread(_fetch)


def get_storage_object_bytes(bucket: str, object_name: str) -> bytes:
    """
    Synchronous helper — fetch raw bytes from Supabase Storage.

    Replaces the old ``get_minio_object_bytes`` used by Celery workers
    and video utilities.
    """
    client = _get_s3_client()
    response = client.get_object(Bucket=bucket, Key=object_name)
    return response["Body"].read()


def get_storage_client():
    """
    Expose the raw S3 client for callers that need direct access
    (e.g. extraction workers that call ``client.get_object`` directly).
    """
    return _get_s3_client()
