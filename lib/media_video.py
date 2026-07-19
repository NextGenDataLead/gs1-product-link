"""Video → GTIN mapping and web-prep for the Noviplast pilot (Phase 9.5 media).

Videos are **not** in the GDSN feed (all 375 feed media are ``PRODUCT_IMAGE``). The operator
supplies two local folders (nl, fr) whose files are named by **English marketing name**
(``DrainSticks_NL.mpeg``, ``WaspTrap_NL_Small.mpg``) — names that mostly appear nowhere in the
feed, so the tool cannot reliably auto-match them to GTINs. The mapping is therefore
**operator-authored**: this module only

* normalizes a filename to a display token (:func:`normalize_video_name`) used for hints/keying,
* ranks fuzzy feed candidates as *hints* for the human (:func:`rank_candidates`),
* loads and validates the human-confirmed ``mapping.yml`` (:class:`VideoMap`,
  :func:`check_video_map`), and
* prepares a matched file for the web (:func:`prepare_video`) — an optional ffmpeg transcode,
  since the source ``.mpg``/``.mpeg`` (MPEG-1/2) will not play in an HTML5 ``<video>``.

Like :mod:`lib.categories`, it *reports rather than guesses*: an unconfirmed or ambiguous entry
is surfaced as a :class:`~lib.records.SourceIssue`, never uploaded.
"""

from __future__ import annotations

import logging
import re
import subprocess  # noqa: S404 — ffmpeg is invoked with a fixed, non-shell argv
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

import yaml
from pydantic import BaseModel, ConfigDict

from lib.records import ProductRecord, SourceIssue

_log = logging.getLogger(__name__)

#: Video file extensions recognised in an operator folder.
_VIDEO_EXTS = frozenset({".mpg", ".mpeg", ".mp4", ".mov", ".m4v", ".webm"})
#: Directory names to skip when scanning a folder (macOS/Windows volume cruft).
_IGNORE_DIRS = frozenset({"System Volume Information"})

#: Standalone tokens dropped during normalization (language + size/version markers).
_LANG_TOKENS = frozenset({"nl", "fr", "en", "de"})
_SIZE_TOKENS = frozenset({"small", "large", "medium", "new"})
_VERSION_RE = re.compile(r"^v\d+$")
#: Insert a boundary between a lower/digit and an upper letter, to split camelCase.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Sentinel gtin value meaning "this video intentionally maps to no product" (not a gap).
_SKIP = "skip"


def normalize_video_name(filename: str) -> str:
    """Reduce a video filename to a canonical display token for matching/keying.

    Drops the extension, splits camelCase, replaces separators with spaces, and removes
    standalone language (``nl``/``fr``) and size/version (``small``/``v2``/``new``) markers,
    then lowercases and collapses whitespace. ``BeanieBrite_NL_SmallV2.mpg`` → ``beanie brite``.
    Used only to build hints and to eyeball the draft — never as an authoritative match.
    """
    stem = Path(filename).stem
    spaced = _CAMEL_RE.sub(" ", stem).replace("_", " ").replace("-", " ")
    tokens = [t for t in spaced.split() if t]
    kept = [
        t
        for t in tokens
        if t.lower() not in _LANG_TOKENS
        and t.lower() not in _SIZE_TOKENS
        and not _VERSION_RE.match(t.lower())
    ]
    return " ".join(kept).lower().strip()


def list_video_files(folder: Path) -> list[Path]:
    """Return the video files directly in ``folder``, sorted, skipping cruft.

    Ignores dotfiles and the ``System Volume Information`` directory; non-video files are
    excluded by extension. Non-recursive: the operator's folders are flat.
    """
    if not folder.is_dir():
        return []
    return sorted(
        p
        for p in folder.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.parent.name not in _IGNORE_DIRS
        and p.suffix.lower() in _VIDEO_EXTS
    )


class VideoCandidate(NamedTuple):
    """A ranked fuzzy match of a video name against one feed field — a hint, not a decision."""

    gtin: str
    name: str
    field: str
    score: float


def _candidate_fields(product: ProductRecord) -> list[tuple[str, str]]:
    """Return ``(field_label, value)`` pairs on ``product`` worth matching a video name against."""
    pairs: list[tuple[str, str]] = [
        (f"product_name.{lang}", value) for lang, value in product.product_name.values.items()
    ]
    for key in ("marketing_name", "functional_name", "logistics_name"):
        value = product.extras.get(key)
        if value:
            pairs.append((f"extras.{key}", value))
    return pairs


def rank_candidates(
    normalized: str, products: list[ProductRecord], *, top_n: int = 3
) -> list[VideoCandidate]:
    """Rank ``products`` against a normalized video name; return the top ``top_n`` hints.

    Scores each product by the best :class:`~difflib.SequenceMatcher` ratio across its
    ``product_name`` (all languages) and ``extras`` marketing/functional/logistics names.
    Hints only — the operator decides the actual GTIN.
    """
    scored: list[VideoCandidate] = []
    for product in products:
        best: VideoCandidate | None = None
        for field, value in _candidate_fields(product):
            ratio = SequenceMatcher(None, normalized, normalize_value(value)).ratio()
            if best is None or ratio > best.score:
                best = VideoCandidate(product.gtin, value, field, ratio)
        if best is not None:
            scored.append(best)
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_n]


