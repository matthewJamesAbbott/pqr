from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pqr.common import asyncio, json, os, pd


class Reader(ABC):
    """Bridge interface for file-specific data loading."""

    @abstractmethod
    def get_row_range(self, start: int, end: int) -> pd.DataFrame: ...

    @abstractmethod
    async def get_row_range_async(self, start: int, end: int) -> pd.DataFrame: ...

    @property
    @abstractmethod
    def num_rows(self) -> int: ...

    @property
    @abstractmethod
    def columns(self) -> list[str]: ...

    @property
    @abstractmethod
    def dtypes(self) -> dict[str, str]: ...

    @abstractmethod
    def close(self) -> None: ...


class ParquetReader(Reader):
    """Handles PyArrow/Parquet disk IO with a row buffer for large files."""

    BUFFER_SIZE = 64 * 1024  # prefetch 64K rows per buffer load

    def __init__(self, path: str) -> None:
        from pqr.common import pq
        self._path = path
        self._pq_file = pq.ParquetFile(path)
        self._metadata = self._pq_file.metadata
        self._buffer_df: pd.DataFrame | None = None
        self._buffer_start: int = -1
        self._buffer_end: int = -1

    @property
    def num_rows(self) -> int:
        return self._metadata.num_rows

    @property
    def columns(self) -> list[str]:
        return self._pq_file.schema_arrow.names

    @property
    def dtypes(self) -> dict[str, str]:
        return {name: str(t) for name, t in zip(self.columns, self._pq_file.schema_arrow.types)}

    def _fill_buffer(self, target_start: int, target_end: int) -> None:
        col_names = self.columns
        rg = self._metadata
        read_start = max(0, target_start - self.BUFFER_SIZE)
        prefetch = (target_end - target_start) * 2
        read_end = min(self.num_rows, target_end + prefetch)
        if self._buffer_df is not None and self._buffer_start <= target_start and self._buffer_end >= target_end:
            return
        chunks = []
        row_offset = 0
        for i in range(rg.num_row_groups):
            rg_size = rg.row_group(i).num_rows
            rg_start = row_offset
            rg_end = row_offset + rg_size
            if rg_end <= read_start:
                row_offset = rg_end
                continue
            if rg_start >= read_end:
                break
            local_start = max(0, read_start - row_offset)
            local_end = min(rg_size, read_end - row_offset)
            table = self._pq_file.read_row_group(i, columns=col_names, use_threads=True)
            table = table.slice(local_start, local_end - local_start)
            chunks.append(table.to_pandas())
            row_offset = rg_end
        if chunks:
            self._buffer_df = pd.concat(chunks, ignore_index=True)
        else:
            self._buffer_df = pd.DataFrame(columns=col_names)
        self._buffer_start = read_start
        self._buffer_end = read_end

    def get_row_range(self, start: int, end: int) -> pd.DataFrame:
        self._fill_buffer(start, end)
        buf = self._buffer_df
        if buf is None or buf.empty:
            return pd.DataFrame(columns=self.columns)
        local_start = start - self._buffer_start
        local_end = end - self._buffer_start
        if local_start >= len(buf) or local_end <= 0:
            return pd.DataFrame(columns=self.columns)
        local_start = max(0, local_start)
        local_end = min(len(buf), local_end)
        return buf.iloc[local_start:local_end].reset_index(drop=True)

    async def get_row_range_async(self, start: int, end: int) -> pd.DataFrame:
        return self.get_row_range(start, end)

    def close(self) -> None:
        self._buffer_df = None
        self._buffer_start = -1
        self._buffer_end = -1


