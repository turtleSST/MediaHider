# MediaHider

MediaHider 是一个基于 Python 3 和 FFmpeg CLI 的命令行工具，用于将任意二进制文件嵌入到 MP4 或 MKV 视频容器中，并在需要时完整提取出来。

本工具采用容器层复用/解复用方式工作：

- 宿主视频、音频流始终使用 `-c copy` 复制，不重新编码。
- MKV 使用 Matroska 原生附件机制保存 payload。
- MP4 使用 FFmpeg 可识别的私有 `bin_data` 数据轨道保存 payload。

> MediaHider 不提供加密能力。若 payload 涉及敏感内容，请先自行加密，再进行嵌入。

## 功能特性

- 支持将任意普通文件嵌入 `.mp4` 或 `.mkv`。
- 支持将隐藏 payload 字节级完整提取。
- 不重编码宿主音视频，避免画质/音质损失。
- 通过 metadata marker 自动识别隐藏流。
- MP4 payload 带 manifest，提取时自动校验大小和 MD5。
- 支持提取后与原始文件进行 MD5 校验。
- 提供 Windows PyInstaller 打包脚本，可生成独立 `.exe`。

## 环境要求

- 推荐 Python 3.10+
- 需要可调用 `ffmpeg` 和 `ffprobe`

FFmpeg/FFprobe 可以放在以下任一位置：

- 系统 `PATH`
- `mediahider.py` 或 `mediahider.exe` 同目录
- PyInstaller 打包时内置到 exe 中

检查 FFmpeg：

```bash
ffmpeg -version
ffprobe -version
```

## 快速开始

嵌入到 MP4：

```bash
python3 mediahider.py embed cover.mp4 secret.bin output.mp4
```

嵌入到 MKV：

```bash
python3 mediahider.py embed cover.mp4 secret.bin output.mkv
```

从 MP4 提取：

```bash
python3 mediahider.py extract output.mp4 recovered.bin
```

从 MKV 提取：

```bash
python3 mediahider.py extract output.mkv recovered.bin
```

覆盖已有输出文件：

```bash
python3 mediahider.py embed -f cover.mp4 secret.bin output.mp4
python3 mediahider.py extract -f output.mp4 recovered.bin
```

提取后与原始文件做 MD5 校验：

```bash
python3 mediahider.py extract -f output.mp4 recovered.bin --verify-against secret.bin
```

## 命令说明

### 嵌入

```bash
python3 mediahider.py embed [options] <cover_video> <secret_file> <output_video>
```

参数：

- `cover_video`：宿主视频文件。
- `secret_file`：需要嵌入的 payload 文件。
- `output_video`：输出视频，扩展名必须为 `.mp4` 或 `.mkv`。

选项：

- `-f, --force`：覆盖已存在的输出文件。
- `--marker TEXT`：隐藏流标记，默认 `hidden_payload`。
- `--mime-type TYPE`：payload MIME metadata，默认 `application/octet-stream`。
- `--mp4-chunk-size N`：MP4 payload 分片大小，默认且最大为 `32768`。

示例：

```bash
python3 mediahider.py embed -f cover.mp4 secret.zip stego.mp4
python3 mediahider.py embed -f cover.mp4 secret.zip stego.mkv
python3 mediahider.py embed -f --marker project_payload cover.mp4 data.bin stego.mp4
```

### 提取

```bash
python3 mediahider.py extract [options] <stego_video> <output_file>
```

参数：

- `stego_video`：包含隐藏 payload 的 MP4 或 MKV 文件。
- `output_file`：提取出的 payload 保存路径。

选项：

- `-f, --force`：覆盖已存在的输出文件。
- `--marker TEXT`：隐藏流标记，默认 `hidden_payload`。
- `--stream-index N`：手动指定要提取的 stream index。
- `--verify-against FILE`：提取后与指定原始文件做 MD5 校验。

示例：

```bash
python3 mediahider.py extract -f stego.mp4 recovered.zip
python3 mediahider.py extract -f stego.mkv recovered.zip
python3 mediahider.py extract -f stego.mp4 recovered.zip --verify-against secret.zip
python3 mediahider.py extract -f stego.mp4 recovered.bin --stream-index 1
```

