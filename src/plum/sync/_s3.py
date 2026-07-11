from __future__ import annotations

from pathlib import Path
from typing import Iterator

from plum.pipeline import MANIFEST_FILE
from plum.sync._backend import BACKENDS, SyncBackend


@BACKENDS.register
class S3Backend(SyncBackend):
    id = "s3"

    class Options(SyncBackend.Options):
        bucket: str
        prefix: str = ""

    _client_cache = None

    def _client(self):
        if self._client_cache is None:
            import boto3

            self._client_cache = boto3.client("s3")
        return self._client_cache

    def _base(self) -> str:
        return f"{self.options.prefix}/" if self.options.prefix else ""

    def _key(self, relpath: str) -> str:
        return self._base() + relpath

    def list_runs(self) -> list[str]:
        import posixpath

        base = self._base()
        runs = []
        for key in self._iter_keys(base):
            relpath = key[len(base):]
            if posixpath.basename(relpath) == MANIFEST_FILE:
                runs.append(posixpath.dirname(relpath))
        return runs

    def list_files(self, run: str) -> list[str]:
        base = self._base()
        return [key[len(base):] for key in self._iter_keys(self._key(run) + "/")]

    def read_bytes(self, relpath: str) -> bytes:
        obj = self._client().get_object(Bucket=self.options.bucket, Key=self._key(relpath))
        return obj["Body"].read()

    def upload(self, src: Path, relpath: str) -> None:
        # upload_file streams via multipart, so artifact size is unbounded
        self._client().upload_file(str(src), self.options.bucket, self._key(relpath))

    def download(self, relpath: str, dest: Path) -> None:
        self._client().download_file(self.options.bucket, self._key(relpath), str(dest))

    def delete_run(self, run: str) -> None:
        client = self._client()
        for key in self._iter_keys(self._key(run) + "/"):
            client.delete_object(Bucket=self.options.bucket, Key=key)

    def _iter_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.options.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]
