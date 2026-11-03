"""設定ファイル（TOML）の [jtk.table.pii] を読む。"""

import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from jtk.errors import JtkError

_STR_KEYS = {
    "preset", "scope", "hash", "hash_column", "out_dir", "out_name",
    "stem_pattern", "stem_replace", "move_original_dir",
}
_LIST_KEYS = {"pii", "omit"}


@dataclass
class PiiSettings:
    preset: str | None = None
    scope: str | None = None
    hash: str | None = None
    hash_column: str | None = None
    pii: list[str] = field(default_factory=list)
    omit: list[str] = field(default_factory=list)
    out_dir: Path | None = None
    out_name: str = "{stem}"
    stem_pattern: re.Pattern[str] | None = None
    stem_replace: str | None = None
    move_original_dir: Path | None = None
    source: Path | None = None


def load_pii_settings(path: Path) -> PiiSettings:
    name = path.name
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise JtkError(f"設定ファイル {name} が見つかりません") from None
    except OSError:
        raise JtkError(f"設定ファイル {name} を読めません") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise JtkError(f"設定ファイル {name} を TOML として読めません") from None

    node = data
    for i, key in enumerate(("jtk", "table", "pii")):
        if key not in node:
            return PiiSettings(source=path)
        node = node[key]
        if not isinstance(node, dict):
            dotted = ".".join(("jtk", "table", "pii")[: i + 1])
            raise JtkError(f"設定ファイル {name}: [{dotted}] は表でなければなりません")

    for key, value in node.items():
        if key in _STR_KEYS:
            if not isinstance(value, str):
                raise JtkError(f"設定ファイル {name}: {key} は文字列でなければなりません")
        elif key in _LIST_KEYS:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise JtkError(f"設定ファイル {name}: {key} は文字列の配列でなければなりません")
        else:
            raise JtkError(f"設定ファイル {name}: 知らないキー {key} があります")

    s = PiiSettings(source=path)
    for key in ("preset", "scope", "hash", "hash_column", "stem_replace"):
        setattr(s, key, node.get(key))
    s.pii = list(node.get("pii", []))
    s.omit = list(node.get("omit", []))

    base = path.absolute().parent
    if "out_dir" in node:
        s.out_dir = (base / node["out_dir"]).resolve()
    if "move_original_dir" in node:
        s.move_original_dir = (base / node["move_original_dir"]).resolve()

    if "out_name" in node:
        out_name = node["out_name"]
        rest = out_name.replace("{stem}", "")
        if (
            "{" in rest
            or "}" in rest
            or "/" in out_name
            or "\\" in out_name
            or out_name.lower().endswith((".xlsx", ".csv"))
        ):
            raise JtkError(
                f"設定ファイル {name}: out_name には {{stem}} だけを使い、"
                "区切り文字や拡張子を入れないでください"
            )
        s.out_name = out_name

    has_pattern = "stem_pattern" in node
    if has_pattern != ("stem_replace" in node):
        missing = "stem_replace" if has_pattern else "stem_pattern"
        raise JtkError(f"設定ファイル {name}: stem_pattern と stem_replace は両方必要です（{missing} がありません）")
    if has_pattern:
        try:
            s.stem_pattern = re.compile(unicodedata.normalize("NFC", node["stem_pattern"]))
        except re.error:
            raise JtkError(f"設定ファイル {name}: stem_pattern が正規表現として正しくありません") from None
    return s


def output_path(settings: PiiSettings, input_path: Path) -> Path:
    if settings.out_dir is None:
        raise JtkError("出力先がありません。--out か設定ファイルの out_dir を指定してください")
    stem = unicodedata.normalize("NFC", input_path.stem)
    if settings.stem_pattern is not None:
        if not settings.stem_pattern.search(stem):
            raise JtkError("入力のファイル名が stem_pattern に一致しません")
        try:
            stem = settings.stem_pattern.sub(settings.stem_replace, stem, count=1)
        except (re.error, IndexError):
            raise JtkError("stem_replace が正しくありません（存在しないグループの参照など）") from None
        if not stem or "/" in stem or "\\" in stem:
            raise JtkError("stem_pattern で書き換えたファイル名が空か、区切り文字を含みます")
    return settings.out_dir / (settings.out_name.replace("{stem}", stem) + input_path.suffix)


def moved_original_path(settings: PiiSettings, input_path: Path) -> Path | None:
    if settings.move_original_dir is None:
        return None
    return settings.move_original_dir / input_path.name
