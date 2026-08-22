# Kanji Neighbours — click a kanji in the header word to see related reviewed vocab.
# AGPL-3.0-or-later

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

from anki.cards import Card
from anki.notes import Note
from anki.utils import strip_html
from aqt import dialogs, gui_hooks, mw
from aqt.qt import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMouseEvent,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import tooltip

ADDON_NAME = "Kanji Neighbours"
CMD_PREFIX = "kanji_neighbours:"

SOUND_RE = re.compile(r"\[sound:[^\]]*\]", re.I)
RUBY_RE = re.compile(r"<rt\b[^>]*>.*?</rt>|<rp\b[^>]*>.*?</rp>", re.I | re.S)
STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
OTHER_TAGS_RE = re.compile(r"<[^>]+>")
HORIZONTAL_SPACE_RE = re.compile(r"[^\S\n]+")
BLOCK_BREAK_RE = re.compile(
    r"<br\s*/?>|</(?:p|div|li|tr|h[1-6]|blockquote|section|article|ul|ol|table)\s*>",
    re.I,
)
LI_OPEN_RE = re.compile(r"<li\b[^>]*>", re.I)
BLOCK_OPEN_RE = re.compile(
    r"<(?:p|div|h[1-6]|blockquote|tr|ul|ol|table)\b[^>]*>",
    re.I,
)

DEFAULT_SHOW_IN_POPUP = {
    "word": True,
    "reading": True,
    "definition": True,
    "sentence": True,
    "native": True,
}

DEFAULT_WORD_FIELDS: list[str] = []
DEFAULT_READING_FIELDS: list[str] = []
DEFAULT_DEFINITION_FIELDS: list[str] = []
DEFAULT_SENTENCE_FIELDS: list[str] = []
DEFAULT_ENGLISH_SENTENCE_FIELDS: list[str] = []
DEFAULT_WORD_SELECTORS = ["#word", ".kanji-neighbours-word", ".kanji-lookup-word"]

INJECT_JS = r"""
(function () {
  const SELECTORS = %SELECTORS%;

  if (!document.getElementById("kanji-neighbours-style")) {
    const style = document.createElement("style");
    style.id = "kanji-neighbours-style";
    style.textContent = `
      .kanji-neighbours-root {
        cursor: pointer;
      }
      .kanji-neighbours-root ruby,
      .kanji-neighbours-root .kanjiHoverTarget {
        cursor: pointer;
      }
    `;
    document.head.appendChild(style);
  }

  const KANJI = /[\u3400-\u9FFF\uF900-\uFAFF]/;

  function roots() {
    const found = [];
    for (const sel of SELECTORS) {
      try {
        document.querySelectorAll(sel).forEach((el) => found.push(el));
      } catch (e) {}
    }
    return found;
  }

  function charFromPoint(x, y) {
    let node = null;
    let offset = 0;
    if (document.caretRangeFromPoint) {
      const range = document.caretRangeFromPoint(x, y);
      if (!range) return null;
      node = range.startContainer;
      offset = range.startOffset;
    } else if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(x, y);
      if (!pos) return null;
      node = pos.offsetNode;
      offset = pos.offset;
    } else {
      return null;
    }
    if (!node) return null;
    if (node.nodeType === Node.ELEMENT_NODE) {
      const text = node.textContent || "";
      return text && KANJI.test(text[0]) ? text[0] : null;
    }
    if (node.nodeType !== Node.TEXT_NODE) return null;
    if (node.parentElement && node.parentElement.closest("rt, rp")) return null;
    const text = node.nodeValue || "";
    const candidates = [];
    if (offset < text.length) candidates.push(text[offset]);
    if (offset > 0) candidates.push(text[offset - 1]);
    for (const ch of candidates) {
      if (ch && KANJI.test(ch)) return ch;
    }
    return null;
  }

  function onClick(e) {
    const root = e.currentTarget;
    if (e.target.closest("rt, rp, a, button, .audio")) return;

    const hoverTarget = e.target.closest(".kanjiHoverTarget");
    let ch = null;
    if (hoverTarget && root.contains(hoverTarget)) {
      const t = (hoverTarget.textContent || "").trim();
      if (t.length === 1 && KANJI.test(t)) ch = t;
    }
    if (!ch) ch = charFromPoint(e.clientX, e.clientY);
    if (!ch) return;

    e.preventDefault();
    e.stopPropagation();
    if (typeof pycmd === "function") pycmd("kanji_neighbours:" + ch);
  }

  for (const root of roots()) {
    if (root.dataset.kanjiNeighboursBound === "1") continue;
    root.dataset.kanjiNeighboursBound = "1";
    root.classList.add("kanji-neighbours-root");
    root.addEventListener("click", onClick);
  }
})();
"""


