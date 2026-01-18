from flask import Flask, request, Response, render_template, jsonify
import yt_dlp
import subprocess

app = Flask(__name__)


def get_audio_url(youtube_url: str) -> dict:
    """Extract audio stream URL from YouTube video."""
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
        return {
            "url": info["url"],
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
        }


def stream_audio(audio_url: str):
    """Stream audio through FFmpeg, yielding chunks."""
    cmd = [
        "ffmpeg",
        "-i", audio_url,
        "-vn",  # No video
        "-acodec", "libmp3lame",
        "-ab", "192k",
        "-f", "mp3",
        "pipe:1",
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    try:
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        process.terminate()
        process.wait()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    """Get video info without streaming."""
    data = request.get_json()
    url = data.get("url", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        info = get_audio_url(url)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stream")
def stream():
    """Stream audio from YouTube URL."""
    url = request.args.get("url", "")

    if not url:
        return "No URL provided", 400

    try:
        info = get_audio_url(url)
        audio_url = info["url"]

        return Response(
            stream_audio(audio_url),
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": "inline",
                "Transfer-Encoding": "chunked",
            },
        )
    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
