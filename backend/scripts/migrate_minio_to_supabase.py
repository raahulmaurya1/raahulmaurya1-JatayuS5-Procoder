"""
scripts/migrate_minio_to_supabase.py
======================================
One-time migration script: copies all objects from local MinIO
into Supabase Storage buckets.

Usage (from the backend directory):
    python -m scripts.migrate_minio_to_supabase

Prerequisites:
    - Local MinIO must still be running (docker-compose up minio)
    - .env must already have SUPABASE_STORAGE_* variables set
    - pip install minio boto3   (minio is still installed at this point)

After a successful run:
    1. Verify objects in Supabase Dashboard → Storage
    2. Run the file_url backfill query (see bottom of this file)
    3. Then remove `minio` from requirements.txt
"""

from __future__ import annotations

import os
import sys

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

import boto3
from botocore.config import Config as BotoConfig

# --- Attempt to import minio; it may already be removed ---
try:
    from minio import Minio
except ImportError:
    print("ERROR: 'minio' package is not installed. Install it temporarily:")
    print("  pip install minio")
    sys.exit(1)


MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

SUPABASE_ENDPOINT = os.getenv("SUPABASE_STORAGE_ENDPOINT")
SUPABASE_ACCESS_KEY = os.getenv("SUPABASE_STORAGE_ACCESS_KEY")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_STORAGE_SECRET_KEY")
SUPABASE_REGION = os.getenv("SUPABASE_STORAGE_REGION", "auto")

BUCKETS_TO_MIGRATE = ["temp", "verified"]


def main() -> None:
    # ── Validate env ─────────────────────────────────────────────────────
    if not SUPABASE_ENDPOINT:
        print("ERROR: SUPABASE_STORAGE_ENDPOINT is not set in .env")
        sys.exit(1)
    if not SUPABASE_ACCESS_KEY or not SUPABASE_SECRET_KEY:
        print("ERROR: SUPABASE_STORAGE_ACCESS_KEY / SECRET_KEY is not set in .env")
        sys.exit(1)

    # ── Init clients ─────────────────────────────────────────────────────
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )

    supabase_s3 = boto3.client(
        "s3",
        endpoint_url=SUPABASE_ENDPOINT,
        aws_access_key_id=SUPABASE_ACCESS_KEY,
        aws_secret_access_key=SUPABASE_SECRET_KEY,
        region_name=SUPABASE_REGION,
        config=BotoConfig(signature_version="s3v4"),
    )

    total_migrated = 0
    total_errors = 0

    for bucket in BUCKETS_TO_MIGRATE:
        if not minio_client.bucket_exists(bucket):
            print(f"SKIP: MinIO bucket '{bucket}' does not exist.")
            continue

        print(f"\n── Migrating bucket: {bucket} ──")
        objects = minio_client.list_objects(bucket, recursive=True)

        for obj in objects:
            try:
                response = minio_client.get_object(bucket, obj.object_name)
                data = response.read()
                response.close()
                response.release_conn()

                supabase_s3.put_object(
                    Bucket=bucket,
                    Key=obj.object_name,
                    Body=data,
                )
                total_migrated += 1
                print(f"  ✓ {bucket}/{obj.object_name} ({len(data)} bytes)")

            except Exception as exc:
                total_errors += 1
                print(f"  ✗ {bucket}/{obj.object_name} — ERROR: {exc}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Migration complete.  Migrated: {total_migrated}  Errors: {total_errors}")
    print(f"{'='*60}")

    if total_errors == 0 and total_migrated > 0:
        print("\nNext step — backfill file_url values in the database:")
        print("""
    -- Run this SQL in Supabase SQL Editor:
    UPDATE user_documents
    SET file_url = file_url   -- URLs are already in 'bucket/key' format, no change needed
    WHERE file_url NOT LIKE 'http%';

    -- If any URLs were stored with the full MinIO host prefix:
    UPDATE user_documents
    SET file_url = REPLACE(file_url, 'http://localhost:9000/', '')
    WHERE file_url LIKE 'http://localhost:9000/%';
        """)


if __name__ == "__main__":
    main()
