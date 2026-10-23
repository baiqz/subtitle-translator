"""一键准备环境：装依赖、备好 ffmpeg。跑完就能用。

设计原则：能用就不动，缺什么装什么，报错说人话。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
REQ = os.path.join(ROOT, "requirements.txt")

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PKGS = ["flask>=3.0", "numpy>=1.24", "faster-whisper>=1.1"]


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd, cwd=ROOT)
    return p.returncode


# --------------------------------------------------------------- 依赖

def missing_pkgs() -> list[str]:
    probe = (
        "import importlib, sys\n"
        "need = {'flask':'flask','numpy':'numpy','faster_whisper':'faster-whisper'}\n"
        "bad = []\n"
        "for mod, name in need.items():\n"
        "    try: importlib.import_module(mod)\n"
        "    except Exception: bad.append(name)\n"
        "print(' '.join(bad))\n"
    )
    try:
        out = subprocess.run([sys.executable, "-c", probe],
                             capture_output=True, text=True, timeout=60)
        return (out.stdout or "").split()
    except Exception:
        return ["flask", "numpy", "faster-whisper"]


def install_pkgs(pkgs: list[str]) -> bool:
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    # 先走境内镜像，失败再走默认源
    for extra in (["-i", MIRROR], []):
        if run(base + extra + pkgs) == 0:
            return True
        say("  这一轮没装上，换默认源再试一次…")
    return False


# --------------------------------------------------------------- ffmpeg

def ffmpeg_ready() -> str:
    """返回可用的 ffmpeg 目录，没有返回空串。"""
    sys.path.insert(0, ROOT)
    try:
        from core import config  # noqa: WPS433
        d = config.find_ffmpeg()
        if d:
            return d
    except Exception:
        pass
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for d in (os.path.join(ROOT, "bin"),):
        if os.path.isfile(os.path.join(d, exe)):
            return d
    return ""


def ensure_ffmpeg() -> str:
    """没有 ffmpeg 就用 imageio-ffmpeg 的静态二进制兜底 —— 比让用户自己配 Path 靠谱得多。"""
    got = ffmpeg_ready()
    if got:
        return got

    say("  本机没找到 ffmpeg，装一个便携版（约 85MB，纯静态文件不写注册表）…")
    if run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "-i", MIRROR, "imageio-ffmpeg"]) != 0:
        return ""

    try:
        import imageio_ffmpeg  # noqa: WPS433
        src = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        say(f"  拿不到便携版 ffmpeg：{e}")
        return ""

    os.makedirs(BIN, exist_ok=True)
    dst = os.path.join(BIN, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    try:
        shutil.copy2(src, dst)          # 统一改名，程序只认 ffmpeg(.exe)
        for extra in ("ffprobe",):
            cand = os.path.join(os.path.dirname(src),
                                extra + (".exe" if os.name == "nt" else ""))
            if os.path.isfile(cand):
                shutil.copy2(cand, os.path.join(BIN, os.path.basename(cand)))
        try:
            os.chmod(dst, 0o755)
        except Exception:
            pass
        return BIN if os.path.isfile(dst) else ""
    except Exception as e:
        say(f"  复制 ffmpeg 失败：{e}")
        return ""


# --------------------------------------------------------------- 主流程

def main() -> int:
    say("=" * 52)
    say("  字幕翻译工作台 · 环境准备")
    say("=" * 52)
    say(f"  Python {sys.version.split()[0]}")
    say(f"  目录   {ROOT}")

    total = 3

    step(1, total, "检查 Python 依赖")
    bad = missing_pkgs()
    if bad:
        say(f"  缺：{', '.join(bad)}  → 开始安装（首次约 200MB，走清华源）")
        if not install_pkgs(bad):
            say("\n  依赖没装上。常见原因：")
            say("   · 网络不通 —— 换个网络或手动跑：")
            say(f"     {sys.executable} -m pip install -r requirements.txt")
            return 1
        say("  装好了")
    else:
        say("  依赖齐全，跳过")

    step(2, total, "检查 ffmpeg")
    ff = ensure_ffmpeg()
    if not ff:
        say("  没有 ffmpeg —— 只有「上传音频」这条路需要它。")
        say("  纯字幕翻译照样能用；要处理音频请手动装 ffmpeg 并加进 PATH。")
    else:
        say(f"  可用：{ff}")

    step(3, total, "检查语音识别模型")
    sys.path.insert(0, ROOT)
    try:
        from core import asr  # noqa: WPS433
        found = asr.find_model("small")
    except Exception:
        found = None
    if found:
        say(f"  已有：{found}")
    else:
        say("  还没有（约 480MB）。不用现在下 ——")
        say("  第一次处理音频时会自动下载，只下一次。")

    say("\n" + "=" * 52)
    say("  准备完成，正在启动…")
    say("=" * 52)
    return 0


if __name__ == "__main__":
    sys.exit(main())
