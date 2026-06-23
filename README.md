# MediaHider 使用说明

`mediahider.py` 是一个基于 Python 3 和 FFmpeg CLI 的命令行工具，用于把任意二进制文件嵌入到 MP4 或 MKV 视频容器中，并在需要时完整提取出来。

核心原则：

- 宿主视频、音频流始终使用 `-c copy` 进行流复制，不重新编码。
- MKV 使用 Matroska 原生附件机制。
- MP4 使用 FFmpeg 可识别的 `bin_data` 私有数据轨道。为规避常见 FFmpeg/MP4 对单个 raw private data sample 只稳定索引 32 KiB 的限制，脚本会生成一个 manifest data track，并把 payload 拆分为多个 32 KiB part data tracks，提取时按 manifest 拼接并校验 MD5。

## 环境要求

- Python 3
- `ffmpeg` 和 `ffprobe` 可在 `PATH` 中直接调用

Windows 打包为 exe 的步骤见 [PACKAGING_WINDOWS.md](PACKAGING_WINDOWS.md)。

检查命令：

```bash
ffmpeg -version
ffprobe -version
```

## 项目文件

本次项目目录中使用的输入文件：

```text
CGVideo.mp4
random_num.txt
mediahider.py
```

本次生成的文件：

```text
CGVideo.mkv
CGVideo_stego.mp4
CGVideo_stego.mkv
random_num_from_stego_mp4.txt
random_num_from_stego_mkv.txt
```

## 本次实际执行命令

### 1. 将 MP4 转封装为 MKV

该步骤只转封装，不重编码：

```bash
ffmpeg -hide_banner -y -i CGVideo.mp4 -map 0 -c copy CGVideo.mkv
```

### 2. 嵌入 random_num.txt

嵌入到 MP4：

```bash
python3 mediahider.py embed -f CGVideo.mp4 random_num.txt CGVideo_stego.mp4
```

嵌入到 MKV：

```bash
python3 mediahider.py embed -f CGVideo.mkv random_num.txt CGVideo_stego.mkv
```

执行结果摘要：

```text
Embedded MP4 data tracks: CGVideo_stego.mp4
Payload size=5242880 md5=6880c487a3b9e564d45bdc2bc42eb508 parts=160

Embedded MKV attachment: CGVideo_stego.mkv
Payload size=5242880 md5=6880c487a3b9e564d45bdc2bc42eb508
```

### 3. 提取 random_num.txt

从 MP4 隐写视频提取：

```bash
python3 mediahider.py extract -f CGVideo_stego.mp4 random_num_from_stego_mp4.txt
```

从 MKV 隐写视频提取：

```bash
python3 mediahider.py extract -f CGVideo_stego.mkv random_num_from_stego_mkv.txt
```

执行结果摘要：

```text
Extracted payload: random_num_from_stego_mp4.txt
Payload size=5242880 md5=6880c487a3b9e564d45bdc2bc42eb508

Extracted payload: random_num_from_stego_mkv.txt
Payload size=5242880 md5=6880c487a3b9e564d45bdc2bc42eb508
```

### 4. SHA-256 校验

```bash
sha256sum random_num.txt random_num_from_stego_mp4.txt random_num_from_stego_mkv.txt
```

校验输出：

```text
856975aeb140bcc2f98260804ce8f849fe4e06a7b830a261f123461b78cf595e  random_num.txt
856975aeb140bcc2f98260804ce8f849fe4e06a7b830a261f123461b78cf595e  random_num_from_stego_mp4.txt
856975aeb140bcc2f98260804ce8f849fe4e06a7b830a261f123461b78cf595e  random_num_from_stego_mkv.txt
```

同时执行了字节级比较：

```bash
cmp random_num.txt random_num_from_stego_mp4.txt
cmp random_num.txt random_num_from_stego_mkv.txt
```

两条 `cmp` 命令均返回 0，说明提取文件与原始文件完全一致。

## 通用用法

### 嵌入

```bash
python3 mediahider.py embed [-f] <cover_video> <secret_file> <output_video>
```

示例：

```bash
python3 mediahider.py embed -f cover.mp4 secret.zip output.mp4
python3 mediahider.py embed -f cover.mp4 secret.zip output.mkv
```