def _cfg() -> dict[str, Any]:
    conf = mw.addonManager.getConfig(__name__)
    return conf if isinstance(conf, dict) else {}


def _as_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        out = [str(v).strip() for v in value if str(v).strip()]
        return out or list(fallback)
    return list(fallback)


def _field_lists(conf: dict[str, Any], note_type: str | None = None) -> dict[str, list[str]]:
    base = {
        "word": _as_list(
            conf.get("word_fields", conf.get("word_field")),
            DEFAULT_WORD_FIELDS,
        ),
        "reading": _as_list(
            conf.get("reading_fields", conf.get("reading_field")),
            DEFAULT_READING_FIELDS,
        ),
        "definition": _as_list(
            conf.get("definition_fields", conf.get("definition_field")),
            DEFAULT_DEFINITION_FIELDS,
        ),
        "sentence": _as_list(
            conf.get("sentence_fields", conf.get("sentence_field")),
            DEFAULT_SENTENCE_FIELDS,
        ),
        "native": _as_list(
            conf.get(
                "native_sentence_fields",
                conf.get("english_sentence_fields", conf.get("english_sentence_field")),
            ),
            DEFAULT_ENGLISH_SENTENCE_FIELDS,
        ),
    }

    note_map = conf.get("note_type_map") or {}
    if note_type and isinstance(note_map, dict) and note_type in note_map:
        override = note_map[note_type] or {}
        if isinstance(override, dict):
            mapping = (
                ("word", "word_fields", "word_field"),
                ("reading", "reading_fields", "reading_field"),
                ("definition", "definition_fields", "definition_field"),
                ("sentence", "sentence_fields", "sentence_field"),
                ("native", "native_sentence_fields", "native_sentence_field"),
            )
            for key, conf_key, singular_alt in mapping:
                legacy = {
                    "native_sentence_fields": "english_sentence_fields",
                    "native_sentence_field": "english_sentence_field",
                }
                legacy_key = legacy.get(conf_key)
                legacy_sing = legacy.get(singular_alt)
                if (
                    conf_key in override
                    or singular_alt in override
                    or (legacy_key and legacy_key in override)
                    or (legacy_sing and legacy_sing in override)
                ):
                    base[key] = _as_list(
                        override.get(
                            conf_key,
                            override.get(
                                singular_alt,
                                override.get(legacy_key, override.get(legacy_sing)),
                            ),
                        ),
                        base[key],
                    )
    return base


def _word_selectors(conf: dict[str, Any]) -> list[str]:
    return _as_list(
        conf.get("word_selectors", conf.get("word_selector")),
        DEFAULT_WORD_SELECTORS,
    )


def _plain_text(value: str | None) -> str:
    if not value:
        return ""
    text = SOUND_RE.sub("", value)
    text = STYLE_RE.sub("", text)
    text = SCRIPT_RE.sub("", text)
    text = RUBY_RE.sub("", text)
    text = BLOCK_BREAK_RE.sub("\n", text)
    text = LI_OPEN_RE.sub("• ", text)
    text = BLOCK_OPEN_RE.sub("\n", text)
    text = strip_html(text)
    text = OTHER_TAGS_RE.sub("", text)
    text = html.unescape(text)
    text = HORIZONTAL_SPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if line:
            cleaned.append(line)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned).strip()


