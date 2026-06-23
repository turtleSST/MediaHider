#!/usr/bin/env python3
"""Embed and extract opaque payload files in MP4/MKV containers with FFmpeg.

The host media streams are always stream-copied. MKV uses Matroska
attachments. MP4 uses FFmpeg-readable bin_data tracks; because common FFmpeg
MOV/MP4 muxers only expose 32 KiB of a raw private data sample reliably, this
script stores MP4 payloads as a manifest data track plus 32 KiB part tracks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MARKER = "hidden_payload"
DEFAULT_MIME_TYPE = "application/octet-stream"
DEFAULT_MP4_CHUNK_SIZE = 32 * 1024
MAX_MP4_CHUNK_SIZE = 32 * 1024
MANIFEST_MAGIC = "mediahider-v1"


class MediaHiderError(RuntimeError):
    """Base exception for user-facing failures."""


class FFmpegError(MediaHiderError):
    def __init__(
        self,
        cmd: list[str],
        returncode: int,
        stdout: str | bytes | None,
        stderr: str | bytes | None,
    ) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        stderr = self.stderr.decode("utf-8", "replace") if isinstance(self.stderr, bytes) else self.stderr
        stdout = self.stdout.decode("utf-8", "replace") if isinstance(self.stdout, bytes) else self.stdout
        parts = [
            f"Command failed with exit code {self.returncode}:",
            shlex.join(self.cmd),
        ]
        if stderr:
            parts.extend(["", "stderr:", stderr.rstrip()])
        if stdout:
            parts.extend(["", "stdout:", stdout.rstrip()])
        return "\n".join(parts)


@dataclass(frozen=True)
class StreamRef:
    index: int
    codec_type: str
    codec_name: str | None
    tags: dict[str, str]


def run_cmd(cmd: list[str], *, binary_stdout: bool = False) -> subprocess.CompletedProcess[Any]:
    stdout_mode: int | None = subprocess.PIPE
    text_mode = not binary_stdout
    try:
        proc = subprocess.run(
            cmd,
            stdout=stdout_mode,
            stderr=subprocess.PIPE,
            text=text_mode,
            check=False,
        )
    except OSError as exc:
        raise MediaHiderError(f"Failed to execute {cmd[0]!r}: {exc}") from exc
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_dir()))


def resolve_tool(name: str) -> str | None:
    executable_names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        executable_names.insert(0, f"{name}.exe")

    search_dirs = [app_dir(), bundled_dir()]
    for directory in search_dirs:
        for executable_name in executable_names:
            candidate = directory / executable_name
            if candidate.is_file():
                return str(candidate)

    return shutil.which(name)


def require_tool(name: str) -> str:
    resolved = resolve_tool(name)
    if resolved is None:
        raise MediaHiderError(
            f"Required executable not found: {name}. Put it in PATH, next to mediahider, "
            "or bundle it with PyInstaller."
        )
    return resolved


def require_input_file(path: Path, label: str) -> None:
    if not path.exists():
        raise MediaHiderError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise MediaHiderError(f"{label} is not a regular file: {path}")


def prepare_output_path(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise MediaHiderError(f"Output already exists; pass --force to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def temporary_sibling(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=path.suffix,
        dir=path.parent,
        delete=False,
    ) as handle:
        return Path(handle.name)


def replace_output(temp_path: Path, output_path: Path) -> None:
    os.replace(temp_path, output_path)


def remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ffprobe_json(path: Path) -> dict[str, Any]:
    cmd = [
        require_tool("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=format_name:format_tags:stream=index,codec_name,codec_type,codec_tag_string:stream_tags",
        "-of",
        "json",
        str(path),
    ]
    proc = run_cmd(cmd)
    return json.loads(proc.stdout)


def streams_from_probe(probe: dict[str, Any]) -> list[StreamRef]:
    streams: list[StreamRef] = []
    for item in probe.get("streams", []):
        tags = {str(k): str(v) for k, v in item.get("tags", {}).items()}
        streams.append(
            StreamRef(
                index=int(item["index"]),
                codec_type=str(item.get("codec_type", "")),
                codec_name=item.get("codec_name"),
                tags=tags,
            )
        )
    return streams


def stream_tag(stream: StreamRef, key: str) -> str:
    for tag_key, tag_value in stream.tags.items():
        if tag_key.lower() == key.lower():
            return tag_value
    return ""


def count_stream_type(path: Path, codec_type: str) -> int:
    return sum(1 for stream in streams_from_probe(ffprobe_json(path)) if stream.codec_type == codec_type)


def container_from_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mkv":
        return "mkv"
    if suffix == ".mp4":
        return "mp4"
    raise MediaHiderError(f"Unsupported output/container extension {suffix!r}; use .mkv or .mp4")


def embed(cover_video: Path, secret_file: Path, output_video: Path, args: argparse.Namespace) -> None:
    require_tool("ffmpeg")
    require_tool("ffprobe")
    require_input_file(cover_video, "Cover video")
    require_input_file(secret_file, "Secret file")
    if secret_file.stat().st_size == 0:
        raise MediaHiderError("Secret file is empty; refusing to create an empty payload stream")
    prepare_output_path(output_video, args.force)

    container = container_from_extension(output_video)
    if container == "mkv":
        embed_mkv(cover_video, secret_file, output_video, args)
    else:
        embed_mp4(cover_video, secret_file, output_video, args)


def embed_mkv(cover_video: Path, secret_file: Path, output_video: Path, args: argparse.Namespace) -> None:
    temp_output = temporary_sibling(output_video)
    attachment_offset = count_stream_type(cover_video, "attachment")
    secret_size = secret_file.stat().st_size
    secret_md5 = md5_file(secret_file)
    metadata_prefix = f"-metadata:s:t:{attachment_offset}"
    cmd = [
        require_tool("ffmpeg"),
        "-hide_banner",
        "-y",
        "-i",
        str(cover_video),
        "-map",
        "0",
        "-c",
        "copy",
        "-attach",
        str(secret_file),
        metadata_prefix,
        f"filename={secret_file.name}",
        metadata_prefix,
        f"mimetype={args.mime_type}",
        metadata_prefix,
        f"title={args.marker}",
        metadata_prefix,
        f"comment=mediahider md5={secret_md5} size={secret_size}",
        str(temp_output),
    ]
    try:
        run_cmd(cmd)
        replace_output(temp_output, output_video)
    except Exception:
        remove_quietly(temp_output)
        raise
    print(f"Embedded MKV attachment: {output_video}")
    print(f"Payload size={secret_size} md5={secret_md5}")


def embed_mp4(cover_video: Path, secret_file: Path, output_video: Path, args: argparse.Namespace) -> None:
    chunk_size = args.mp4_chunk_size
    if chunk_size < 1 or chunk_size > MAX_MP4_CHUNK_SIZE:
        raise MediaHiderError(f"--mp4-chunk-size must be between 1 and {MAX_MP4_CHUNK_SIZE}")

    secret_size = secret_file.stat().st_size
    secret_md5 = md5_file(secret_file)
    existing_data_streams = count_stream_type(cover_video, "data")
    temp_output = temporary_sibling(output_video)

    with tempfile.TemporaryDirectory(prefix="mediahider-mp4-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        chunks = split_file(secret_file, temp_dir, chunk_size)
        manifest = {
            "magic": MANIFEST_MAGIC,
            "container": "mp4",
            "marker": args.marker,
            "original_name": secret_file.name,
            "mime_type": args.mime_type,
            "payload_size": secret_size,
            "payload_md5": secret_md5,
            "chunk_size": chunk_size,
            "parts": len(chunks),
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_bytes = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(manifest_bytes) > MAX_MP4_CHUNK_SIZE:
            raise MediaHiderError("Internal manifest exceeded one MP4 data sample")
        manifest_path.write_bytes(manifest_bytes)

        ts_inputs = [wrap_data_file_as_ts(manifest_path, temp_dir / "manifest.ts")]
        for idx, chunk_path in enumerate(chunks):
            ts_inputs.append(wrap_data_file_as_ts(chunk_path, temp_dir / f"part_{idx:08d}.ts"))

        cmd = [require_tool("ffmpeg"), "-hide_banner", "-y", "-i", str(cover_video)]
        for ts_path in ts_inputs:
            cmd.extend(["-i", str(ts_path)])
        cmd.extend(["-map", "0"])
        for input_idx in range(1, len(ts_inputs) + 1):
            cmd.extend(["-map", f"{input_idx}:0"])
        cmd.extend(["-c", "copy"])

        manifest_data_pos = existing_data_streams
        cmd.extend(
            [
                f"-metadata:s:d:{manifest_data_pos}",
                f"handler_name={args.marker}_manifest",
            ]
        )
        total = len(chunks)
        for part_idx in range(total):
            data_pos = existing_data_streams + 1 + part_idx
            handler = f"{args.marker}_part_{part_idx:08d}_of_{total:08d}"
            cmd.extend([f"-metadata:s:d:{data_pos}", f"handler_name={handler}"])
        cmd.append(str(temp_output))

        try:
            run_cmd(cmd)
            replace_output(temp_output, output_video)
        except Exception:
            remove_quietly(temp_output)
            raise

    print(f"Embedded MP4 data tracks: {output_video}")
    print(f"Payload size={secret_size} md5={secret_md5} parts={len(chunks)}")


def split_file(source: Path, temp_dir: Path, chunk_size: int) -> list[Path]:
    chunks: list[Path] = []
    with source.open("rb") as src:
        part_idx = 0
        while True:
            data = src.read(chunk_size)
            if not data:
                break
            chunk_path = temp_dir / f"chunk_{part_idx:08d}.bin"
            chunk_path.write_bytes(data)
            chunks.append(chunk_path)
            part_idx += 1
    return chunks


def wrap_data_file_as_ts(input_path: Path, output_ts: Path) -> Path:
    packet_size = input_path.stat().st_size
    if packet_size < 1 or packet_size > MAX_MP4_CHUNK_SIZE:
        raise MediaHiderError(f"MP4 data part size must be 1..{MAX_MP4_CHUNK_SIZE}: {input_path}")
    cmd = [
        require_tool("ffmpeg"),
        "-hide_banner",
        "-y",
        "-f",
        "data",
        "-raw_packet_size",
        str(packet_size),
        "-i",
        str(input_path),
        "-map",
        "0:0",
        "-c",
        "copy",
        "-f",
        "mpegts",
        str(output_ts),
    ]
    run_cmd(cmd)
    return output_ts


def extract(stego_video: Path, output_file: Path, args: argparse.Namespace) -> None:
    require_tool("ffmpeg")
    require_tool("ffprobe")
    require_input_file(stego_video, "Stego video")
    prepare_output_path(output_file, args.force)

    container = container_from_extension(stego_video)
    if container == "mkv":
        extract_mkv(stego_video, output_file, args)
    else:
        extract_mp4(stego_video, output_file, args)

    extracted_md5 = md5_file(output_file)
    extracted_size = output_file.stat().st_size
    print(f"Extracted payload: {output_file}")
    print(f"Payload size={extracted_size} md5={extracted_md5}")
    if args.verify_against:
        require_input_file(args.verify_against, "Verification source")
        original_md5 = md5_file(args.verify_against)
        if original_md5 != extracted_md5:
            raise MediaHiderError(
                f"MD5 verification failed: original={original_md5} extracted={extracted_md5}"
            )
        print("MD5 verification against source file passed")


def extract_mkv(stego_video: Path, output_file: Path, args: argparse.Namespace) -> None:
    streams = streams_from_probe(ffprobe_json(stego_video))
    attachments = [stream for stream in streams if stream.codec_type == "attachment"]
    target = select_attachment(attachments, args.marker, args.stream_index)
    attachment_ordinal = attachments.index(target)
    temp_output = temporary_sibling(output_file)
    cmd = [
        require_tool("ffmpeg"),
        "-hide_banner",
        "-y",
        f"-dump_attachment:t:{attachment_ordinal}",
        str(temp_output),
        "-i",
        str(stego_video),
        "-map",
        "0",
        "-map",
        "-0:t?",
        "-c",
        "copy",
        "-f",
        "null",
        "-",
    ]
    try:
        try:
            run_cmd(cmd)
        except FFmpegError:
            # Some FFmpeg builds dump attachments before failing if no null
            # output stream can be mapped. Treat an extracted temp file as the
            # authoritative result.
            if not temp_output.exists():
                raise
        replace_output(temp_output, output_file)
    except Exception:
        remove_quietly(temp_output)
        raise


def select_attachment(attachments: list[StreamRef], marker: str, stream_index: int | None) -> StreamRef:
    if stream_index is not None:
        for stream in attachments:
            if stream.index == stream_index:
                return stream
        raise MediaHiderError(f"No attachment stream with index {stream_index}")

    marked = [
        stream
        for stream in attachments
        if stream_tag(stream, "title") == marker or stream_tag(stream, "filename").startswith(marker)
    ]
    if len(marked) == 1:
        return marked[0]
    if not marked and len(attachments) == 1:
        return attachments[0]
    if not marked:
        raise MediaHiderError(f"No MKV attachment marked as {marker!r}; pass --stream-index")
    raise MediaHiderError(f"Multiple MKV attachments marked as {marker!r}; pass --stream-index")


def extract_mp4(stego_video: Path, output_file: Path, args: argparse.Namespace) -> None:
    streams = streams_from_probe(ffprobe_json(stego_video))
    data_streams = [stream for stream in streams if stream.codec_type == "data"]

    if args.stream_index is not None:
        extract_single_data_stream(stego_video, args.stream_index, output_file)
        return

    manifest_stream = find_mp4_manifest_stream(data_streams, args.marker)
    if manifest_stream:
        extract_mp4_chunked_payload(stego_video, output_file, data_streams, manifest_stream, args.marker)
        return

    target = select_single_data_stream(data_streams, args.marker)
    extract_single_data_stream(stego_video, target.index, output_file)


def find_mp4_manifest_stream(data_streams: list[StreamRef], marker: str) -> StreamRef | None:
    expected = f"{marker}_manifest"
    matches = [stream for stream in data_streams if stream_tag(stream, "handler_name") == expected]
    if len(matches) > 1:
        raise MediaHiderError(f"Multiple MP4 manifest data streams found for marker {marker!r}")
    return matches[0] if matches else None


def extract_mp4_chunked_payload(
    stego_video: Path,
    output_file: Path,
    data_streams: list[StreamRef],
    manifest_stream: StreamRef,
    marker: str,
) -> None:
    temp_output = temporary_sibling(output_file)
    try:
        with tempfile.TemporaryDirectory(prefix="mediahider-extract-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            manifest_bin = temp_dir / "manifest.json"
            extract_data_stream_to_path(stego_video, manifest_stream.index, manifest_bin)
            manifest = json.loads(manifest_bin.read_text("utf-8"))
            validate_manifest(manifest, marker)

            part_streams = collect_mp4_part_streams(data_streams, marker, int(manifest["parts"]))
            with temp_output.open("wb") as output_handle:
                for part_idx, stream in part_streams:
                    part_path = temp_dir / f"part_{part_idx:08d}.bin"
                    extract_data_stream_to_path(stego_video, stream.index, part_path)
                    with part_path.open("rb") as part_handle:
                        shutil.copyfileobj(part_handle, output_handle)

            expected_size = int(manifest["payload_size"])
            expected_md5 = str(manifest["payload_md5"])
            actual_size = temp_output.stat().st_size
            actual_md5 = md5_file(temp_output)
            if actual_size != expected_size:
                raise MediaHiderError(f"MP4 payload size mismatch: expected={expected_size} actual={actual_size}")
            if actual_md5 != expected_md5:
                raise MediaHiderError(f"MP4 payload md5 mismatch: expected={expected_md5} actual={actual_md5}")
            replace_output(temp_output, output_file)
    except Exception:
        remove_quietly(temp_output)
        raise


def validate_manifest(manifest: dict[str, Any], marker: str) -> None:
    if manifest.get("magic") != MANIFEST_MAGIC:
        raise MediaHiderError("MP4 manifest magic mismatch")
    if manifest.get("marker") != marker:
        raise MediaHiderError(f"MP4 manifest marker mismatch: {manifest.get('marker')!r}")
    if int(manifest.get("parts", 0)) < 1:
        raise MediaHiderError("MP4 manifest has no payload parts")
    if int(manifest.get("chunk_size", 0)) > MAX_MP4_CHUNK_SIZE:
        raise MediaHiderError("MP4 manifest chunk size is not supported by this extractor")


def collect_mp4_part_streams(
    data_streams: list[StreamRef],
    marker: str,
    expected_parts: int,
) -> list[tuple[int, StreamRef]]:
    pattern = re.compile(rf"^{re.escape(marker)}_part_(\d{{8}})_of_(\d{{8}})$")
    collected: dict[int, StreamRef] = {}
    for stream in data_streams:
        match = pattern.match(stream_tag(stream, "handler_name"))
        if not match:
            continue
        part_idx = int(match.group(1))
        total = int(match.group(2))
        if total != expected_parts:
            raise MediaHiderError(f"MP4 part stream total mismatch at stream {stream.index}")
        if part_idx in collected:
            raise MediaHiderError(f"Duplicate MP4 payload part index {part_idx}")
        collected[part_idx] = stream

    missing = [idx for idx in range(expected_parts) if idx not in collected]
    if missing:
        sample = ", ".join(str(idx) for idx in missing[:10])
        raise MediaHiderError(f"Missing MP4 payload part streams: {sample}")
    return [(idx, collected[idx]) for idx in range(expected_parts)]


def select_single_data_stream(data_streams: list[StreamRef], marker: str) -> StreamRef:
    marked = [
        stream
        for stream in data_streams
        if stream_tag(stream, "handler_name") == marker or stream_tag(stream, "handler_name").startswith(marker)
    ]
    if len(marked) == 1:
        return marked[0]
    if not marked and len(data_streams) == 1:
        return data_streams[0]
    if not marked:
        raise MediaHiderError(f"No MP4 data stream marked as {marker!r}; pass --stream-index")
    raise MediaHiderError(f"Multiple MP4 data streams match {marker!r}; pass --stream-index")


def extract_single_data_stream(stego_video: Path, stream_index: int, output_file: Path) -> None:
    temp_output = temporary_sibling(output_file)
    try:
        extract_data_stream_to_path(stego_video, stream_index, temp_output)
        replace_output(temp_output, output_file)
    except Exception:
        remove_quietly(temp_output)
        raise


def extract_data_stream_to_path(stego_video: Path, stream_index: int, output_path: Path) -> None:
    cmd = [
        require_tool("ffmpeg"),
        "-hide_banner",
        "-y",
        "-i",
        str(stego_video),
        "-map",
        f"0:{stream_index}",
        "-c",
        "copy",
        "-f",
        "data",
        str(output_path),
    ]
    run_cmd(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hide and recover arbitrary payload files in MP4/MKV containers using FFmpeg stream copy.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    embed_parser = subparsers.add_parser("embed", help="Embed a payload into a video container")
    embed_parser.add_argument("cover_video_path", type=Path)
    embed_parser.add_argument("secret_file_path", type=Path)
    embed_parser.add_argument("output_video_path", type=Path)
    embed_parser.add_argument("-f", "--force", action="store_true", help="Overwrite output if it exists")
    embed_parser.add_argument("--marker", default=DEFAULT_MARKER, help="Payload stream marker")
    embed_parser.add_argument("--mime-type", default=DEFAULT_MIME_TYPE, help="Payload MIME type metadata")
    embed_parser.add_argument(
        "--mp4-chunk-size",
        type=int,
        default=DEFAULT_MP4_CHUNK_SIZE,
        help=f"MP4 payload data track chunk size, max {MAX_MP4_CHUNK_SIZE}",
    )

    extract_parser = subparsers.add_parser("extract", help="Extract a hidden payload from a video container")
    extract_parser.add_argument("stego_video_path", type=Path)
    extract_parser.add_argument("output_file_path", type=Path)
    extract_parser.add_argument("-f", "--force", action="store_true", help="Overwrite output if it exists")
    extract_parser.add_argument("--marker", default=DEFAULT_MARKER, help="Payload stream marker")
    extract_parser.add_argument("--stream-index", type=int, help="Explicit stream index to extract")
    extract_parser.add_argument("--verify-against", type=Path, help="Compare extracted MD5 with this file")

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "embed":
            embed(args.cover_video_path, args.secret_file_path, args.output_video_path, args)
        elif args.command == "extract":
            extract(args.stego_video_path, args.output_file_path, args)
        else:
            parser.error(f"Unknown command: {args.command}")
    except MediaHiderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
