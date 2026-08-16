import logging
from pathlib import Path

from invoicing.config import settings

logger = logging.getLogger(__name__)


class ObjectStorage:
    def put(self, key: str, data: bytes, content_type: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def get(self, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError


def _safe_path(key: str) -> str:
    """规范化 key：拒绝绝对路径与 .. 越界，保留子目录结构。"""
    normalized = key.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if ":" in parts[0] or ".." in parts:
        raise ValueError(f"非法路径: {key}")
    return normalized


class LocalFileStorage(ObjectStorage):
    def __init__(self, root: str):
        self.root = Path(root)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        safe = _safe_path(key)
        target = self.root / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return safe

    def get(self, key: str) -> bytes:
        safe = _safe_path(key)
        target = self.root / safe
        if not target.is_file():
            raise FileNotFoundError(f"对象不存在: {key}")
        return target.read_bytes()

    def delete(self, key: str) -> None:
        safe = _safe_path(key)
        target = self.root / safe
        try:
            target.unlink()
        except FileNotFoundError:
            pass  # 幂等：对象已不存在视为删除成功


class S3Storage(ObjectStorage):
    """S3/MinIO 后端（生产对齐用；开发默认 local，不触发 boto3 依赖）。"""

    def __init__(self, endpoint, access_key, secret_key, bucket, secure):
        import boto3
        from botocore.client import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"{'https' if secure else 'http'}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "local":
            _storage = LocalFileStorage(root=settings.storage_root)
        elif settings.storage_backend == "s3":
            _storage = S3Storage(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                bucket=settings.minio_bucket,
                secure=settings.minio_secure,
            )
        else:
            raise ValueError(f"未知存储后端: {settings.storage_backend}")
    return _storage
