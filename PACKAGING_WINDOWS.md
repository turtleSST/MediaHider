# Windows 可执行文件打包说明

本文档说明如何在 Windows 上使用 Python 和 PyInstaller 将 `mediahider.py` 打包为 `mediahider.exe`。

## 需要拷贝到 Windows 的文件

至少拷贝：

```text
mediahider.py
requirements-build.txt
build_windows.bat
```

如果希望把 FFmpeg 一起打进 exe，再额外拷贝：

```text
build_windows_bundle_ffmpeg.bat
ffmpeg.exe
ffprobe.exe
```

`ffmpeg.exe` 和 `ffprobe.exe` 可以来自 Windows 版 FFmpeg 发布包。需要把这两个 exe 放在和 `build_windows_bundle_ffmpeg.bat` 同一个目录中。

## 方式一：不内置 FFmpeg

这种方式生成的 `mediahider.exe` 体积较小。运行时需要满足以下任意一种条件：

- `ffmpeg.exe` 和 `ffprobe.exe` 已在系统 `PATH` 中。
- `ffmpeg.exe` 和 `ffprobe.exe` 与 `mediahider.exe` 放在同一个目录。

在 Windows 命令提示符中执行：

```bat
build_windows.bat
```

生成结果：

```text
dist\mediahider.exe
```

## 方式二：内置 FFmpeg

这种方式会把 `ffmpeg.exe` 和 `ffprobe.exe` 一起打进 PyInstaller 单文件 exe。最终文件更大，但拷贝部署更方便。

目录结构示例：

```text
mediahider.py
requirements-build.txt
build_windows_bundle_ffmpeg.bat
ffmpeg.exe
ffprobe.exe
```

执行：

```bat
build_windows_bundle_ffmpeg.bat
```

生成结果：

```text
dist\mediahider.exe
```

## 打包后使用

查看帮助：

```bat
dist\mediahider.exe --help
```

嵌入到 MP4：

```bat
dist\mediahider.exe embed -f CGVideo.mp4 random_num.txt CGVideo_stego.mp4
```

嵌入到 MKV：

```bat
dist\mediahider.exe embed -f CGVideo.mkv random_num.txt CGVideo_stego.mkv
```

提取：

```bat
dist\mediahider.exe extract -f CGVideo_stego.mp4 random_num_from_stego_mp4.txt
dist\mediahider.exe extract -f CGVideo_stego.mkv random_num_from_stego_mkv.txt
```

校验 SHA-256：

```bat
certutil -hashfile random_num.txt SHA256
certutil -hashfile random_num_from_stego_mp4.txt SHA256
certutil -hashfile random_num_from_stego_mkv.txt SHA256
```

## 注意事项

- 生成 exe 的平台应与目标运行平台一致。要生成 Windows exe，建议在 Windows 上执行上述 bat 脚本。
- `mediahider.exe` 本身不重新编码视频或音频；内部仍通过 FFmpeg 执行容器复用和解复用。
- 如果不内置 FFmpeg，运行时报错 `Required executable not found: ffmpeg` 或 `ffprobe`，请把对应 exe 放到 `mediahider.exe` 同目录，或加入系统 `PATH`。
- MP4 输出会包含 manifest data track 和多个 payload part data tracks；MKV 输出会包含 Matroska attachment。
