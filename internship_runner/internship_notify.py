"""Voice annotations via Edge neural TTS, with transcript export for 3rd-party voice-over."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("internship_notify")

_SCRIPT_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _SCRIPT_DIR / "internship_config.json"


def load_config() -> dict[str, Any]:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def stage_message(config: dict[str, Any], key: str, **kwargs: object) -> str:
    messages = config.get("stage_messages") or {}
    template = str(messages.get(key) or "").strip()
    if not template:
        return ""
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


def _voice_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("voice") or {}


def _transcript_path(voice: dict[str, Any], stage_key: str) -> Path:
    base = _SCRIPT_DIR / voice.get("transcript_dir", "voice/transcripts")
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"{stamp}_{stage_key}.txt"


def _audio_path(voice: dict[str, Any], stage_key: str) -> Path:
    base = _SCRIPT_DIR / voice.get("audio_dir", "voice/audio")
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"{stamp}_{stage_key}.mp3"


def _append_manifest(voice: dict[str, Any], entry: dict[str, Any]) -> None:
    manifest = _SCRIPT_DIR / voice.get("manifest_file", "voice/voice_manifest.jsonl")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_transcript(path: Path, *, role: str, stage_key: str, text: str) -> None:
    header = (
        f"# Internship workflow narration\n"
        f"# Role: {role}\n"
        f"# Stage: {stage_key}\n"
        f"# Use this file with ElevenLabs, Murf, Play.ht, or similar.\n\n"
    )
    path.write_text(header + text.strip() + "\n", encoding="utf-8")


async def _synthesize_edge_tts(text: str, output_mp3: Path, voice_id: str, rate: str, volume: str) -> bool:
    try:
        import edge_tts
    except ImportError:
        logger.info("edge-tts not installed — transcript only. pip install edge-tts")
        return False

    communicate = edge_tts.Communicate(text, voice=voice_id, rate=rate, volume=volume)
    await communicate.save(str(output_mp3))
    return output_mp3.is_file()


def _auto_run_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("auto_run") or {}


def _pause_seconds(config: dict[str, Any], key: str, default: float) -> float:
    auto = _auto_run_cfg(config)
    try:
        return max(0.0, float(auto.get(key, default)))
    except (TypeError, ValueError):
        return default


def pause_after_stage(config: dict[str, Any], *, auto: bool) -> None:
    if not auto:
        return
    sec = _pause_seconds(config, "pause_after_stage_sec", 2.0)
    if sec > 0:
        logger.info("Pause %.1fs after narration...", sec)
        time.sleep(sec)


def pause_between_roles(config: dict[str, Any], *, auto: bool) -> None:
    if not auto:
        return
    sec = _pause_seconds(config, "pause_between_roles_sec", 4.0)
    if sec > 0:
        logger.info("Pause %.1fs before next role...", sec)
        time.sleep(sec)


def _estimate_speech_seconds(text: str) -> float:
    words = max(1, len((text or "").split()))
    return max(2.0, words / 2.5)


def _play_audio(path: Path, *, blocking: bool = False, fallback_text: str = "") -> None:
    if not path.is_file():
        return
    if blocking:
        try:
            from playsound import playsound

            playsound(str(path), block=True)
            return
        except Exception as exc:
            logger.debug("Blocking audio fallback (%s)", exc)

        try:
            import shutil
            import subprocess

            if shutil.which("ffplay"):
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", str(path)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
        except Exception:
            pass

        wait = _estimate_speech_seconds(fallback_text)
        logger.info("Waiting %.1fs for narration timing...", wait)
        time.sleep(wait)
        return

    try:
        if sys.platform == "win32":
            import os

            os.startfile(str(path))  # noqa: S606
        else:
            import shutil
            import subprocess

            if shutil.which("ffplay"):
                subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif shutil.which("afplay"):
                subprocess.Popen(["afplay", str(path)])
    except Exception as exc:
        logger.warning("Could not play audio %s: %s", path.name, exc)

def annotate_stage(
    stage_key: str,
    *,
    role: str,
    config: dict[str, Any] | None = None,
    auto: bool = False,
    **message_kwargs: object,
) -> dict[str, Any]:
    """
    Speak a stage annotation (Edge TTS if available) and always save transcript + manifest.
    Returns paths and whether audio was generated.
    """
    cfg = config or load_config()
    voice = _voice_cfg(cfg)
    if not voice.get("enabled", True):
        return {"skipped": True}

    text = stage_message(cfg, stage_key, **message_kwargs)
    if not text:
        return {"skipped": True, "reason": "empty_message"}

    transcript_path = _transcript_path(voice, stage_key)
    audio_path = _audio_path(voice, stage_key)
    result: dict[str, Any] = {
        "stage": stage_key,
        "role": role,
        "text": text,
        "transcript_file": str(transcript_path.relative_to(_SCRIPT_DIR)),
        "audio_file": "",
        "audio_generated": False,
        "engine": voice.get("engine", "edge_tts"),
    }

    if voice.get("always_write_transcript", True):
        _write_transcript(transcript_path, role=role, stage_key=stage_key, text=text)
        logger.info("Transcript saved: %s", transcript_path)

    audio_ok = False
    if voice.get("engine", "edge_tts") == "edge_tts":
        audio_ok = asyncio.run(
            _synthesize_edge_tts(
                text,
                audio_path,
                str(voice.get("voice_id", "en-US-JennyNeural")),
                str(voice.get("rate", "+0%")),
                str(voice.get("volume", "+0%")),
            )
        )

    if audio_ok:
        result["audio_file"] = str(audio_path.relative_to(_SCRIPT_DIR))
        result["audio_generated"] = True
        logger.info("Audio saved: %s", audio_path)
        if voice.get("play_audio", True):
            blocking = auto and bool(_auto_run_cfg(cfg).get("blocking_audio", True))
            _play_audio(audio_path, blocking=blocking, fallback_text=text)
    else:
        logger.info("No audio generated — use transcript with your voice-over tool.")
        if auto:
            time.sleep(_estimate_speech_seconds(text))

    pause_after_stage(cfg, auto=auto)

    _append_manifest(
        voice,
        {
            **result,
            "voice_id": voice.get("voice_id", "en-US-JennyNeural"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    return result


def export_all_transcripts(output_dir: str | Path | None = None) -> Path:
    """Copy latest transcript per stage into a folder for bulk voice-over."""
    cfg = load_config()
    voice = _voice_cfg(cfg)
    src = _SCRIPT_DIR / voice.get("transcript_dir", "voice/transcripts")
    dest = Path(output_dir or (_SCRIPT_DIR / "voice" / "export_for_voiceover"))
    dest.mkdir(parents=True, exist_ok=True)

    if not src.is_dir():
        return dest

    copied = 0
    for path in sorted(src.glob("*.txt")):
        target = dest / path.name
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        copied += 1

    readme = dest / "README.txt"
    readme.write_text(
        "Import these .txt files into your voice-over tool (ElevenLabs, Murf, Play.ht, etc.).\n"
        "Each file header lists the role and stage.\n"
        f"Copied {copied} transcript(s).\n",
        encoding="utf-8",
    )
    return dest
