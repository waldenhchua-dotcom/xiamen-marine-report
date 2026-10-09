"""Portable, openly licensed typography shared by raster and PDF renderers."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager, rcParams  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402

FONT_PATH = Path(__file__).parent / "assets" / "NotoSansSC.ttf"
FONT_BOLD_PATH = Path(__file__).parent / "assets" / "NotoSansSC-Bold.ttf"
INK = "#19344b"
BLUE = "#187eae"
TEAL = "#219b93"
AMBER = "#e49426"
RED = "#b93435"
MUTED = "#587082"
PALE = "#eff5f7"
FONT_NAME = "MarineNotoSC"
FONT_BOLD_NAME = "MarineNotoSCBold"


def configure_fonts() -> None:
    font_manager.fontManager.addfont(str(FONT_PATH))
    font_manager.fontManager.addfont(str(FONT_BOLD_PATH))
    family = font_manager.FontProperties(fname=str(FONT_PATH)).get_name()
    rcParams.update({
        "font.family": family, "font.size": 11, "axes.unicode_minus": False,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.labelcolor": INK, "text.color": INK, "xtick.color": MUTED,
        "ytick.color": MUTED, "savefig.facecolor": "white",
    })
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        pdfmetrics.registerFont(TTFont(FONT_BOLD_NAME, str(FONT_BOLD_PATH)))


def status_label(manifest: dict, partial: bool) -> str:
    parts = ["UNAPPROVED_REFERENCE"]
    if manifest.get("synthetic"):
        parts.append("SYNTHETIC 合成数据")
    if partial:
        parts.append("PARTIAL 部分查看版")
    return " | ".join(parts)
