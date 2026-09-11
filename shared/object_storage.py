#!/usr/bin/env python3
"""
Shared Object Storage Abstraction Layer

Provides a unified S3/object storage interface for all Mailyte services.
Supports content-addressable storage with SHA-256 deduplication, storage
tiering (hot/warm/cold), presigned URLs, and standard CRUD file operations.

Configuration is driven by environment variables:
    AWS_ACCESS_KEY_ID       - AWS access key
    AWS_SECRET_ACCESS_KEY   - AWS secret key
    AWS_DEFAULT_REGION      - AWS region (default: eu-west-2)
    AWS_BUCKET              - S3 bucket name (default: development-local-1)
    AWS_S3_PREFIX           - Key prefix for all objects (default: mailyte)
"""

import hashlib
import logging
import os
import threading
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# Storage tier to S3 StorageClass mapping
TIER_STORAGE_CLASS_MAP = {
    "hot": "STANDARD",
    "warm": "STANDARD_IA",
    "cold": "GLACIER",
}


class ObjectStorageClient:
    """
    S3-backed object storage client for the Mailyte email server.

    Wraps the boto3 S3 client and provides content-addressable storage,
    deduplication, storage tiering, and standard file operations with
    graceful error handling throughout.
    """

    def __init__(
        self,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str | None = None,
        bucket: str | None = None,
        prefix: str | None = None,
    ):
        """
        Initialize the object storage client.

        Args:
            access_key_id: AWS access key ID (falls back to AWS_ACCESS_KEY_ID env var).
            secret_access_key: AWS secret access key (falls back to AWS_SECRET_ACCESS_KEY env var).
            region: AWS region (falls back to AWS_DEFAULT_REGION env var, default: eu-west-2).
            bucket: S3 bucket name (falls back to AWS_BUCKET env var, default: development-local-1).
            prefix: Key prefix for all objects (falls back to AWS_S3_PREFIX env var, default: mailyte).
        """
        self.access_key_id = access_key_id or os.getenv("AWS_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or os.getenv("AWS_SECRET_ACCESS_KEY")
        self.region = region or os.getenv("AWS_DEFAULT_REGION", "eu-west-2")
        self.bucket = bucket or os.getenv("AWS_BUCKET", "development-local-1")
        self.prefix = prefix or os.getenv("AWS_S3_PREFIX", "mailyte")

        self._client = None
        self._lock = threading.Lock()

        logger.info(
            "ObjectStorageClient configured for bucket=%s region=%s prefix=%s",
            self.bucket,
            self.region,
            self.prefix,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def client(self):
        """Lazily initialise and return the boto3 S3 client (thread-safe)."""
        if self._client is None:
            with self._lock:
                if self._client is None:
                    session_kwargs: dict[str, Any] = {
                        "region_name": self.region,
                    }
                    if self.access_key_id and self.secret_access_key:
                        session_kwargs["aws_access_key_id"] = self.access_key_id
                        session_kwargs["aws_secret_access_key"] = self.secret_access_key

                    session = boto3.Session(**session_kwargs)
                    self._client = session.client("s3")
                    logger.info("boto3 S3 client initialised for region=%s", self.region)
        return self._client

    def _full_key(self, key: str) -> str:
        """Return the full S3 object key with the configured prefix."""
        return f"{self.prefix}/{key}" if self.prefix else key

    @staticmethod
    def _sha256(data: bytes) -> str:
        """Return the hex-encoded SHA-256 digest of *data*."""
        return hashlib.sha256(data).hexdigest()

    def _cas_key(self, content_hash: str) -> str:
        """Build the content-addressable storage key for a given SHA-256 hash.

        Layout: {prefix}/cas/{hash[0:2]}/{hash[2:4]}/{hash}
        """
        return self._full_key(f"cas/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}")

    # ------------------------------------------------------------------
    # Content-addressable storage
    # ------------------------------------------------------------------

    def store_content(self, data: bytes, content_type: str) -> str:
        """
        Store data using content-addressable storage.

        The object is keyed by its SHA-256 hash so that identical content is
        automatically deduplicated. If an object with the same hash already
        exists in the bucket, the upload is skipped and the existing key is
        returned.

        Args:
            data: Raw bytes to store.
            content_type: MIME content type (e.g. ``"application/octet-stream"``).

        Returns:
            The full S3 key under which the data is stored.

        Raises:
            ClientError: On unrecoverable S3 errors.
        """
        content_hash = self._sha256(data)
        key = self._cas_key(content_hash)

        # Deduplication check -- skip upload if object already exists
        if self.file_exists(key, use_raw_key=True):
            logger.info(
                "Duplicate content detected (hash=%s), returning existing key: %s",
                content_hash,
                key,
            )
            return key

        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            logger.info(
                "Stored content-addressable object: key=%s hash=%s size=%d",
                key,
                content_hash,
                len(data),
            )
            return key
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Failed to store content (hash=%s): %s",
                content_hash,
                exc,
            )
            raise

    # ------------------------------------------------------------------
    # Standard file operations
    # ------------------------------------------------------------------

    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> bool:
        """
        Upload a file to S3 under the configured prefix.

        Args:
            key: The object key (relative to the prefix).
            data: Raw bytes to upload.
            content_type: MIME type for the object.
            metadata: Optional dict of user-defined metadata.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        full_key = self._full_key(key)
        try:
            put_kwargs: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": full_key,
                "Body": data,
                "ContentType": content_type,
            }
            if metadata:
                put_kwargs["Metadata"] = metadata

            self.client.put_object(**put_kwargs)
            logger.info("Uploaded object: key=%s size=%d", full_key, len(data))
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to upload object key=%s: %s", full_key, exc)
            return False

    def download_file(self, key: str) -> bytes | None:
        """
        Download a file from S3.

        Args:
            key: The object key (relative to the prefix).

        Returns:
            The file contents as bytes, or ``None`` if the download fails.
        """
        full_key = self._full_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=full_key)
            data = response["Body"].read()
            logger.info("Downloaded object: key=%s size=%d", full_key, len(data))
            return data
        except self.client.exceptions.NoSuchKey:
            logger.warning("Object not found: key=%s", full_key)
            return None
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to download object key=%s: %s", full_key, exc)
            return None

    def delete_file(self, key: str) -> bool:
        """
        Delete a file from S3.

        Args:
            key: The object key (relative to the prefix).

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        full_key = self._full_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=full_key)
            logger.info("Deleted object: key=%s", full_key)
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to delete object key=%s: %s", full_key, exc)
            return False

    def list_files(self, prefix: str = "", max_keys: int = 1000) -> list[str]:
        """
        List object keys under a given prefix.

        Args:
            prefix: Additional prefix to filter by (appended to the configured prefix).
            max_keys: Maximum number of keys to return.

        Returns:
            A list of object keys (including the configured prefix).
        """
        full_prefix = self._full_key(prefix)
        keys: list[str] = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(
                Bucket=self.bucket,
                Prefix=full_prefix,
                PaginationConfig={"MaxItems": max_keys},
            )
            for page in page_iterator:
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            logger.info("Listed %d objects under prefix=%s", len(keys), full_prefix)
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to list objects under prefix=%s: %s", full_prefix, exc)
        return keys

    def get_presigned_url(
        self, key: str, expiration: int = 3600, http_method: str = "get_object"
    ) -> str | None:
        """
        Generate a presigned URL for an S3 object.

        Args:
            key: The object key (relative to the prefix).
            expiration: URL expiry time in seconds (default: 3600).
            http_method: The S3 client method to presign (default: ``get_object``).

        Returns:
            The presigned URL string, or ``None`` on failure.
        """
        full_key = self._full_key(key)
        try:
            url = self.client.generate_presigned_url(
                ClientMethod=http_method,
                Params={"Bucket": self.bucket, "Key": full_key},
                ExpiresIn=expiration,
            )
            logger.info(
                "Generated presigned URL for key=%s (expires in %ds)",
                full_key,
                expiration,
            )
            return url
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to generate presigned URL for key=%s: %s", full_key, exc)
            return None

    def file_exists(self, key: str, use_raw_key: bool = False) -> bool:
        """
        Check whether a file exists in S3.

        Args:
            key: The object key.
            use_raw_key: If ``True``, use the key as-is without prepending
                the configured prefix (useful for internal CAS lookups).

        Returns:
            ``True`` if the object exists, ``False`` otherwise.
        """
        full_key = key if use_raw_key else self._full_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=full_key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                return False
            logger.error("Error checking existence of key=%s: %s", full_key, exc)
            return False
        except BotoCoreError as exc:
            logger.error("Error checking existence of key=%s: %s", full_key, exc)
            return False

    # ------------------------------------------------------------------
    # Storage tiering
    # ------------------------------------------------------------------

    def store_with_tier(
        self,
        key: str,
        data: bytes,
        tier: str,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> bool:
        """
        Upload a file to S3 with a specific storage tier.

        Supported tiers:
            - ``"hot"``  -> ``STANDARD``
            - ``"warm"`` -> ``STANDARD_IA`` (Infrequent Access)
            - ``"cold"`` -> ``GLACIER``

        Args:
            key: The object key (relative to the prefix).
            data: Raw bytes to upload.
            tier: One of ``"hot"``, ``"warm"``, or ``"cold"``.
            content_type: MIME type for the object.
            metadata: Optional dict of user-defined metadata.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        storage_class = TIER_STORAGE_CLASS_MAP.get(tier)
        if storage_class is None:
            logger.error(
                "Invalid storage tier '%s'. Valid tiers: %s",
                tier,
                ", ".join(TIER_STORAGE_CLASS_MAP.keys()),
            )
            return False

        full_key = self._full_key(key)
        try:
            put_kwargs: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": full_key,
                "Body": data,
                "ContentType": content_type,
                "StorageClass": storage_class,
            }
            if metadata:
                put_kwargs["Metadata"] = metadata

            self.client.put_object(**put_kwargs)
            logger.info(
                "Uploaded tiered object: key=%s tier=%s storage_class=%s size=%d",
                full_key,
                tier,
                storage_class,
                len(data),
            )
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Failed to upload tiered object key=%s tier=%s: %s",
                full_key,
                tier,
                exc,
            )
            return False


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

_storage_client_instance: ObjectStorageClient | None = None
_singleton_lock = threading.Lock()


def get_storage_client() -> ObjectStorageClient:
    """
    Return a module-level singleton ``ObjectStorageClient``.

    The instance is created on first call using environment variable
    configuration and reused for all subsequent calls. Thread-safe.

    Returns:
        A shared ``ObjectStorageClient`` instance.
    """
    global _storage_client_instance

    if _storage_client_instance is None:
        with _singleton_lock:
            if _storage_client_instance is None:
                _storage_client_instance = ObjectStorageClient()
                logger.info("Created singleton ObjectStorageClient instance")
    return _storage_client_instance