def normalize_value(value: str) -> str:
    """Normalize a feed string the same way as a video name, for comparable matching."""
    spaced = _CAMEL_RE.sub(" ", value).replace("_", " ").replace("-", " ")
    return " ".join(spaced.split()).lower().strip()


class VideoMapEntry(BaseModel):
    """One row of the confirmed mapping: a filename and the operator-filled GTIN."""

    model_config = ConfigDict(frozen=True)

    file: str
    gtin: str = ""


class VideoMap(BaseModel):
    """The client-confirmed ``{language: [{file, gtin}]}`` video mapping."""

    model_config = ConfigDict(frozen=True)

    by_language: dict[str, list[VideoMapEntry]]

    def resolve(self, gtin: str, language: str) -> str | None:
        """Return the confirmed filename for ``(gtin, language)``, or ``None``.

        ``None`` when the pair is absent, the GTIN is blank/``skip``, or the GTIN is
        confirmed to more than one file in that language (ambiguous — reported elsewhere).
        """
        matches = [
            e.file
            for e in self.by_language.get(language, [])
            if e.gtin and e.gtin.lower() != _SKIP and e.gtin == gtin
        ]
        if len(matches) == 1:
            return matches[0]
        return None


def load_video_map(path: Path) -> VideoMap:
    """Load and validate the confirmed mapping YAML (``{lang: [{file, gtin}]}``)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return VideoMap.model_validate({"by_language": raw})


def check_video_map(vmap: VideoMap, files_by_language: dict[str, list[str]]) -> list[SourceIssue]:
    """Return the coverage gaps in ``vmap`` against the actual folder contents.

    Emits one :class:`~lib.records.SourceIssue` per gap: an entry with a blank GTIN
    (``video_unconfirmed``), a GTIN confirmed to two files in one language
    (``video_ambiguous``), a file on disk absent from the map (``video_missing_from_map``),
    or a map entry naming a file not on disk (``video_file_missing``). A ``skip`` GTIN is an
    intentional non-mapping, not a gap. An empty list means the mapping is complete.
    """
    issues: list[SourceIssue] = []
    for language, disk_names in _all_languages(vmap, files_by_language):
        entries = vmap.by_language.get(language, [])
        map_files = {e.file for e in entries}
        disk_files = set(disk_names)

        for entry in entries:
            gtin = entry.gtin.strip()
            if not gtin:
                issues.append(
                    _issue(language, entry.file, "video_unconfirmed", "no GTIN filled in yet")
                )

        real = [e for e in entries if e.gtin and e.gtin.lower() != _SKIP]
        for gtin, count in Counter(e.gtin for e in real).items():
            if count > 1:
                issues.append(
                    SourceIssue(
                        gtin=gtin,
                        field=f"video.{language}",
                        source="operator video folder",
                        issue="video_ambiguous",
                        value=", ".join(sorted(e.file for e in real if e.gtin == gtin)),
                        detail=f"GTIN mapped to {count} files in {language}; keep one.",
                    )
                )

        for name in sorted(disk_files - map_files):
            issues.append(
                _issue(language, name, "video_missing_from_map", "file on disk not in the map")
            )
        for name in sorted(map_files - disk_files):
            issues.append(
                _issue(language, name, "video_file_missing", "map names a file not on disk")
            )
    return issues


def _all_languages(
    vmap: VideoMap, files_by_language: dict[str, list[str]]
) -> list[tuple[str, list[str]]]:
    """Every language appearing in the map or the folders, with its disk filenames."""
    languages = sorted(set(vmap.by_language) | set(files_by_language))
    return [(lang, files_by_language.get(lang, [])) for lang in languages]


def _issue(language: str, filename: str, kind: str, detail: str) -> SourceIssue:
    return SourceIssue(
        gtin="",
        field=f"video.{language}",
        source="operator video folder",
        issue=kind,
        value=filename,
        detail=detail,
    )


# --- Web preparation (transcode) ---------------------------------------------

#: Deterministic-ish ffmpeg flags: H.264/AAC MP4, faststart for web, metadata stripped so the
#: output does not carry a wall-clock timestamp that would churn the upload dedupe hash.
_FFMPEG_FLAGS = (
    "-c:v",
    "libx264",
    "-crf",
    "23",
    "-preset",
    "medium",
    "-c:a",
    "aac",
    "-movflags",
    "+faststart",
    "-map_metadata",
    "-1",
)


def prepare_video(
    src: Path, dest_dir: Path, *, transcode: bool, ffmpeg_bin: str = "ffmpeg"
) -> Path | None:
    """Return an upload-ready video path for ``src``.

    With ``transcode=False`` the source is returned unchanged (upload the ``.mpg`` as-is).
    With ``transcode=True`` the file is transcoded to H.264/AAC MP4 at ``dest_dir/{stem}.mp4``;
    an existing destination is reused (idempotency short-circuit, before the upload hash check).
    Returns ``None`` if ffmpeg fails or is missing, so a bad video is skipped and the page still
    publishes.
    """
    if not transcode:
        return src
    dest = dest_dir / f"{src.stem}.mp4"
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin, "-y", "-i", str(src), *_FFMPEG_FLAGS, str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603 — fixed argv, no shell
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        _log.warning("ffmpeg transcode failed for %s: %r (skipping video)", src.name, exc)
        return None
    return dest