## Windows exe 使用

Windows 打包说明见 [PACKAGING_WINDOWS.md](PACKAGING_WINDOWS.md)。

生成 `dist\mediahider.exe` 后，命令格式与 Python 脚本一致，只需将 `python3 mediahider.py` 换成 `dist\mediahider.exe`。

查看帮助：

```bat
dist\mediahider.exe --help
```

嵌入到 MP4：

```bat
dist\mediahider.exe embed -f cover.mp4 secret.bin stego.mp4
```

嵌入到 MKV：

```bat
dist\mediahider.exe embed -f cover.mp4 secret.bin stego.mkv
```

从 MP4 提取：

```bat
dist\mediahider.exe extract -f stego.mp4 recovered.bin
```

从 MKV 提取：

```bat
dist\mediahider.exe extract -f stego.mkv recovered.bin
```

如果 `mediahider.exe` 所在目录已加入 `PATH`，也可以直接使用：

```bat
mediahider.exe embed -f cover.mp4 secret.bin stego.mp4
mediahider.exe extract -f stego.mp4 recovered.bin
```

Windows 下可使用 `certutil` 校验 SHA-256：

```bat
certutil -hashfile secret.bin SHA256
certutil -hashfile recovered.bin SHA256
```

如果未使用 `build_windows_bundle_ffmpeg.bat` 将 FFmpeg 内置进 exe，请将 `ffmpeg.exe` 和 `ffprobe.exe` 放到 `mediahider.exe` 同目录，或加入系统 `PATH`。

## 工作原理

### MKV

MKV 使用 Matroska 附件机制保存 payload。

核心 FFmpeg 命令结构如下：

```bash
ffmpeg -i cover.mp4 \
  -map 0 \
  -c copy \
  -attach secret.bin \
  -metadata:s:t:0 filename=secret.bin \
  -metadata:s:t:0 mimetype=application/octet-stream \
  -metadata:s:t:0 title=hidden_payload \
  stego.mkv
```

提取时，工具通过 `ffprobe` 定位带有 `hidden_payload` 标记的附件流，再通过 `ffmpeg -dump_attachment` 导出。

### MP4

MP4 对任意新建裸 data stream 的支持较受 FFmpeg 构建和 muxer 行为影响。为保证可恢复性，MediaHider 使用以下兼容方案：

1. 将 payload 拆分为 32 KiB part。
2. 写入一个 JSON manifest，记录 payload 大小、MD5、part 数量和 marker。
3. 将 manifest 和每个 part 通过 FFmpeg `data` demuxer 临时包装为 MPEG-TS private data。
4. 将这些 `bin_data` stream 通过 `-map` 复制进 MP4。
5. 提取时先读取 manifest，再按顺序提取并拼接 part，最后校验大小和 MD5。

该方案只操作容器层数据，不会重新编码宿主音视频。

## 注意事项

- MKV 原生支持附件，更适合承载任意 payload。
- MP4 会产生 manifest data track 和多个 payload part data tracks。
- 某些播放器或媒体分析工具可能会显示这些额外 data tracks。
- 大 payload 在 MP4 中会产生较多 tracks；大文件场景优先建议使用 MKV。
- 该工具不是加密工具，只负责在容器层携带隐藏数据。
- 由于使用 `-c copy`，输入流 codec 必须被目标容器支持。

## 仓库文件

- `mediahider.py`：主 CLI 实现。
- `PACKAGING_WINDOWS.md`：Windows PyInstaller 打包说明。
- `build_windows.bat`：构建不内置 FFmpeg 的 `mediahider.exe`。
- `build_windows_bundle_ffmpeg.bat`：构建内置 `ffmpeg.exe` / `ffprobe.exe` 的 `mediahider.exe`。
- `requirements-build.txt`：打包阶段依赖。

## 许可证

本项目采用 GNU General Public License v3.0 许可证，详见 [LICENSE](LICENSE)。
