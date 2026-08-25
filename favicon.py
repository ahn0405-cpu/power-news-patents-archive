"""탭 아이콘(파비콘) 생성 — 표준 라이브러리만 쓴다.

왜 그리는가: 페이지에 아이콘 선언이 없어서 브라우저가 자동으로 /favicon.ico 를
찾다가 404 를 받고, 탭·북마크에는 기본 아이콘이 붙었다. 기관 서비스에서 이건
눈에 띈다.

왜 직접 그리는가: 빌드 러너에 pip install 단계가 없다(표준 라이브러리만 있다).
PIL 도 cairosvg 도 못 쓴다. 그래서 픽셀을 직접 채우고 zlib 으로 PNG 를 만든다.
어차피 모양이 단순해서 이쪽이 의존성을 늘리는 것보다 낫다.

모양: 사이트 제목의 ⚡ 를 그대로 쓴다(머리글 .bolt 와 같은 --accent 색).
둥근 사각 타일에 흰 번개를 올린다. 타일을 까는 이유는 탭 막대 배경색이
브라우저·테마마다 달라서다 — 배경 없이 호박색 번개만 두면 밝은 탭에서 묻힌다.

번개 좌표는 16px 에서 눈으로 고른 것이다. 통통한 모양은 그 크기에서 번개가
아니라 별로 읽혔다(실제로 네 가지를 그려 놓고 비교했다).
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# 24×24 상자 기준 번개 꼭짓점. 벡터(SVG)와 래스터(PNG)가 이 하나를 같이 쓴다 —
# 둘로 나눠 두면 언젠가 서로 다른 모양이 된다.
BOLT = [(16, 1), (7, 14), (12, 14), (8, 23), (17, 10), (12, 10)]
BG = (0xE8, 0xA3, 0x3D)      # --accent (라이트) — 머리글 번개와 같은 색
FG = (0xFF, 0xFF, 0xFF)
PAD_RATIO = 0.10             # 타일 여백. 0.16 이면 16px 에서 번개가 너무 작다
RADIUS_RATIO = 0.22


def _fit(size: float) -> list[tuple[float, float]]:
    """번개를 size 픽셀 타일 안에 여백만큼 띄워 가운데 정렬한다."""
    pad = size * PAD_RATIO
    box = size - 2 * pad
    xs = [p[0] for p in BOLT]
    ys = [p[1] for p in BOLT]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    s = min(box / w, box / h)                 # 비율 유지(번개는 세로로 길다)
    ox = (size - w * s) / 2 - min(xs) * s
    oy = (size - h * s) / 2 - min(ys) * s
    return [(x * s + ox, y * s + oy) for x, y in BOLT]


def _inside(poly, x: float, y: float) -> bool:
    n, ins = len(poly), False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) / (y2 - y1) * (x2 - x1):
                ins = not ins
    return ins


def _rows(size: int, ss: int = 4) -> list[bytes]:
    """RGBA 픽셀 행. ss 배 초과표본으로 가장자리 계단을 없앤다."""
    poly = _fit(size)
    r = size * RADIUS_RATIO
    lo, hi = r, size - r
    n = ss * ss
    out = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            tile = bolt = 0
            for sy in range(ss):
                y = py + (sy + 0.5) / ss
                cy = lo if y < lo else (hi if y > hi else y)
                for sx in range(ss):
                    x = px + (sx + 0.5) / ss
                    cx = lo if x < lo else (hi if x > hi else x)
                    if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                        tile += 1
                    if _inside(poly, x, y):
                        bolt += 1
            fb, fa = bolt / n, tile / n
            row += bytes(round(BG[i] * (1 - fb) + FG[i] * fb) for i in range(3))
            row += bytes((round(255 * fa),))
        out.append(bytes(row))
    return out


def _png(rows: list[bytes], w: int, h: int | None = None) -> bytes:
    h = w if h is None else h

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def png(size: int) -> bytes:
    return _png(_rows(size), size)


def ico(sizes=(16, 32)) -> bytes:
    """ICO 안에 PNG 를 그대로 넣는다(Vista 이후·현행 브라우저 전부 읽는다).

    두 크기를 담는 이유: 탭은 16, 북마크·바로가기는 32 를 쓴다. 하나만 넣으면
    나머지 크기에서 브라우저가 줄이거나 늘려 흐려진다.
    """
    imgs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    offset = len(head) + 16 * len(imgs)
    entries, blobs = b"", b""
    for s, data in zip(sizes, imgs):
        entries += struct.pack("<BBBBHHII", s & 0xFF, s & 0xFF, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        blobs += data
    return head + entries + blobs


def svg() -> str:
    """벡터판. 어떤 크기로 키워도 또렷해 기본 아이콘으로 쓴다."""
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in _fit(32))
    # viewBox 만 두면 고유 크기가 없어 브라우저가 기본값(150px)으로 잡는다.
    # 아이콘은 어차피 늘려 쓰지만, 고유 크기를 요구하는 도구가 있어 같이 적는다.
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" '
            'viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="{32 * RADIUS_RATIO:.0f}" '
            f'fill="#{BG[0]:02X}{BG[1]:02X}{BG[2]:02X}"/>'
            f'<polygon points="{pts}" fill="#FFFFFF"/></svg>')


def data_uri() -> str:
    """head 에 그대로 박을 수 있는 형태.

    파일 대신 data: 로 넣으면 요청이 아예 없고, file:// 로 열어도 아이콘이 뜬다.
    괄호·꺾쇠만 인코딩하면 되고 그 편이 base64 보다 짧다.
    """
    s = svg().replace('"', "'")
    for a, b in (("%", "%25"), ("<", "%3C"), (">", "%3E"), ("#", "%23")):
        s = s.replace(a, b)
    return "data:image/svg+xml," + s


def write(site_dir: Path) -> None:
    """탭 아이콘 파일들을 사이트에 쓴다.

    favicon.ico 는 링크가 없어도 브라우저가 자동으로 찾는 자리라, 있어야 404 가
    사라진다(SVG 를 못 읽는 옛 클라이언트의 대비책이기도 하다).
    """
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "favicon.svg").write_text(svg(), encoding="utf-8")
    (site_dir / "favicon.ico").write_bytes(ico())
    (site_dir / "apple-touch-icon.png").write_bytes(png(180))
