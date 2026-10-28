"""대시보드 보고서의 PDF: JPEG 그림 하나를 넣은 1쪽 PDF를 직접 쓴다(새 라이브러리 없이).
구조: 1 Catalog, 2 Pages, 3 Page, 4 Contents(그림을 쪽 가득), 5 Image(/DCTDecode = JPEG를 그대로 넣는다). 끝에 xref 표(각 개체의 바이트 위치)와 trailer를 쓴다.
쪽 크기는 그림 픽셀 × 0.75(96dpi의 포인트). JPEG는 회색(1)·RGB(3) 성분만 받는다."""
import base64
import binascii

PX_TO_PT = 0.75
MAX_JPEG_BYTES = 30 * 1024 * 1024
MAX_PAGE_PT = 14400  # PDF 쪽 크기 상한(포인트)
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}  # 그림 크기와 성분 수를 담은 표식(C4·C8·CC는 아님)
DATA_URL = "data:image/jpeg;base64,"


def jpeg_info(raw):
    """JPEG 바이트의 (가로, 세로, 성분 수). JPEG가 아니거나 크기를 읽을 수 없으면 ValueError."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4 or raw[:2] != b"\xff\xd8":
        raise ValueError("JPEG 그림만 PDF로 만들 수 있어요.")
    i = 2
    while i + 4 <= len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0x01, *range(0xD0, 0xDA)) or marker == 0:  # 길이가 없는 표식
            i += 2
            continue
        size = int.from_bytes(raw[i + 2:i + 4], "big")
        if marker in SOF:
            if i + 10 > len(raw):
                break
            height, width, parts = int.from_bytes(raw[i + 5:i + 7], "big"), int.from_bytes(raw[i + 7:i + 9], "big"), raw[i + 9]
            if not width or not height:
                break
            if parts not in (1, 3):
                raise ValueError("회색이나 RGB 색의 JPEG만 PDF로 만들 수 있어요.")
            return width, height, parts
        i += 2 + size
    raise ValueError("JPEG 그림의 크기를 읽을 수 없어요.")


def number(value):
    """PDF에 쓰는 숫자 글자(소수 둘째 자리까지, 군더더기 0 없음)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def make_pdf(jpeg):
    """JPEG 바이트 하나를 쪽 가득 채운 1쪽 PDF 바이트로."""
    width, height, parts = jpeg_info(jpeg)
    w_pt, h_pt = width * PX_TO_PT, height * PX_TO_PT
    if max(w_pt, h_pt) > MAX_PAGE_PT:
        raise ValueError("그림이 너무 커서 PDF 한 쪽에 담을 수 없어요. 카드를 줄이거나 배율을 낮추세요.")
    content = f"q {number(w_pt)} 0 0 {number(h_pt)} 0 0 cm /Im0 Do Q".encode("ascii")
    color = "/DeviceRGB" if parts == 3 else "/DeviceGray"
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {number(w_pt)} {number(h_pt)}] /Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>".encode("ascii"),
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace {color} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>".encode("ascii")
        + b"\nstream\n" + bytes(jpeg) + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode("ascii") + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode("ascii")
    return bytes(out)


def decode_image(data):
    """화면이 보낸 JPEG data URL을 바이트로. 아니면 ValueError."""
    if not isinstance(data, str) or not data.startswith(DATA_URL):
        raise ValueError("JPEG 그림만 PDF로 만들 수 있어요.")
    try:
        raw = base64.b64decode(data[len(DATA_URL):], validate=True)
    except binascii.Error:
        raise ValueError("그림 데이터가 올바르지 않아요.") from None
    if len(raw) > MAX_JPEG_BYTES:
        raise ValueError(f"그림은 {MAX_JPEG_BYTES // 1024 // 1024}MB까지예요.")
    return raw