def _plain_to_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def _show_in_popup(conf: dict[str, Any]) -> dict[str, bool]:
    raw = conf.get("show_in_popup")
    out = dict(DEFAULT_SHOW_IN_POPUP)
    if isinstance(raw, dict):
        for key in out:
            if key in raw:
                out[key] = bool(raw[key])
        if "native" not in raw and "english" in raw:
            out["native"] = bool(raw["english"])
    out["word"] = True
    return out


def _escape_search(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _note_type_name(note: Note) -> str:
    model = note.note_type()
    return model["name"] if model else ""


def _pick_field(note: Note, candidates: list[str]) -> str:
    names = set(note.keys())
    empty_hit = ""
    for name in candidates:
        if name not in names:
            continue
        raw = note[name]
        if _plain_text(raw):
            return raw
        if not empty_hit:
            empty_hit = raw
    return empty_hit


def _pick_field_plain(note: Note, candidates: list[str]) -> str:
    return _plain_text(_pick_field(note, candidates))


def _highlight_kanji(text: str, kanji: str) -> str:
    if not text:
        return ""
    if not kanji:
        return _plain_to_html(text)
    parts: list[str] = []
    for ch in text:
        if ch == "\n":
            parts.append("<br>")
            continue
        esc = html.escape(ch)
        if ch == kanji:
            parts.append(f'<span style="color:#e74c3c;font-weight:700;">{esc}</span>')
        else:
            parts.append(esc)
    return "".join(parts)


def open_note_in_browser(note_id: int) -> None:
    browser = dialogs.open("Browser", mw)
    browser.search_for(f"nid:{note_id}")


_KANJI_INFO_CACHE: dict[str, dict[str, Any] | None] = {}
_KANJIVG_CACHE: dict[str, dict[str, Any] | None] = {}
KVG_NS = "http://kanjivg.tagaini.net"
KANJI_CHAR_RE = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF]")


def fetch_kanji_info(kanji: str) -> dict[str, Any] | None:
    if kanji in _KANJI_INFO_CACHE:
        return _KANJI_INFO_CACHE[kanji]
    url = f"https://kanjiapi.dev/v1/kanji/{quote(kanji)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "KanjiNeighbours/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or "kanji" not in data:
            _KANJI_INFO_CACHE[kanji] = None
            return None
        _KANJI_INFO_CACHE[kanji] = data
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        _KANJI_INFO_CACHE[kanji] = None
        return None


def _component_meaning(char: str, original: str = "") -> str:
    lookup = original if original and KANJI_CHAR_RE.fullmatch(original) else char
    if not KANJI_CHAR_RE.fullmatch(lookup):
        return ""
    info = fetch_kanji_info(lookup)
    return _join_list((info or {}).get("meanings"))


def fetch_kanjivg_data(kanji: str) -> dict[str, Any] | None:
    """Get the dictionary radical and top-level visual components from KanjiVG."""
    if kanji in _KANJIVG_CACHE:
        return _KANJIVG_CACHE[kanji]

    url = (
        "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/"
        f"{ord(kanji):05x}.svg"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "KanjiNeighbours/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            root = ET.fromstring(resp.read())

        element_attr = f"{{{KVG_NS}}}element"
        original_attr = f"{{{KVG_NS}}}original"
        radical_attr = f"{{{KVG_NS}}}radical"
        groups = list(root.iter("{http://www.w3.org/2000/svg}g"))
        radical_group = next(
            (group for group in groups if group.get(radical_attr) == "general"), None
        )
        radical = ""
        radical_original = ""
        radical_meaning = ""
        if radical_group is not None:
            radical = radical_group.get(element_attr, "")
            radical_original = radical_group.get(original_attr, "")
            radical_meaning = _component_meaning(radical, radical_original)

        composition_root = next(
            (group for group in groups if group.get(element_attr) == kanji), None
        )
        components: list[dict[str, str]] = []
        if composition_root is not None:
            for child in list(composition_root):
                if child.tag != "{http://www.w3.org/2000/svg}g":
                    continue
                element = child.get(element_attr, "")
                if element and element != kanji:
                    original = child.get(original_attr, "")
                    components.append(
                        {
                            "element": element,
                            "original": original,
                            "meaning": _component_meaning(element, original),
                        }
                    )

        data = {
            "radical": radical,
            "radical_original": radical_original,
            "radical_meaning": radical_meaning,
            "components": components,
        }
        _KANJIVG_CACHE[kanji] = data
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, OSError):
        _KANJIVG_CACHE[kanji] = None
        return None


