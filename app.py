"""인스타그램 게시물·릴스 링크를 넣으면 영상을 mp4로 다운로드해주는 간단한 웹앱."""
import os
import re
import shutil
import tempfile

from flask import Flask, after_this_request, jsonify, render_template, request, send_file
import yt_dlp

app = Flask(__name__)

# instagram.com 의 게시물/릴스/IGTV 링크만 허용 (임의 URL 요청 방지)
# 예: /p/ABC/ · /reel/ABC/ · /reels/ABC/ · /tv/ABC/ · /username/reel/ABC/
POST_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9._]+/)?(?:p|reel|reels|tv)/[A-Za-z0-9_-]+"
)

# 로그인이 필요한 게시물용 쿠키 파일 (Netscape 형식). 없으면 비로그인으로 시도합니다.
COOKIES_FILE = os.environ.get("IG_COOKIES_FILE") or None


def valid_url(url: str) -> bool:
    return bool(url) and bool(POST_RE.match(url.strip()))


def human_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def base_opts() -> dict:
    opts = {"quiet": True}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    return opts


def friendly_error(msg: str) -> str:
    low = msg.lower()
    if "no video" in low:
        return "이 게시물에는 영상이 없어요. (사진 게시물은 아직 지원하지 않아요)"
    if "login" in low or "log in" in low:
        return "로그인이 필요한 게시물이에요. IG_COOKIES_FILE 설정이 필요할 수 있어요."
    if "rate" in low and "limit" in low:
        return "인스타그램이 요청을 잠시 제한했어요. 몇 분 뒤에 다시 시도해 주세요."
    return msg


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/info")
def info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not valid_url(url):
        return jsonify(error="올바른 인스타그램 게시물 링크가 아니에요."), 400
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            data = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        return jsonify(error=f"가져오기 실패: {friendly_error(str(e))}"), 422

    # 여러 장/여러 영상(캐러셀)이면 entries 로 옴.
    # index 는 원래 순서를 유지해야 다운로드 때 같은 항목을 가리키므로 건너뛰어도 번호는 그대로 둡니다.
    entries = data.get("entries") or [data]
    items = []
    for idx, e in enumerate(entries, 1):
        if not e or not e.get("formats"):
            continue
        formats = []
        for f in e["formats"]:
            if f.get("vcodec") in (None, "none") or f.get("ext") != "mp4":
                continue
            formats.append(
                {
                    "format_id": f["format_id"],
                    "height": f.get("height"),
                    "label": f"{f.get('height') or '?'}p",
                    "size": human_size(f.get("filesize") or f.get("filesize_approx")),
                }
            )
        formats.sort(key=lambda x: x["height"] or 0, reverse=True)
        items.append(
            {
                "index": idx,
                "title": e.get("title") or e.get("id") or "video",
                "thumbnail": e.get("thumbnail"),
                "duration": e.get("duration"),
                "uploader": e.get("uploader") or e.get("uploader_id"),
                "formats": formats,
            }
        )
    if not items:
        return jsonify(error="이 게시물에는 다운로드할 영상이 없어요. (사진 게시물은 아직 지원하지 않아요)"), 422
    return jsonify(items=items)


@app.get("/api/download")
def download():
    url = request.args.get("url", "").strip()
    fmt = request.args.get("format", "best")
    index = request.args.get("index", "1")
    if not valid_url(url):
        return "잘못된 링크", 400
    if not re.fullmatch(r"[A-Za-z0-9_\-+/\[\]=<>,.:*]+", fmt) or not index.isdigit():
        return "잘못된 파라미터", 400

    tmpdir = tempfile.mkdtemp(prefix="igdl_")

    @after_this_request
    def cleanup(resp):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return resp

    opts = {
        **base_opts(),
        "format": fmt,
        "playlist_items": index,
        "outtmpl": os.path.join(tmpdir, "%(uploader)s_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        return f"다운로드 실패: {friendly_error(str(e))}", 422

    # yt-dlp 가 받는 도중의 .part 파일은 제외
    files = [f for f in os.listdir(tmpdir) if not f.endswith(".part")]
    if not files:
        return "다운로드된 파일이 없어요.", 500
    path = os.path.join(tmpdir, files[0])
    return send_file(path, as_attachment=True, download_name=files[0])


if __name__ == "__main__":
    # 기본은 이 PC에서만 접속 가능하게 둡니다. 서버 IP로 다운로드가 나가므로 외부 공개는 신중히.
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
    )
