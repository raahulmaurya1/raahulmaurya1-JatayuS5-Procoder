"""
app/storage/minio.py
=====================
BACKWARD-COMPATIBILITY SHIM — Migrated to Supabase Storage.

This module re-exports every function from ``supabase_storage`` under
the original names so that existing imports across the codebase
(``from app.storage.minio import ...``) continue to work without
modification.

New code should import directly from ``app.storage.supabase_storage``.

NOTE: The ``minio_client`` attribute used by some Celery workers is
replaced by ``storage_client`` — a ``boto3`` S3 client pointing at
Supabase Storage.
"""

from app.storage.supabase_storage import (        # noqa: F401  — re-exports
    save_to_minio,
    move_minio_object,
    get_minio_file,
    get_storage_object_bytes,
    get_storage_client,
)

# Legacy alias: code that used ``minio_client.get_object(...)`` directly
# now gets an S3 client.  The calling code in extraction.py and
# video_utils.py is updated to use get_storage_client() instead.
minio_client = None   # Sentinel — forces callers to migrate to get_storage_client()