class JsonlReader(Reader):
    """Streaming reader for .zst compressed JSONL files."""

    SAMPLE_COMPRESSED_BYTES = 100 * 1024 * 1024
    CHUNK_ROWS = 3000

    def __init__(self, path: str) -> None:
        try:
            import zstandard as zstd
        except ImportError:
            from pqr.common import sys
            print("Error: zstandard module not installed. Install with: pip install zstandard", file=sys.stderr)
            raise

        self._path = path
        self._file_size = os.path.getsize(path)
        self._decompressor = zstd.ZstdDecompressor()
        self._stream_reader = None
        self._stream_open: bool = False
        self._num_rows: int = 0
        self._all_columns: list[str] = []
        self._dtypes: dict[str, str] = {}
        self._rows_per_mb: float = 0.0
        self._cache: list[dict] = []
        self._cache_start: int = 0
        self._cache_end: int = 0
        self._eof: bool = False

    @property
    def num_rows(self) -> int:
        return self._num_rows

    @property
    def columns(self) -> list[str]:
        return self._all_columns

    @property
    def dtypes(self) -> dict[str, str]:
        return self._dtypes

    def _ensure_indexed(self) -> None:
        if self._num_rows > 0:
            return
        self._sample_index()

    def _sample_index(self) -> None:
        with open(self._path, "rb") as f:
            sr = self._decompressor.stream_reader(f)
            raw = sr.read(self.SAMPLE_COMPRESSED_BYTES)
            sr.close()
        text = raw.decode("utf-8", errors="replace")
        non_empty = [l for l in text.split("\n") if l.strip()]
        sample_records = []
        for line in non_empty[:5]:
            try:
                sample_records.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        self._rows_per_mb = len(non_empty) / (self.SAMPLE_COMPRESSED_BYTES / 1024 / 1024)
        sample_file_mb = self.SAMPLE_COMPRESSED_BYTES / 1024 / 1024
        file_mb = self._file_size / 1024 / 1024
        if file_mb <= sample_file_mb:
            self._num_rows = len(non_empty)
        else:
            self._num_rows = int(self._rows_per_mb * file_mb)
        self._all_columns = []
        self._dtypes = {}
        for rec in sample_records:
            self._flatten_record(rec, self._all_columns, self._dtypes)
        self._cache = []
        for line in non_empty[:self.CHUNK_ROWS]:
            try:
                self._cache.append(self._flatten_to_dict(json.loads(line)))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        self._cache_start = 0
        self._cache_end = len(self._cache)

    def _open_stream(self) -> None:
        if self._stream_reader is not None:
            try:
                self._stream_reader.close()
            except Exception:
                pass
        self._file = open(self._path, "rb")
        self._stream_reader = self._decompressor.stream_reader(self._file)
        self._stream_open = True

    def _fill_cache_from(self, target_row: int) -> None:
        if target_row < self._cache_end and target_row >= self._cache_start:
            return
        need_reopen = target_row < self._cache_start or not self._cache or not self._stream_open
        if need_reopen:
            self._open_stream()
            self._cache = []
            self._cache_start = 0
            self._cache_end = 0
            self._eof = False
        produced = 0
        buffer = b""
        target = target_row - self._cache_start + self.CHUNK_ROWS
        while produced < target and not self._eof:
            chunk = self._stream_reader.read(1048576)
            if not chunk:
                self._eof = True
                if buffer.strip():
                    try:
                        self._cache.append(self._flatten_to_dict(json.loads(buffer.strip().decode("utf-8"))))
                        produced += 1
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    produced += 1
                    try:
                        self._cache.append(self._flatten_to_dict(json.loads(line.decode("utf-8"))))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
        self._cache_end = self._cache_start + len(self._cache)

    async def fill_cache_from_async(self, target_row: int) -> None:
        if target_row < self._cache_end and target_row >= self._cache_start:
            return
        need_reopen = target_row < self._cache_start or not self._cache or not self._stream_open
        if need_reopen:
            self._open_stream()
            self._cache = []
            self._cache_start = 0
            self._cache_end = 0
            self._eof = False
        produced = 0
        buffer = b""
        target = target_row - self._cache_start + self.CHUNK_ROWS
        while produced < target and not self._eof:
            chunk = self._stream_reader.read(1048576)
            if not chunk:
                self._eof = True
                if buffer.strip():
                    try:
                        self._cache.append(self._flatten_to_dict(json.loads(buffer.strip().decode("utf-8"))))
                        produced += 1
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    produced += 1
                    try:
                        self._cache.append(self._flatten_to_dict(json.loads(line.decode("utf-8"))))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                if produced % 5000 == 0:
                    await asyncio.sleep(0)
        self._cache_end = self._cache_start + len(self._cache)

    def get_row_range(self, start: int, end: int) -> pd.DataFrame:
        self._ensure_indexed()
        if start >= self._num_rows:
            return pd.DataFrame(columns=self._all_columns)
        end = min(end, self._num_rows)
        n_rows = end - start
        self._fill_cache_from(start)
        idx_start = start - self._cache_start
        idx_end = end - self._cache_start
        records = self._cache[idx_start:idx_end]
        return pd.DataFrame(records[:n_rows], columns=self._all_columns)

    async def get_row_range_async(self, start: int, end: int) -> pd.DataFrame:
        self._ensure_indexed()
        if start >= self._num_rows:
            return pd.DataFrame(columns=self._all_columns)
        end = min(end, self._num_rows)
        n_rows = end - start
        await self.fill_cache_from_async(start)
        idx_start = start - self._cache_start
        idx_end = end - self._cache_start
        records = self._cache[idx_start:idx_end]
        return pd.DataFrame(records[:n_rows], columns=self._all_columns)

    @staticmethod
    def _flatten_record(rec: dict, columns: list[str], dtypes: dict[str, str]) -> None:
        def _walk(obj, prefix: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, list):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "list"
            elif isinstance(obj, bool):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "bool"
            elif isinstance(obj, int):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "int64"
            elif isinstance(obj, float):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "float64"
            elif obj is None:
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "object"
            elif isinstance(obj, str):
                if prefix not in columns:
                    columns.append(prefix)
                if prefix not in dtypes:
                    dtypes[prefix] = "object"
        _walk(rec, "")

    @staticmethod
    def _flatten_to_dict(rec: dict) -> dict:
        result = {}
        def _walk(obj, prefix: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, list):
                result[prefix] = json.dumps(obj)
            else:
                result[prefix] = obj
        _walk(rec, "")
        return result

    def close(self) -> None:
        try:
            if self._stream_reader:
                self._stream_reader.close()
        except Exception:
            pass
        try:
            if hasattr(self, "_file"):
                self._file.close()
        except Exception:
            pass


class ReaderFactory:
    """Factory to create the appropriate Reader based on file extension."""

    @staticmethod
    def create(path: str) -> Reader:
        if path.endswith(".zst") or path.endswith(".zst."):
            return JsonlReader(path)
        return ParquetReader(path)
