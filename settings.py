# -*- coding: utf-8 -*-
"""GUI settings for Kanji Neighbours (Tools → Add-ons → Config)."""

from __future__ import annotations

from typing import Any

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    Qt,
    qconnect,
)
from aqt.utils import showInfo, tooltip

NONE = "(none)"

DECK_SCOPES = [
    ("current", "Current card's deck"),
    ("deck_root", "Top-level deck + subdecks"),
    ("collection", "Whole collection"),
]


def all_field_names() -> list[str]:
    names: set[str] = set()
    if not mw or not mw.col:
        return []
    for model in mw.col.models.all():
        for field in model["flds"]:
            names.add(field["name"])
    return sorted(names, key=str.lower)


def note_type_names() -> list[str]:
    if not mw or not mw.col:
        return []
    return sorted((m["name"] for m in mw.col.models.all()), key=str.lower)


def fields_for_note_type(name: str) -> list[str]:
    if not mw or not mw.col:
        return []
    model = mw.col.models.by_name(name)
    if not model:
        return []
    return [f["name"] for f in model["flds"]]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _first(value: Any) -> str:
    items = _as_list(value)
    return items[0] if items else ""


class _FieldCombo(QComboBox):
    def __init__(self, fields: list[str], allow_none: bool = True, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if allow_none:
            self.addItem(NONE)
        for name in fields:
            self.addItem(name)

    def set_field(self, name: str) -> None:
        if not name:
            if self.count() and self.itemText(0) == NONE:
                self.setCurrentIndex(0)
            else:
                self.setEditText("")
            return
        idx = self.findText(name)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(name)

    def field(self) -> str:
        text = self.currentText().strip()
        if not text or text == NONE:
            return ""
        return text


class SettingsDialog(QDialog):
    def __init__(self, conf: dict[str, Any], parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Kanji Neighbours settings")
        self.resize(560, 520)
        self._conf = dict(conf or {})
        note_map = self._conf.get("note_type_map") or {}
        self._conf["note_type_map"] = dict(note_map) if isinstance(note_map, dict) else {}
        self._all_fields = all_field_names()
        self._loaded_note_type = NONE
        self.result_conf: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_fields_tab(), "Fields")
        tabs.addTab(self._build_overrides_tab(), "Note types")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        qconnect(buttons.accepted, self.accept)
        qconnect(buttons.rejected, self.reject)
        root.addWidget(buttons)

        self._load(self._conf)

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.deck_scope = QComboBox()
        for key, label in DECK_SCOPES:
            self.deck_scope.addItem(label, key)
        form.addRow("Search scope", self.deck_scope)

        self.min_reps = QSpinBox()
        self.min_reps.setRange(0, 9999)
        form.addRow("Minimum reviews", self.min_reps)

        self.max_results = QSpinBox()
        self.max_results.setRange(1, 5000)
        form.addRow("Max results", self.max_results)

        self.selectors = QLineEdit()
        self.selectors.setPlaceholderText("#word, .kanji-neighbours-word")
        form.addRow("Click selectors", self.selectors)

        show_box = QGroupBox("Show in popup")
        show_layout = QVBoxLayout(show_box)
        self.show_word = QCheckBox("Word")
        self.show_word.setChecked(True)
        self.show_word.setEnabled(False)
        self.show_reading = QCheckBox("Reading")
        self.show_definition = QCheckBox("Definition")
        self.show_sentence = QCheckBox("Japanese sentence")
        self.show_english = QCheckBox("Native sentence")
        for box in (
            self.show_word,
            self.show_reading,
            self.show_definition,
            self.show_sentence,
            self.show_english,
        ):
            show_layout.addWidget(box)
        form.addRow(show_box)

        hint = QLabel(
            'Tip: wrap the word as '
            '<span style="font-family:monospace;">&lt;span class="kanji-neighbours-word"&gt;...&lt;/span&gt;</span>'
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(hint)
        return tab

    def _build_fields_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        box = QGroupBox("Default fields")
        form = QFormLayout(box)

        self.word_field = _FieldCombo(self._all_fields, allow_none=False)
        self.reading_field = _FieldCombo(self._all_fields)
        self.definition_field = _FieldCombo(self._all_fields)
        self.sentence_field = _FieldCombo(self._all_fields)
        self.english_field = _FieldCombo(self._all_fields)

        form.addRow("Word (required)", self.word_field)
        form.addRow("Reading", self.reading_field)
        form.addRow("Definition", self.definition_field)
        form.addRow("Japanese sentence", self.sentence_field)
        form.addRow("Native sentence", self.english_field)
        layout.addWidget(box)

        extra_box = QGroupBox("Also search these word fields")
        extra_layout = QVBoxLayout(extra_box)
        self.extra_word_list = QListWidget()
        self.extra_word_list.setMinimumHeight(140)
        for name in self._all_fields:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.extra_word_list.addItem(item)
        extra_layout.addWidget(self.extra_word_list)
        layout.addWidget(extra_box)
        layout.addStretch(1)
        return tab

    def _build_overrides_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        intro = QLabel(
            "If a note type uses different field names, select it and set fields here."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.note_type = QComboBox()
        self.note_type.addItem(NONE)
        for name in note_type_names():
            self.note_type.addItem(name)
        row.addWidget(QLabel("Note type"))
        row.addWidget(self.note_type, 1)
        layout.addLayout(row)

        self.override_enabled = QCheckBox("Use custom fields for this note type")
        layout.addWidget(self.override_enabled)

        form = QFormLayout()
        self.o_word = _FieldCombo([], allow_none=False)
        self.o_reading = _FieldCombo([])
        self.o_definition = _FieldCombo([])
        self.o_sentence = _FieldCombo([])
        self.o_english = _FieldCombo([])
        form.addRow("Word", self.o_word)
        form.addRow("Reading", self.o_reading)
        form.addRow("Definition", self.o_definition)
        form.addRow("Japanese sentence", self.o_sentence)
        form.addRow("Native sentence", self.o_english)
        layout.addLayout(form)

        self._override_status = QLabel("")
        layout.addWidget(self._override_status)
        layout.addStretch(1)

        qconnect(self.note_type.currentTextChanged, self._on_note_type_changed)
        qconnect(self.override_enabled.toggled, self._sync_override_enabled)
        return tab

    def _repopulate_override_combos(self, fields: list[str]) -> None:
        for combo, allow_none in (
            (self.o_word, False),
            (self.o_reading, True),
            (self.o_definition, True),
            (self.o_sentence, True),
            (self.o_english, True),
        ):
            combo.blockSignals(True)
            combo.clear()
            if allow_none:
                combo.addItem(NONE)
            for name in fields:
                combo.addItem(name)
            combo.blockSignals(False)
        if fields:
            self.o_word.set_field(fields[0])
        else:
            self.o_word.set_field("")
        self.o_reading.set_field("")
        self.o_definition.set_field("")
        self.o_sentence.set_field("")
        self.o_english.set_field("")

    def _on_note_type_changed(self, name: str) -> None:
        if self._loaded_note_type and self._loaded_note_type != NONE:
            self._commit_override_for(self._loaded_note_type)

        self._loaded_note_type = name

        if not name or name == NONE:
            self.override_enabled.setChecked(False)
            self.override_enabled.setEnabled(False)
            self._repopulate_override_combos([])
            self._override_status.setText("")
            self._sync_override_enabled()
            return

        self.override_enabled.setEnabled(True)
        fields = fields_for_note_type(name)
        self._repopulate_override_combos(fields)

        note_map = self._conf.get("note_type_map") or {}
        override = note_map.get(name) if isinstance(note_map, dict) else None
        if isinstance(override, dict) and override:
            self.override_enabled.setChecked(True)
            self.o_word.set_field(_first(override.get("word_fields", override.get("word_field"))))
            self.o_reading.set_field(
                _first(override.get("reading_fields", override.get("reading_field")))
            )
            self.o_definition.set_field(
                _first(override.get("definition_fields", override.get("definition_field")))
            )
            self.o_sentence.set_field(
                _first(override.get("sentence_fields", override.get("sentence_field")))
            )
            self.o_english.set_field(
                _first(
                    override.get(
                        "native_sentence_fields",
                        override.get(
                            "english_sentence_fields",
                            override.get("english_sentence_field"),
                        ),
                    )
                )
            )
            self._override_status.setText("This note type has a saved override.")
        else:
            self.override_enabled.setChecked(False)
            self._override_status.setText("No override saved for this note type.")
        self._sync_override_enabled()

    def _sync_override_enabled(self) -> None:
        on = self.override_enabled.isChecked() and self.override_enabled.isEnabled()
        for w in (
            self.o_word,
            self.o_reading,
            self.o_definition,
            self.o_sentence,
            self.o_english,
        ):
            w.setEnabled(on)

    def _load(self, conf: dict[str, Any]) -> None:
        scope = str(conf.get("deck_scope", "current"))
        idx = self.deck_scope.findData(scope)
        self.deck_scope.setCurrentIndex(idx if idx >= 0 else 0)

        self.min_reps.setValue(int(conf.get("min_reps", 1)))
        self.max_results.setValue(int(conf.get("max_results", 200)))

        selectors = _as_list(conf.get("word_selectors", conf.get("word_selector")))
        self.selectors.setText(
            ", ".join(selectors)
            if selectors
            else "#word, .kanji-neighbours-word, .kanji-lookup-word"
        )

        show = conf.get("show_in_popup") if isinstance(conf.get("show_in_popup"), dict) else {}
        self.show_reading.setChecked(bool(show.get("reading", True)))
        self.show_definition.setChecked(bool(show.get("definition", True)))
        self.show_sentence.setChecked(bool(show.get("sentence", True)))
        self.show_english.setChecked(bool(show.get("native", show.get("english", True))))

        word_fields = _as_list(conf.get("word_fields", conf.get("word_field")))
        self.word_field.set_field(word_fields[0] if word_fields else "")
        extras = set(word_fields[1:])
        for i in range(self.extra_word_list.count()):
            item = self.extra_word_list.item(i)
            item.setCheckState(
                Qt.CheckState.Checked if item.text() in extras else Qt.CheckState.Unchecked
            )

        self.reading_field.set_field(
            _first(conf.get("reading_fields", conf.get("reading_field")))
        )
        self.definition_field.set_field(
            _first(conf.get("definition_fields", conf.get("definition_field")))
        )
        self.sentence_field.set_field(
            _first(conf.get("sentence_fields", conf.get("sentence_field")))
        )
        self.english_field.set_field(
            _first(
                conf.get(
                    "native_sentence_fields",
                    conf.get("english_sentence_fields", conf.get("english_sentence_field")),
                )
            )
        )

        self._on_note_type_changed(self.note_type.currentText())

    def _commit_override_for(self, name: str) -> None:
        if not name or name == NONE:
            return
        note_map = self._conf.setdefault("note_type_map", {})
        if not isinstance(note_map, dict):
            note_map = {}
            self._conf["note_type_map"] = note_map

        if not self.override_enabled.isChecked():
            note_map.pop(name, None)
            return

        word = self.o_word.field()
        if not word:
            note_map.pop(name, None)
            return

        entry: dict[str, list[str]] = {"word_fields": [word]}
        if self.o_reading.field():
            entry["reading_fields"] = [self.o_reading.field()]
        if self.o_definition.field():
            entry["definition_fields"] = [self.o_definition.field()]
        if self.o_sentence.field():
            entry["sentence_fields"] = [self.o_sentence.field()]
        if self.o_english.field():
            entry["native_sentence_fields"] = [self.o_english.field()]
        note_map[name] = entry

    def _build_conf(self) -> dict[str, Any] | None:
        self._commit_override_for(self.note_type.currentText().strip())

        word = self.word_field.field()
        if not word:
            showInfo("Please choose a Word field on the Fields tab.")
            return None

        word_fields = [word]
        for i in range(self.extra_word_list.count()):
            item = self.extra_word_list.item(i)
            if item.checkState() == Qt.CheckState.Checked and item.text() != word:
                word_fields.append(item.text())

        selectors = [s.strip() for s in self.selectors.text().split(",") if s.strip()]
        if not selectors:
            showInfo("Please set at least one click selector (e.g. #word).")
            return None

        def one(combo: _FieldCombo) -> list[str]:
            value = combo.field()
            return [value] if value else []

        return {
            "word_fields": word_fields,
            "reading_fields": one(self.reading_field),
            "definition_fields": one(self.definition_field),
            "sentence_fields": one(self.sentence_field),
            "native_sentence_fields": one(self.english_field),
            "word_selectors": selectors,
            "show_in_popup": {
                "word": True,
                "reading": self.show_reading.isChecked(),
                "definition": self.show_definition.isChecked(),
                "sentence": self.show_sentence.isChecked(),
                "native": self.show_english.isChecked(),
            },
            "note_type_map": self._conf.get("note_type_map") or {},
            "min_reps": self.min_reps.value(),
            "deck_scope": self.deck_scope.currentData(),
            "max_results": self.max_results.value(),
        }

    def accept(self) -> None:
        conf = self._build_conf()
        if conf is None:
            return
        self.result_conf = conf
        super().accept()


def register_config_action(addon_name: str) -> None:
    def _open() -> None:
        if not mw or not mw.col:
            tooltip("Open a profile first.")
            return
        conf = mw.addonManager.getConfig(addon_name) or {}
        dlg = SettingsDialog(conf, parent=mw)
        if dlg.exec() and dlg.result_conf is not None:
            mw.addonManager.writeConfig(addon_name, dlg.result_conf)
            tooltip("Kanji Neighbours settings saved.")

    mw.addonManager.setConfigAction(addon_name, _open)
