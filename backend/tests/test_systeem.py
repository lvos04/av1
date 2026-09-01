from __future__ import annotations

from app.systeem import GpuMonitor, _gpu_values, _platform_power_value


def test_gpu_monitor_leest_video_render_en_rapl_vermogen() -> None:
    values = _gpu_values(
        {
            "frequency": {"actual": 2200.0},
            "engines": {
                "Render/3D": {"busy": 37.5},
                "Video": {"busy": 81.25},
                "VideoEnhance": {"busy": 12.0},
            },
            "power": {"GPU": 8.6, "Package": 73.7, "unit": "W"},
        }
    )

    assert values == {
        "video_percent": 81.25,
        "render_percent": 37.5,
        "freq_mhz": 2200.0,
        "power_w": 8.6,
        "package_power_w": 73.7,
        "beschikbaar": True,
    }


def test_gpu_monitor_begint_zonder_verzonnen_metingen() -> None:
    monitor = GpuMonitor()

    assert monitor.latest["beschikbaar"] is False
    assert monitor.latest["power_w"] is None
    assert monitor.latest["package_power_w"] is None
    assert monitor.latest["platform_power_w"] is None
    assert monitor.latest["platform_power_beschikbaar"] is False


def test_psys_monitor_accepteert_alleen_aannemelijk_vermogen() -> None:
    assert _platform_power_value("104.17") == 104.17
    assert _platform_power_value("geen meting") is None
    assert _platform_power_value("nan") is None
    assert _platform_power_value("10001") is None