def _join_list(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    parts = [str(v).strip() for v in values if str(v).strip()]
    return ", ".join(parts)


def format_kanji_info_html(
    info: dict[str, Any] | None,
    kanji: str,
) -> str:
    if not info:
        return (
            '<div style="font-size:13px;opacity:0.55;">'
            "Kanji info unavailable (needs network · kanjiapi.dev)"
            "</div>"
        )

    label = "color:#e95464;font-weight:600;"
    rows: list[str] = [
        f'<div style="font-size:15px;line-height:1.45;">'
        f'<span style="{label}">Kanji:</span> '
        f'{html.escape(str((info or {}).get("kanji") or kanji))}'
    ]
    if info and info.get("grade") is not None:
        rows.append(
            f'<br><span style="{label}">Grade:</span> {html.escape(str(info["grade"]))}'
        )
    meanings = _join_list((info or {}).get("meanings"))
    if meanings:
        rows.append(f'<br><span style="{label}">Meaning:</span> {html.escape(meanings)}')
    kun = _join_list((info or {}).get("kun_readings"))
    if kun:
        rows.append(f'<br><span style="{label}">Kun\'yomi:</span> {html.escape(kun)}')
    on = _join_list((info or {}).get("on_readings"))
    if on:
        rows.append(f'<br><span style="{label}">On\'yomi:</span> {html.escape(on)}')
    rows.append("</div>")
    return "".join(rows)


def _component_display_html(component: dict[str, str]) -> str:
    element = str(component.get("element") or "")
    original = str(component.get("original") or "")
    meaning = str(component.get("meaning") or "")
    form = ""
    if original and original != element:
        form = f'<div style="font-size:11px;opacity:0.58;">{html.escape(original)} form</div>'
    meaning_html = (
        f'<div style="font-size:12px;line-height:1.25;opacity:0.82;">{html.escape(meaning)}</div>'
        if meaning
        else '<div style="font-size:12px;line-height:1.25;opacity:0.46;">meaning unavailable</div>'
    )
    return (
        '<div style="margin-top:7px;padding-top:7px;border-top:1px solid #3a3a3a;">'
        '<table cellpadding="0" cellspacing="0" width="100%">'
        "<tr>"
        '<td valign="top" style="width:38px;">'
        f'<span style="font-size:24px;font-weight:700;">{html.escape(element)}</span>'
        "</td>"
        '<td valign="top">'
        f"{meaning_html}{form}"
        "</td>"
        "</tr>"
        "</table>"
        "</div>"
    )


def format_kanji_structure_html(kanjivg_data: dict[str, Any] | None) -> str:
    label = "color:#e95464;font-weight:700;font-size:12px;text-transform:uppercase;"
    if not kanjivg_data:
        return (
            '<div style="font-size:13px;line-height:1.35;opacity:0.55;">'
            "Radicals unavailable<br>needs network · KanjiVG"
            "</div>"
        )

    rows: list[str] = ['<div style="font-size:13px;line-height:1.35;">']
    if kanjivg_data:
        radical = str(kanjivg_data.get("radical") or "")
        original = str(kanjivg_data.get("radical_original") or "")
        radical_meaning = str(kanjivg_data.get("radical_meaning") or "")
        if radical:
            rows.append(f'<div style="{label}">Radical</div>')
            rows.append(
                _component_display_html(
                    {
                        "element": radical,
                        "original": original,
                        "meaning": radical_meaning,
                    }
                )
            )
        components = kanjivg_data.get("components")
        if isinstance(components, list) and components:
            rows.append(f'<div style="{label};margin-top:11px;">Parts</div>')
            for component in components:
                if isinstance(component, dict):
                    rows.append(_component_display_html(component))
                else:
                    rows.append(
                        _component_display_html(
                            {
                                "element": str(component),
                                "original": "",
                                "meaning": _component_meaning(str(component)),
                            }
                        )
                    )
            rows.append(
                '<div style="font-size:11px;opacity:0.48;margin-top:8px;">KanjiVG structure</div>'
            )
    if len(rows) == 1:
        rows.append('<div style="opacity:0.55;">No radical details found.</div>')
    rows.append("</div>")
    return "".join(rows)


def _current_deck_name(card: Card | None) -> str | None:
    if card is None:
        return None
    return mw.col.decks.name(card.did)


def _deck_query(deck_name: str, scope: str) -> str:
    escaped = _escape_search(deck_name)
    if scope == "collection":
        return ""
    if scope == "deck_root":
        root = deck_name.split("::", 1)[0]
        return f'deck:"{_escape_search(root)}" '
    return f'deck:"{escaped}" '


def _collect_word_fields_for_search(conf: dict[str, Any]) -> list[str]:
    wanted: list[str] = []
    seen: set[str] = set()

    def add_all(names: list[str]) -> None:
        for name in names:
            if name not in seen:
                seen.add(name)
                wanted.append(name)

    add_all(_as_list(conf.get("word_fields", conf.get("word_field")), DEFAULT_WORD_FIELDS))
    note_map = conf.get("note_type_map") or {}
    if isinstance(note_map, dict):
        for override in note_map.values():
            if isinstance(override, dict):
                add_all(
                    _as_list(
                        override.get("word_fields", override.get("word_field")),
                        [],
                    )
                )

    existing: set[str] = set()
    for model in mw.col.models.all():
        for field in model["flds"]:
            existing.add(field["name"])
    return [name for name in wanted if name in existing] or wanted


def find_related_notes(kanji: str) -> tuple[str | None, list[dict[str, str]]]:
    conf = _cfg()
    min_reps = int(conf.get("min_reps", 1))
    max_results = int(conf.get("max_results", 200))
    scope = str(conf.get("deck_scope", "current"))

    card = mw.reviewer.card if mw.reviewer else None
    deck_name = _current_deck_name(card)
    if not deck_name and scope != "collection":
        return None, []

    deck_part = _deck_query(deck_name or "", scope)
    word_fields = _collect_word_fields_for_search(conf)
    if not word_fields:
        tooltip("Kanji Neighbours: choose a Word field in Add-ons → Config.")
        return deck_name, []

    field_clause = " OR ".join(
        f'"{_escape_search(name)}:*{_escape_search(kanji)}*"' for name in word_fields
    )
    query = f"{deck_part}({field_clause}) prop:reps>={min_reps}"
    card_ids = mw.col.find_cards(query)

    seen: set[int] = set()
    results: list[dict[str, str]] = []
    for cid in card_ids:
        note = mw.col.get_card(cid).note()
        if note.id in seen:
            continue
        seen.add(note.id)

        fields = _field_lists(conf, _note_type_name(note))
        word_plain = _pick_field_plain(note, fields["word"])
        if kanji not in word_plain:
            continue

        sentence = _pick_field_plain(note, fields["sentence"])
        results.append(
            {
                "note_id": str(note.id),
                "word": word_plain,
                "word_rich": _highlight_kanji(word_plain, kanji),
                "reading": _pick_field_plain(note, fields["reading"]),
                "definition": _pick_field_plain(note, fields["definition"]),
                "sentence": _highlight_kanji(sentence, kanji),
                "native": _pick_field_plain(note, fields["native"]),
            }
        )
        if len(results) >= max_results:
            break

    results.sort(key=lambda item: item["word"])
    return deck_name, results


class ResultsDialog(QDialog):
    def __init__(
        self,
        kanji: str,
        deck_name: str,
        results: list[dict[str, str]],
        kanji_info: dict[str, Any] | None = None,
        kanjivg_data: dict[str, Any] | None = None,
        parent=None,
    ):
        super().__init__(parent or mw)
        self.setWindowTitle(f"{ADDON_NAME} — {kanji}")
        self.resize(720, 640)
        self.setMinimumSize(480, 360)
        self._note_to_open = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        scope = str(_cfg().get("deck_scope", "current"))
        scope_label = {
            "collection": "all decks",
            "deck_root": f"deck tree of {deck_name.split('::', 1)[0] if deck_name else '?'}",
        }.get(scope, deck_name or "current deck")

        info_box = QFrame()
        info_box.setObjectName("kanjiInfo")
        info_box.setStyleSheet(
            """
            QFrame#kanjiInfo {
                background: #252025;
                border: 1px solid #3a3a3a;
                border-radius: 10px;
            }
            QFrame#kanjiStructure {
                background: #211d22;
                border: 1px solid #443944;
                border-radius: 8px;
            }
            """
        )
        info_layout = QHBoxLayout(info_box)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(12)

        info_label = QLabel(format_kanji_info_html(kanji_info, kanji))
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info_layout.addWidget(info_label, 1)

        structure_box = QFrame()
        structure_box.setObjectName("kanjiStructure")
        structure_box.setMinimumWidth(180)
        structure_box.setMaximumWidth(260)
        structure_layout = QVBoxLayout(structure_box)
        structure_layout.setContentsMargins(12, 10, 12, 10)
        structure_label = QLabel(format_kanji_structure_html(kanjivg_data))
        structure_label.setTextFormat(Qt.TextFormat.RichText)
        structure_label.setWordWrap(True)
        structure_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        structure_layout.addWidget(structure_label)
        info_layout.addWidget(structure_box)
        root.addWidget(info_box)

        header = QLabel(
            f'<div style="font-size:22px;">Words with '
            f'<span style="color:#e74c3c;font-weight:700;">{html.escape(kanji)}</span>'
            f' <span style="opacity:0.7;font-size:15px;">in {html.escape(scope_label)}</span>'
            f"</div>"
            f'<div style="opacity:0.65;font-size:13px;margin-top:4px;">'
            f"{len(results)} note{'s' if len(results) != 1 else ''} · reviewed at least once"
            f" · click a row to open in Browse"
            f"</div>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        container = QWidget()
        scroll.setWidget(container)
        list_layout = QVBoxLayout(container)
        list_layout.setContentsMargins(0, 0, 8, 0)
        list_layout.setSpacing(10)

        if not results:
            empty = QLabel("No reviewed words in scope use that kanji.")
            empty.setStyleSheet("font-size:16px; padding:24px; opacity:0.8;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            list_layout.addWidget(empty)
        else:
            for item in results:
                list_layout.addWidget(self._entry_widget(item))

        list_layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        qconnect(close_btn.clicked, self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self.setStyleSheet(
            """
            QDialog { background: #1e1e1e; color: #f2f2f2; }
            QScrollArea { background: transparent; }
            QLabel { color: #f2f2f2; }
            QPushButton {
                background: #333; color: #f2f2f2; border: 1px solid #555;
                border-radius: 6px; padding: 8px 18px; font-size: 14px;
            }
            QPushButton:hover { background: #444; }
            """
        )

    def _entry_widget(self, item: dict[str, str]) -> QFrame:
        show = _show_in_popup(_cfg())
        note_id = int(item["note_id"]) if item.get("note_id") else 0
        frame = _ClickableEntry(note_id, self)
        frame.setObjectName("entry")
        frame.setStyleSheet(
            """
            QFrame#entry {
                background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 10px;
            }
            QFrame#entry:hover { border: 1px solid #666; background: #303030; }
            """
        )

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        reading = item["reading"] if show.get("reading") else ""
        reading_bit = (
            f'<div style="font-size:22px;opacity:0.8;margin-top:4px;">'
            f"{_plain_to_html(reading)}</div>"
            if reading
            else ""
        )
        word = QLabel(
            f'<div style="font-size:28px;font-weight:700;line-height:1.2;">'
            f'{item["word_rich"]}</div>{reading_bit}'
        )
        word.setTextFormat(Qt.TextFormat.RichText)
        word.setWordWrap(True)
        word.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(word)

        if show.get("definition") and item["definition"]:
            definition = QLabel(
                '<div style="font-size:16px;line-height:1.45;margin-top:4px;">'
                f'{_plain_to_html(item["definition"])}</div>'
            )
            definition.setTextFormat(Qt.TextFormat.RichText)
            definition.setWordWrap(True)
            definition.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(definition)

        if show.get("sentence") and item["sentence"]:
            sentence = QLabel(
                '<div style="font-size:22px;line-height:1.5;margin-top:8px;">'
                f'{item["sentence"]}</div>'
            )
            sentence.setTextFormat(Qt.TextFormat.RichText)
            sentence.setWordWrap(True)
            sentence.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(sentence)

        if show.get("native") and item["native"]:
            native = QLabel(
                '<div style="font-size:15px;line-height:1.4;opacity:0.8;margin-top:2px;">'
                f'{_plain_to_html(item["native"])}</div>'
            )
            native.setTextFormat(Qt.TextFormat.RichText)
            native.setWordWrap(True)
            native.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(native)

        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return frame

    def open_note_after_close(self, note_id: int) -> None:
        self._note_to_open = note_id
        self.accept()


class _ClickableEntry(QFrame):
    def __init__(self, note_id: int, dialog: ResultsDialog, parent=None):
        super().__init__(parent)
        self._note_id = note_id
        self._dialog = dialog
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open in Browse")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._note_id:
            if hasattr(event, "position"):
                pos = event.position().toPoint()
            else:
                pos = event.pos()
            if self.rect().contains(pos):
                self._dialog.open_note_after_close(self._note_id)
        super().mouseReleaseEvent(event)


def show_kanji_lookup(kanji: str) -> None:
    if len(kanji) != 1:
        return
    deck_name, results = find_related_notes(kanji)
    if deck_name is None and str(_cfg().get("deck_scope", "current")) != "collection":
        tooltip("Kanji Neighbours works while reviewing a card.")
        return
    kanji_info = fetch_kanji_info(kanji)
    kanjivg_data = fetch_kanjivg_data(kanji)
    dialog = ResultsDialog(
        kanji,
        deck_name or "",
        results,
        kanji_info=kanji_info,
        kanjivg_data=kanjivg_data,
        parent=mw,
    )
    dialog.exec()
    if dialog._note_to_open:
        open_note_in_browser(dialog._note_to_open)


def on_webview_message(handled: tuple[bool, Any], message: str, context: Any):
    if not message.startswith(CMD_PREFIX):
        return handled
    from aqt.reviewer import Reviewer

    if not isinstance(context, Reviewer):
        return handled
    kanji = message[len(CMD_PREFIX) :]
    show_kanji_lookup(kanji)
    return (True, None)


def _inject_for_card(_card: Card) -> None:
    conf = _cfg()
    selectors = _word_selectors(conf)
    js = INJECT_JS.replace("%SELECTORS%", repr(selectors))
    try:
        mw.reviewer.web.eval(js)
    except Exception:
        pass


def _on_show_question(card: Card) -> None:
    _inject_for_card(card)


def _on_show_answer(card: Card) -> None:
    _inject_for_card(card)


gui_hooks.webview_did_receive_js_message.append(on_webview_message)
gui_hooks.reviewer_did_show_question.append(_on_show_question)
gui_hooks.reviewer_did_show_answer.append(_on_show_answer)


def _register_config() -> None:
    try:
        from .settings import register_config_action

        register_config_action(__name__)
    except Exception:
        # Don't prevent the add-on from loading if config UI wiring fails.
        pass


# Prefer after main window exists; also try immediately for older Anki load orders.
try:
    _register_config()
except Exception:
    pass
gui_hooks.main_window_did_init.append(lambda: _register_config())
