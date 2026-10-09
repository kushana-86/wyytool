"""Small end-to-end fixtures; no user audio and no network required."""
import base64
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from mutagen.flac import FLAC

from wyytool import converter as app


def fixture(path, audio, fmt):
    key = b'fixture-key'
    encrypted_key = AES.new(app.CORE_KEY, AES.MODE_ECB).encrypt(pad(b'neteasecloudmusic' + key, 16))
    encrypted_key = bytes(x ^ 0x64 for x in encrypted_key)
    metadata = {'musicName': '测试歌曲', 'artist': [['测试歌手', 1]], 'album': '测试专辑', 'format': fmt}
    encrypted_meta = AES.new(app.META_KEY, AES.MODE_ECB).encrypt(pad(b'music:' + json.dumps(metadata).encode(), 16))
    encrypted_meta = bytes(x ^ 0x63 for x in b"163 key(Don't modify):" + base64.b64encode(encrypted_meta))
    box, cursor = list(range(256)), 0
    for i in range(256):
        cursor = (cursor + box[i] + key[i % len(key)]) % 256
        box[i], box[cursor] = box[cursor], box[i]
    encrypted_audio = bytearray()
    for offset, value in enumerate(audio):
        j = (offset + 1) % 256
        encrypted_audio.append(value ^ box[(box[j] + box[(box[j] + j) % 256]) % 256])
    def block(data):
        return struct.pack('<I', len(data)) + data
    # Empty image with allocated padding exercises a common parser edge case.
    path.write_bytes(b'CTENFDAM\0\0' + block(encrypted_key) + block(encrypted_meta)
                     + b'\0' * 5 + struct.pack('<II', 19, 0) + b'\0' * 19 + encrypted_audio)


class ConversionTests(unittest.TestCase):
    def test_flac_and_mp3_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for fmt in ('flac', 'mp3'):
                original = root / ('original.' + fmt)
                app.run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', str(original)])
                ncm = root / ('中文 & 空格 ' + fmt + '.ncm')
                fixture(ncm, original.read_bytes(), fmt)
                ncm.with_suffix('.lrc').write_text('[00:00.00]测试歌词', encoding='utf-8')
                before = hashlib.sha256(ncm.read_bytes()).digest()
                result = app.convert(ncm, root / 'out', offline=True)
                converted = next(result.glob('*.flac'))
                tags = FLAC(converted)
                self.assertEqual(tags['title'], ['测试歌曲'])
                self.assertIn('测试歌词', tags['lyrics'][0])
                report = json.loads(next(result.glob('*.json')).read_text('utf-8'))
                self.assertEqual(report['audio_preserved_without_reencoding'], fmt == 'flac')
                if fmt == 'flac':
                    self.assertEqual(tags.info.md5_signature, FLAC(original).info.md5_signature)
                second = app.convert(ncm, root / 'out', offline=True, compatible=True)
                self.assertNotEqual(result, second)
                compatible = FLAC(next(second.glob('*.flac')))
                self.assertEqual((compatible.info.bits_per_sample, compatible.info.sample_rate, compatible.info.channels), (16, 44100, 2))
                self.assertEqual(before, hashlib.sha256(ncm.read_bytes()).digest())

    def test_corruption_and_chunk_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'broken.ncm'
            source.write_bytes(b'not ncm')
            with self.assertRaises(ValueError):
                app.convert(source, root / 'out', offline=True)
            self.assertEqual(list((root / 'out').iterdir()), [])
            payload = bytes(range(256)) * 4100 + b'last partial chunk'
            fixture(source, payload, 'flac')
            decoded = root / 'decoded.bin'
            app.decrypt_ncm(source, decoded)
            self.assertEqual(decoded.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
