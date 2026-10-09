# NCM 转 FLAC（Windows 拖入转换）

## 使用

把一首或多首 `.ncm` 文件，或整个文件夹，拖到 **拖入转换.bat** 上即可。
也可以把歌曲放到 `download` 文件夹，再双击 `拖入转换.bat`。

结果在本工具的 `output` 文件夹中，每首歌曲单独一个文件夹：

- `.flac`：音频，内嵌歌曲名、歌手、专辑、可取得的封面和歌词。
- `.lrc`：原文歌词，带时间轴与否取决于歌词源；UTF-8 BOM 编码。
- `.translated.lrc` / `.romanized.lrc`：接口提供时导出翻译和罗马音。
- `.jpg`：封面。
- `.json`：原始歌曲信息、音频参数、歌词获取状态和警告。

源文件始终保留。重复转换会创建 `(2)`、`(3)` 等新文件夹，不覆盖以前结果。
单首失败会继续转换其他歌曲。转换结束前会完整解码检查 FLAC。

## 音质与播放器

NCM 是容器，不代表里面一定是无损音频。原音频为 FLAC 时，默认直接保留音频数据，不重新编码、不改变采样率或位深。原音频为 MP3 等格式时会转码为 FLAC，但不能恢复已经丢失的音质，文件也可能明显变大。

把 `.flac` 与**同名** `.lrc` 一起复制到播放器。播放器需要支持 FLAC；封面和歌词显示能力取决于设备。部分播放器不支持高采样率、多声道或 24bit 文件，可以使用兼容模式生成 16bit / 44.1kHz / 双声道版本；该模式会重采样，不是原音频数据的直接保留。

使用兼容模式时，把歌曲拖到 **拖入转换_播放器兼容版.bat**，或双击它处理 `download`。

## 歌词与网络

优先读取 NCM 旁边的同名 `.lrc`，其次尝试 NCM 元数据中的歌词，最后按歌曲 ID 请求网易云歌词接口。NCM 通常不包含歌词，网络、接口变动或歌曲没有歌词都可能导致无法取得；不会生成伪造歌词。缺失封面时会尝试从网易云图片地址下载。歌曲文件不会被上传，网络请求仅用于获取歌词和缺失封面。

`--offline` 可完全禁用脚本的歌曲网络请求。第一次安装 Python 依赖仍需要联网。封面、歌词缺失不阻止音频输出，详情在 JSON 中查看。

## 环境与命令行

需要 Python 3.10 或更高版本。Windows 安装 Python 时勾选 **Add python.exe to PATH**。从 GitHub 下载 ZIP 并完整解压后使用，不要直接在压缩包里运行。

启动器首次运行会在 `.venv` 中安装 `requirements.txt`，需要联网，后续直接使用该环境；FFmpeg 优先使用系统版本，否则使用 imageio-ffmpeg 自带程序。不要单独移动启动器，整个工具文件夹需一同保留。

手动安装环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```powershell
# 默认处理 download
.\.venv\Scripts\python.exe ncm_to_flac.py

# 指定歌曲或目录，可传多个路径
.\.venv\Scripts\python.exe ncm_to_flac.py "D:\音乐" -o "D:\转换结果"

# 离线
.\.venv\Scripts\python.exe ncm_to_flac.py --offline

# 老播放器兼容版本
.\.venv\Scripts\python.exe ncm_to_flac.py --compatible
```

拖入时请拖到 `.bat` 文件图标上；转换窗口会显示进度并在完成后等待按键。
不支持的 NCM 变体或损坏文件会报告失败。

## 项目目录

```text
wyytool/
├── README.md                      # GitHub 首页
├── read.md                        # 完整使用说明
├── requirements.txt               # Python 依赖
├── ncm_to_flac.py                  # 命令行和启动器入口
├── 拖入转换.bat                    # 保持源 FLAC 参数
├── 拖入转换_播放器兼容版.bat         # 16bit / 44.1kHz / 双声道
├── wyytool/
│   ├── __init__.py
│   ├── __main__.py                 # python -m wyytool 入口
│   └── converter.py                # 解析、转换、歌词和封面逻辑
├── tests/
│   └── test_converter.py           # 无网络的合成音频测试
├── download/                      # 待转换歌曲，内容不提交
├── output/                        # 转换结果，内容不提交
└── .venv/                         # 首次运行自动创建，不提交
```

默认输入和输出目录始终相对于工具根目录，命令行自定义的相对路径则相对于当前工作目录。

## 开发验证

在工具根目录运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m wyytool --help
```

测试使用临时生成的音频，覆盖 FLAC 保留、MP3 转码、兼容模式、中文路径、本地歌词、重复输出、损坏文件和分块解码，不需要真实歌曲或歌词网络服务。

## 常见问题

- **找不到 Python / 安装失败**：安装 Python 3.10+ 并启用 PATH，确认可以联网下载依赖，再运行启动器。
- **有音频但没有歌词或封面**：查看同目录 JSON 中的 `lyrics_status` 和 `warnings`；也可自行准备同名 LRC 后重新转换。
- **播放器无法播放或不显示歌词**：先确认设备支持 FLAC；尝试兼容版，并确保 FLAC 与 LRC 文件名一致。部分设备需要不同的歌词编码或不支持内嵌图片。
- **重复运行后出现多个文件夹**：这是避免覆盖的设计，确认结果后可自行清理不需要的版本。
- **转换失败**：窗口会显示具体错误；脚本保留源 NCM，并继续处理下一首。

## GitHub 提交范围

`.gitignore` 排除虚拟环境、缓存、音乐文件和输出目录的实际内容；`download/.gitkeep` 与 `output/.gitkeep` 仅用于在新下载的项目中保留空目录。不要把账号密码、Cookie 或访问令牌加入项目。

格式实现参考：[ncmdump-py](https://github.com/ww-rm/ncmdump-py)、[NCMdump](https://github.com/rio4raki/NCMdump/blob/main/ncm_converter.py)。
