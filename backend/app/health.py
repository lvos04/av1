"""Eerlijke dependency- en QSV-diagnostiek voor de healthcheck."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any


async def _output(arguments: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (FileNotFoundError, TimeoutError):
        return 127, ""
    return process.returncode or 0, stdout.decode("utf-8", errors="replace")


async def dependency_report() -> dict[str, Any]:
    tools = {
        name: shutil.which(name) is not None
        for name in ("ffmpeg", "ffprobe", "mkvmerge", "dovi_tool")
    }
    version_code, version_output = await _output(["ffmpeg", "-version"])
    version_match = re.search(r"ffmpeg version\s+(\S+)", version_output)
    encoders_code, encoders_output = await _output(["ffmpeg", "-hide_banner", "-encoders"])
    dolby_code, dolby_output = await _output(["ffmpeg", "-hide_banner", "-h", "encoder=libsvtav1"])
    vpl_code, vpl_output = await _output(["ldconfig", "-p"])
    vaapi_code, vaapi_output = await _output(
        ["vainfo", "--display", "drm", "--device", "/dev/dri/renderD128"]
    )
    render_node = Path("/dev/dri/renderD128")
    render_readable = render_node.exists() and os.access(render_node, os.R_OK)
    render_writable = render_node.exists() and os.access(render_node, os.W_OK)
    libsvtav1 = encoders_code == 0 and "libsvtav1" in encoders_output
    av1_qsv = encoders_code == 0 and "av1_qsv" in encoders_output
    dolbyvision = dolby_code == 0 and "dolbyvision" in dolby_output.lower()
    qsv_ready = av1_qsv and render_readable and render_writable
    return {
        "tools": tools,
        "ffmpeg_version": version_match.group(1) if version_match else None,
        "libsvtav1": libsvtav1,
        "av1_qsv": av1_qsv,
        "dolbyvision": dolbyvision,
        "onevpl": vpl_code == 0 and "libvpl.so" in vpl_output,
        "vaapi": vaapi_code == 0 and "VAProfile" in vaapi_output,
        "render_node": {
            "pad": str(render_node),
            "aanwezig": render_node.exists(),
            "leesbaar": render_readable,
            "schrijfbaar": render_writable,
        },
        "qsv_gereed": qsv_ready,
        "media_stack_gereed": version_code == 0 and libsvtav1 and dolbyvision,
    }
