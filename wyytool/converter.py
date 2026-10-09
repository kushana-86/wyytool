"""Drag-and-drop NCM converter. Python 3.10+, see README.md."""
from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from Crypto.Util.strxor import strxor
from mutagen.flac import FLAC, Picture
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CORE_KEY = bytes.fromhex('687A4852416D736F356B496E62617857')
META_KEY = bytes.fromhex('2331346C6A6B5F215C5D2630553C2728')


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('NCM 文件不完整或已损坏')
    return data


def uint32(stream):
    return struct.unpack('<I', read_exact(stream, 4))[0]


def blob(stream, limit=32 * 1024 * 1024):
    size = uint32(stream)
    if size > limit:
        raise ValueError('NCM 数据块大小异常')
    return read_exact(stream, size)


def decrypt_ncm(source: Path, output: Path):
    """Decode in bounded chunks; cover allocation can exceed its actual size."""
    warnings = []
    with source.open('rb') as stream:
        if read_exact(stream, 8) != b'CTENFDAM':
            raise ValueError('不是有效的 NCM 文件')
        read_exact(stream, 2)
        encrypted = bytes(b ^ 0x64 for b in blob(stream, 65536))
        key = unpad(AES.new(CORE_KEY, AES.MODE_ECB).decrypt(encrypted), 16)
        if not key.startswith(b'neteasecloudmusic') or len(key) <= 17:
            raise ValueError('无法解析 NCM 音频密钥')
        key = key[17:]
        box = list(range(256))
        last = 0
        for i in range(256):
            last = (box[i] + last + key[i % len(key)]) & 255
            box[i], box[last] = box[last], box[i]
        mask = bytes(box[(box[j] + box[(box[j] + j) & 255]) & 255]
                     for j in list(range(1, 256)) + [0])
        metadata = {}
        encrypted = blob(stream)
        if encrypted:
            try:
                raw = bytes(b ^ 0x63 for b in encrypted)
                raw = base64.b64decode(raw[22:], validate=True)
                raw = unpad(AES.new(META_KEY, AES.MODE_ECB).decrypt(raw), 16)
                metadata = json.loads(raw.split(b':', 1)[1])
                if not isinstance(metadata, dict):
                    raise ValueError('歌曲信息不是 JSON 对象')
            except Exception as exc:
                metadata = {}
                warnings.append(f'歌曲信息解析失败：{exc}')
        read_exact(stream, 5)  # CRC32 + reserved byte
        allocated, actual = uint32(stream), uint32(stream)
        if actual > allocated or allocated > 32 * 1024 * 1024:
            raise ValueError('NCM 封面区大小异常')
        cover = read_exact(stream, actual)
        read_exact(stream, allocated - actual)
        with output.open('wb') as target:
            while chunk := stream.read(1024 * 1024):
                target.write(strxor(chunk, (mask * ((len(chunk) + 255) // 256))[:len(chunk)]))
    if not output.stat().st_size:
        raise ValueError('NCM 中没有音频数据')
    return metadata, cover, warnings


def fetch(url, limit=16 * 1024 * 1024):
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com/'})
    with urlopen(request, timeout=12) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('服务器返回的数据过大')
    return data


def get_lyrics(source, metadata, offline, warnings):
    # Prefer a user-provided sidecar, preserving its timestamps.
    local = source.with_suffix('.lrc')
    if local.is_file():
        raw = local.read_bytes()
        for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
            try:
                lyric = raw.decode(encoding)
                if lyric.strip():
                    return {'original': lyric}, '本地同名 LRC'
            except UnicodeError:
                pass
    for field in ('lyric', 'lyrics'):
        if isinstance(metadata.get(field), str) and metadata[field].strip():
            return {'original': metadata[field]}, 'NCM 内嵌歌词'
    if offline:
        return {}, '离线模式：未发现本地或内嵌歌词'
    song_id = metadata.get('musicId')
    if not str(song_id).isdigit() or int(song_id) <= 0:
        return {}, '缺少歌曲 ID，无法查询歌词'
    try:
        query = urlencode({'id': song_id, 'lv': -1, 'kv': -1, 'tv': -1, 'rv': -1})
        response = json.loads(fetch('https://music.163.com/api/song/lyric?' + query))
        if response.get('code') != 200:
            raise ValueError(f"接口状态：{response.get('code')}")
        result = {}
        for key, name in [('lrc', 'original'), ('tlyric', 'translation'), ('romalrc', 'romanized')]:
            value = (response.get(key) or {}).get('lyric', '')
            if value.strip():
                result[name] = value
        return result, '网易云歌词接口' if result else '接口未提供歌词（可能为纯音乐）'
    except Exception as exc:
        warnings.append(f'歌词获取失败：{exc}')
        return {}, '歌词请求失败，可稍后重试'


def ffmpeg():
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(arguments):
    result = subprocess.run([ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin', *arguments],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError('FFmpeg 处理失败：' + result.stderr[-2000:])


def convert(source: Path, out_root: Path, offline=False, compatible=False):
    out_root.mkdir(parents=True, exist_ok=True)
    # Reserve a separate folder so duplicate names and reruns never overwrite files.
    folder = out_root / source.stem
    index = 2
    while True:
        try:
            folder.mkdir()
            break
        except FileExistsError:
            folder = out_root / f'{source.stem} ({index})'
            index += 1
    try:
        with tempfile.TemporaryDirectory(prefix='.ncm-', dir=out_root) as temp:
            temp = Path(temp)
            raw = temp / 'audio.bin'
            metadata, cover, warnings = decrypt_ncm(source, raw)
            music = metadata.get('mainMusic', metadata)
            if not isinstance(music, dict):
                music = {}
            with raw.open('rb') as stream:
                is_flac = stream.read(4) == b'fLaC'
            output = temp / 'audio.flac'
            original_info = FLAC(raw).info if is_flac else None
            if is_flac and not compatible:
                shutil.copyfile(raw, output)
            else:
                print('  编码 FLAC…' if is_flac else '  注意：原音频不是 FLAC，转码不会提升原音质。', flush=True)
                arguments = ['-y', '-i', str(raw), '-map', '0:a:0', '-map_metadata', '-1', '-c:a', 'flac']
                if compatible:
                    arguments += ['-ar', '44100', '-ac', '2', '-sample_fmt', 's16']
                run_ffmpeg(arguments + [str(output)])
            audio = FLAC(output)
            title = str(music.get('musicName') or source.stem)
            artists = [str(a[0]) for a in music.get('artist', []) if isinstance(a, (list, tuple)) and a]
            audio['title'] = title
            if artists:
                audio['artist'] = artists
                audio['albumartist'] = artists
            for field, tag in [('album', 'album'), ('musicId', 'netease_music_id'),
                               ('trackNumber', 'tracknumber'), ('discNumber', 'discnumber')]:
                if music.get(field) is not None:
                    audio[tag] = str(music[field])
            if not cover and audio.pictures:
                cover = audio.pictures[0].data
            if not cover and not offline and music.get('albumPic'):
                try:
                    url = str(music['albumPic'])
                    parsed = urlparse(url)
                    if parsed.scheme not in ('http', 'https') or not (parsed.hostname or '').endswith('.music.126.net'):
                        raise ValueError('封面地址不是网易云图片域名')
                    cover = fetch(url.replace('http://', 'https://', 1))
                except Exception as exc:
                    warnings.append(f'封面下载失败：{exc}')
            base = source.stem
            staged = temp / 'result'
            staged.mkdir()
            cover_name = None
            if cover:
                try:
                    with Image.open(io.BytesIO(cover)) as image:
                        image.load()
                        # JPEG is broadly supported by portable players.
                        buffer = io.BytesIO()
                        image.convert('RGB').save(buffer, 'JPEG', quality=95)
                        picture = Picture()
                        picture.type, picture.mime = 3, 'image/jpeg'
                        picture.width, picture.height = image.size
                        picture.depth, picture.data = 24, buffer.getvalue()
                    audio.clear_pictures()
                    audio.add_picture(picture)
                    cover_name = base + '.jpg'
                    (staged / cover_name).write_bytes(picture.data)
                except Exception as exc:
                    warnings.append(f'封面处理失败：{exc}')
            if not cover_name:
                warnings.append('没有可导出的封面')
            lyrics, lyric_status = get_lyrics(source, music, offline, warnings)
            for kind, suffix in [('original', '.lrc'), ('translation', '.translated.lrc'), ('romanized', '.romanized.lrc')]:
                if kind in lyrics:
                    (staged / (base + suffix)).write_text(lyrics[kind], encoding='utf-8-sig')
            if lyrics.get('original'):
                audio['lyrics'] = lyrics['original']
            audio.save()
            # Decode the whole output to catch truncated/corrupt audio before publishing.
            run_ffmpeg(['-xerror', '-i', str(output), '-map', '0:a:0', '-f', 'null', '-'])
            audio = FLAC(output)
            report = {
                'source': str(source), 'title': title, 'artists': artists,
                'album': music.get('album'), 'music_id': music.get('musicId'),
                'source_format': 'flac' if is_flac else music.get('format', 'unknown'),
                'audio_preserved_without_reencoding': is_flac and not compatible,
                'compatibility_mode': compatible,
                'original_sample_rate': original_info.sample_rate if original_info else None,
                'original_bits_per_sample': original_info.bits_per_sample if original_info else None,
                'sample_rate': audio.info.sample_rate, 'bits_per_sample': audio.info.bits_per_sample,
                'channels': audio.info.channels, 'duration_seconds': audio.info.length,
                'lyrics_status': lyric_status, 'cover_file': cover_name,
                'warnings': warnings, 'ncm_metadata': metadata,
            }
            (staged / (base + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            shutil.move(str(output), staged / (base + '.flac'))
            for file in staged.iterdir():
                shutil.move(str(file), folder / file.name)
        print(f'  完成：{folder}\n  歌词：{lyric_status}', flush=True)
        for warning in warnings:
            print('  提示：' + warning)
        return folder
    except BaseException:
        # Only remove the empty directory reserved by this invocation.
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
        raise


def main():
    parser = argparse.ArgumentParser(description='NCM 转 FLAC，导出歌词、封面和歌曲信息。')
    parser.add_argument('paths', nargs='*', type=Path, help='NCM 文件或文件夹；默认扫描 download')
    parser.add_argument('-o', '--output', type=Path, default=ROOT / 'output')
    parser.add_argument('--offline', action='store_true', help='禁止封面和歌词网络请求')
    parser.add_argument('--compatible', action='store_true', help='转为 16bit/44.1kHz 双声道，适配老播放器（会重采样）')
    args = parser.parse_args()
    files, seen, failures = [], set(), 0
    for path in args.paths or [ROOT / 'download']:
        path = path.resolve()
        if not path.exists():
            print(f'路径不存在：{path}')
            failures += 1
            continue
        candidates = sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in candidates:
            if item.is_file() and item.suffix.lower() == '.ncm' and item not in seen:
                seen.add(item)
                files.append(item)
    if not files:
        print('没有找到 NCM 文件。请把文件/文件夹拖到「拖入转换.bat」，或放入 download 后双击运行。')
        return 1
    succeeded = 0
    for i, source in enumerate(files, 1):
        print(f'[{i}/{len(files)}] {source.name}', flush=True)
        try:
            convert(source, args.output.resolve(), args.offline, args.compatible)
            succeeded += 1
        except Exception as exc:
            failures += 1
            print(f'  失败：{exc}', flush=True)
    print(f'\n结束：成功 {succeeded} 首，失败 {failures} 项。输出：{args.output.resolve()}')
    return 1 if failures else 0


def cli():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已取消。')
        sys.exit(130)


if __name__ == '__main__':
    cli()
