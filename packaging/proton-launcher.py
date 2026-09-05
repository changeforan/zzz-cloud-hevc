#!/usr/bin/env python3
"""Release entry point: HEVC defaults before Proton configures its prefix."""

import os
from pathlib import Path
import sys


def launch_environment(original):
    env = original.copy()
    # A separate switch distinguishes an intentional override from Gaming Mode's
    # inherited dxgi=n, which must be replaced for the default wined3d path.
    if env.get("PROTON_HEVC_DEFAULTS", "1") == "0":
        return env
    env.setdefault("PROTON_USE_WINED3D", "1")
    env.setdefault("WINE_D3D_CONFIG", "renderer=vulkan")
    if env["PROTON_USE_WINED3D"] != "0":
        overrides = []
        for entry in env.get("WINEDLLOVERRIDES", "").split(";"):
            if not entry.strip():
                continue
            names, separator, value = entry.partition("=")
            if not separator:
                overrides.append(entry)
                continue
            names = [name.strip() for name in names.split(",")
                     if name.strip().lower().lstrip("*").removesuffix(".dll") != "dxgi"]
            if names:
                overrides.append(",".join(names) + "=" + value)
        overrides.append("dxgi=b")
        env["WINEDLLOVERRIDES"] = ";".join(overrides)
    return env


if __name__ == "__main__":
    original = Path(__file__).resolve().with_name("proton-original")
    os.execve(sys.executable, [sys.executable, str(original), *sys.argv[1:]],
              launch_environment(os.environ))