参数说明：

- `cover_video`：宿主视频。
- `secret_file`：需要隐藏的任意二进制文件。
- `output_video`：输出文件，扩展名必须是 `.mp4` 或 `.mkv`。
- `-f, --force`：覆盖已存在的输出文件。
- `--marker`：隐藏流标记，默认 `hidden_payload`。
- `--mime-type`：payload MIME metadata，默认 `application/octet-stream`。
- `--mp4-chunk-size`：MP4 payload part 大小，最大 32768，默认 32768。

### 提取

```bash
python3 mediahider.py extract [-f] <stego_video> <output_file>
```

示例：

```bash
python3 mediahider.py extract -f output.mp4 recovered.zip
python3 mediahider.py extract -f output.mkv recovered.zip
python3 mediahider.py extract -f output.mp4 recovered.zip --verify-against secret.zip
```

参数说明：

- `stego_video`：包含隐藏数据的 MP4 或 MKV。
- `output_file`：提取出的 payload 文件。
- `--stream-index`：手动指定要提取的数据流或附件流 index。
- `--verify-against`：提取后与指定原始文件做 MD5 校验。

## 内部实现说明

### MKV 路径

MKV 使用 Matroska 附件机制。核心 FFmpeg 命令结构如下：

```bash
ffmpeg -hide_banner -y \
  -i CGVideo.mkv \
  -map 0 \
  -c copy \
  -attach random_num.txt \
  -metadata:s:t:0 filename=random_num.txt \
  -metadata:s:t:0 mimetype=application/octet-stream \
  -metadata:s:t:0 title=hidden_payload \
  -metadata:s:t:0 "comment=mediahider md5=6880c487a3b9e564d45bdc2bc42eb508 size=5242880" \
  CGVideo_stego.mkv
```

提取时使用 `ffprobe` 找到带 `hidden_payload` 标记的附件流，然后通过 FFmpeg `-dump_attachment` 导出。

### MP4 路径

MP4 中任意裸文件不能直接稳定作为新 data stream 写入所有 FFmpeg/MP4 muxer 组合。脚本采用以下流程：

1. 将 payload 拆为 32 KiB part。
2. 为 payload 写入一个 JSON manifest，记录总大小、MD5、part 数量等信息。
3. 每个 manifest/part 先通过 FFmpeg `data` demuxer 包装为临时 MPEG-TS private data stream，使其被 FFmpeg 识别为 `bin_data`。
4. 将这些 `bin_data` stream 通过 `-map` 复制进 MP4。
5. 提取时先读取 manifest，再逐个提取 part data stream，按序拼回原始文件，并校验大小和 MD5。

单个 part 的临时包装命令结构如下：

```bash
ffmpeg -hide_banner -y \
  -f data \
  -raw_packet_size 32768 \
  -i <part.bin> \
  -map 0:0 \
  -c copy \
  -f mpegts \
  <part.ts>
```

最终 MP4 复用命令结构如下：

```bash
ffmpeg -hide_banner -y \
  -i CGVideo.mp4 \
  -i <manifest.ts> \
  -i <part_00000000.ts> \
  -i <part_00000001.ts> \
  ... \
  -map 0 \
  -map 1:0 \
  -map 2:0 \
  -map 3:0 \
  ... \
  -c copy \
  -metadata:s:d:0 handler_name=hidden_payload_manifest \
  -metadata:s:d:1 handler_name=hidden_payload_part_00000000_of_00000160 \
  -metadata:s:d:2 handler_name=hidden_payload_part_00000001_of_00000160 \
  ... \
  CGVideo_stego.mp4
```

本次 `random_num.txt` 大小为 5242880 字节，按 32768 字节分片后得到 160 个 payload part tracks。

## 注意事项

- MKV 是更适合附件型 payload 的容器，兼容性和效率更好。
- MP4 方案可用，但会增加多个 `bin_data` data tracks；某些播放器或工具可能会显示额外的 metadata/data streams。
- 该工具不加密 payload，只负责容器层隐藏/携带。需要保密时，应先自行加密文件，再嵌入。
- 如果 FFmpeg 执行失败，脚本会输出完整 stderr，便于定位具体 muxer 或 codec 兼容问题。
