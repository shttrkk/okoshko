"""Mux an owner's existing voice recording into a separate demo MP4, offline.

python -m scripts.mix_voice --voice /path/voice.wav --video demo/okno-demo.mp4 \
    --output demo/okno-demo-voiced.mp4
No speech is generated. Original video and existing output files are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
        "-of", "json", str(path),
    ], text=True))


def video_hash(path: Path) -> str:
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c:v", "copy",
        "-f", "hash", "-hash", "sha256", "-",
    ], text=True).strip()


def mix(voice: Path, video: Path, output: Path) -> dict:
    voice, video, output = voice.resolve(), video.resolve(), output.resolve()
    if output in {voice, video} or output.exists():
        raise ValueError("Use a new, separate output path; inputs and existing files cannot be overwritten")
    if not voice.is_file() or not video.is_file():
        raise ValueError("Both voice and video must be existing files")
    audio_probe, source_probe = probe(voice), probe(video)
    if not any(stream["codec_type"] == "audio" for stream in audio_probe["streams"]):
        raise ValueError("The voice file has no audio stream")
    if not any(stream["codec_type"] == "video" for stream in source_probe["streams"]):
        raise ValueError("The video file has no video stream")
    duration = float(source_probe["format"]["duration"])
    voice_duration = float(audio_probe["format"]["duration"])
    if voice_duration > duration + .05:
        raise ValueError(f"Voice is {voice_duration:.3f}s, longer than video {duration:.3f}s; edit the recording first to avoid cutting words")
    source_sha = hashlib.sha256(video.read_bytes()).hexdigest()
    packet_hash = video_hash(video)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-n", "-v", "error", "-i", str(video), "-i", str(voice),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-af", "apad", "-t", f"{duration:.6f}", "-movflags", "+faststart", str(output),
    ], check=True)
    result = probe(output)
    if abs(float(result["format"]["duration"]) - duration) > .1:
        raise RuntimeError("Muxed duration differs from the video by more than 100 ms")
    if video_hash(output) != packet_hash or hashlib.sha256(video.read_bytes()).hexdigest() != source_sha:
        raise RuntimeError("Video packet stream or original file changed")
    return {"status": "passed", "voice": str(voice), "video": str(video), "output": str(output),
            "video_duration_seconds": duration, "voice_duration_seconds": voice_duration,
            "output_duration_seconds": float(result["format"]["duration"]),
            "video_codec": "copy", "video_packet_sha256": packet_hash.split("=", 1)[-1],
            "source_sha256": source_sha, "silence_padded": voice_duration < duration,
            "note": "Owner-provided voice only; no speech generation, no video re-encoding, no uploads"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(mix(args.voice, args.video, args.output), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"mix_voice: {error}\n")


if __name__ == "__main__":
    main()
