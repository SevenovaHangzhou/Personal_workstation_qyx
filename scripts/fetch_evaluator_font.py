"""Fetch the pinned, OFL-licensed Song-style fallback for evaluator builds."""
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'f8d157532fbfaeda587e826d4cd5b21a49186f7c'
BASE = f'https://raw.githubusercontent.com/notofonts/noto-cjk/{REVISION}/Serif/'
FILES = (
    ('OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf', 'NotoSerifCJKsc-Regular.otf',
     24543080, 'git', 'cba8a4783cc38574ac7cda52cae7d9b4241c07a5'),
    ('LICENSE', 'NotoSerifCJK-LICENSE.txt', 4301, 'sha256',
     '6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2'),
)


def verified(data, length, algorithm, expected):
    digest = (hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest() if algorithm == 'git'
              else hashlib.sha256(data).hexdigest())
    return len(data) == length and digest == expected


def main():
    directory = ROOT/'assets/fonts'
    directory.mkdir(parents=True, exist_ok=True)
    for source, name, length, algorithm, expected in FILES:
        destination = directory/name
        if destination.is_file() and verified(destination.read_bytes(), length, algorithm, expected):
            continue
        with urllib.request.urlopen(BASE+source, timeout=90) as response:
            data = response.read(length+1)
        if not verified(data, length, algorithm, expected):
            raise ValueError('Pinned font asset failed integrity verification: '+name)
        temporary = destination.with_suffix('.tmp')
        temporary.write_bytes(data)
        temporary.replace(destination)
    print('Verified evaluator fallback font and license at '+REVISION)


if __name__ == '__main__':
    main()
