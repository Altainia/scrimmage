"""The "scrimmage part" task panel (GUI only; opened by the Scrimmage_Part command in InitGui.py).

Five sections (spec, "The panel and saved choices"): Bodies, Joints (with motor and encoder slots), Gyro, Mounts,
Export. The panel holds no logic of its own: it shows `overview.overview(doc)`, edits the document's saved choices
(`settings`, written back on every change), makes mounts with `pick.pick` from the 3D selection, and runs
`export.export`. Every edit is saved into the document's ScrimmagePart object at once; saving the .FCStd keeps it.

`show()` opens the panel for the active document and returns it (also kept as `current`; the GUI smoke test,
tests/freecad/gui_smoke.py, drives it through its widgets).
"""

import os
import traceback

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from . import export as exporter
from . import overview, pick, read, settings
from .errors import ExportError
from .motors import MOTOR_NAMES

current = None                  # the panel show() opened last
OUTSIDE = "scrimmage: settings changed outside the panel (undo/redo?); re-open the scrimmage panel\n"

DEFAULT_MOTOR = "KrakenX60" if "KrakenX60" in MOTOR_NAMES else MOTOR_NAMES[0]


def _box(title):
    w = QtWidgets.QWidget()
    w.setWindowTitle(title)                              # the task box's header
    lay = QtWidgets.QVBoxLayout(w)
    lay.setContentsMargins(4, 4, 4, 4)
    return w, lay


def _note(text, error=False):
    lab = QtWidgets.QLabel(text)
    lab.setWordWrap(True)
    if error:
        lab.setStyleSheet("color: #c0392b;")
    return lab


class _ListEditor(QtWidgets.QWidget):
    """A list of dicts (slots or mounts) with Add/Remove and a form editing the selected one.

    `fields`: [(key, label, kind, options)] with kind "text", "combo" (options: [(text, value)] or a callable item ->
    that list), "int" ((min, max)), "float" ((min, max, decimals)), "bool", or "info" (options: item -> text, read-only).
    `make()` returns a new dict to append, or None (it may report why through `status`). `title(item)` is the list text.
    `enabled(key, item)` grays out a field that doesn't apply. `changed()` runs after every edit (it saves)."""

    def __init__(self, items, fields, make, title, changed, add_text="Add", enabled=None):
        super().__init__()
        self.items, self.fields, self.make, self.title, self.changed = items, fields, make, title, changed
        self.enabled = enabled or (lambda key, item: True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumHeight(90)
        lay.addWidget(self.list)
        row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton(add_text)
        self.remove_button = QtWidgets.QPushButton("Remove")
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        lay.addLayout(row)
        self.status = _note("")
        self.status.hide()
        lay.addWidget(self.status)
        self.form_widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        self.editors = {}
        for key, label, kind, opts in fields:
            ed = self._editor(key, kind, opts)
            self.editors[key] = (ed, kind, opts)
            form.addRow(label, ed)
        lay.addWidget(self.form_widget)
        self.add_button.clicked.connect(self.add)
        self.remove_button.clicked.connect(self.remove)
        self.list.currentRowChanged.connect(self._load)
        self._loading = False
        self.refresh()

    def _editor(self, key, kind, opts):
        if kind == "text":
            ed = QtWidgets.QLineEdit()
            ed.editingFinished.connect(lambda k=key, e=ed: self._set(k, e.text()))
        elif kind == "combo":
            ed = QtWidgets.QComboBox()
            ed.activated.connect(lambda i, k=key, e=ed: self._set(k, e.itemData(i)))
        elif kind == "int":
            ed = QtWidgets.QSpinBox()
            ed.setKeyboardTracking(False)              # valueChanged once typing is done, not per keystroke
            ed.setRange(*opts)
            ed.valueChanged.connect(lambda v, k=key: self._set(k, int(v)))
        elif kind == "float":
            ed = QtWidgets.QDoubleSpinBox()
            ed.setKeyboardTracking(False)
            ed.setRange(opts[0], opts[1])
            ed.setDecimals(opts[2])
            ed.valueChanged.connect(lambda v, k=key: self._set(k, float(v)))
        elif kind == "bool":
            ed = QtWidgets.QCheckBox()
            ed.toggled.connect(lambda v, k=key: self._set(k, bool(v)))
        else:
            ed = QtWidgets.QLabel()
            ed.setWordWrap(True)
        return ed

    def current(self):
        i = self.list.currentRow()
        return self.items[i] if 0 <= i < len(self.items) else None

    def refresh(self, select=None):
        row = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for it in self.items:
            self.list.addItem(self.title(it))
        self.list.blockSignals(False)
        if self.items:
            self.list.setCurrentRow(min(max(row, 0), len(self.items) - 1))
        self._load()

    def say(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _load(self, *_):
        item = self.current()
        self.form_widget.setEnabled(item is not None)
        self.remove_button.setEnabled(item is not None)
        self._loading = True
        try:
            for key, (ed, kind, opts) in self.editors.items():
                v = item.get(key) if item is not None else None
                if kind == "text":
                    ed.setText("" if v is None else str(v))
                elif kind == "combo":
                    ed.clear()
                    choices = opts(item) if callable(opts) else opts
                    for text, value in choices:
                        ed.addItem(text, value)
                    if item is not None and v not in [c[1] for c in choices]:
                        ed.addItem(f"{v} (missing)", v)
                    ed.setCurrentIndex(max(ed.findData(v), 0) if item is not None else -1)
                elif kind in ("int", "float"):
                    ed.setValue(v if isinstance(v, (int, float)) and not isinstance(v, bool) else ed.minimum())
                elif kind == "bool":
                    ed.setChecked(bool(v))
                else:
                    ed.setText(opts(item) if item is not None else "")
                ed.setEnabled(item is not None and self.enabled(key, item))
        finally:
            self._loading = False

    def _set(self, key, value):
        item = self.current()
        if self._loading or item is None or item.get(key) == value:
            return
        item[key] = value
        self.list.item(self.list.currentRow()).setText(self.title(item))
        for k, (ed, _, _) in self.editors.items():
            ed.setEnabled(self.enabled(k, item))
        self.changed()

    def add(self):
        self.say("")
        item = self.make(self)
        if item is None:
            return None
        self.items.append(item)
        self.refresh(select=len(self.items) - 1)
        self.changed()
        return item

    def remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.items):
            del self.items[i]
            self.refresh(select=i)
            self.changed()


class _DocWatcher:
    """Closes the panel when its document is closed, or when an undo/redo changed its settings (a FreeCAD document
    observer)."""

    def __init__(self, panel):
        self.panel, self.name = panel, panel.doc.Name

    def slotDeletedDocument(self, doc):
        if doc.Name == self.name:
            QtCore.QTimer.singleShot(0, self.panel.document_closed)

    def slotUndoDocument(self, doc):
        if doc.Name == self.name:
            QtCore.QTimer.singleShot(0, self.panel.check_outside_change)

    slotRedoDocument = slotUndoDocument


class ScrimmagePanel:
    def __init__(self, doc):
        self.doc = doc
        self.asm = read.find_assembly(doc)
        self.data = settings.data(doc)            # the stored JSON the panel last read or wrote
        self.st = settings.get(doc) or settings.ScrimmageSettings()
        self.ov = overview.overview(doc)
        self.labels = {c["name"]: c["label"] for c in self.ov["components"]}
        self.computed = {c["name"]: c["mass_kg"] for c in self.ov["components"]}
        self.joints = {j["name"]: j for j in self.ov["joints"]}
        self.last_summary = None
        self.override_editors, self.friction_editors = {}, {}
        self.form = [self._bodies(), self._joints(), self._gyro(), self._mounts(), self._export()]
        self.watcher = _DocWatcher(self)
        FreeCAD.addDocumentObserver(self.watcher)

    # ------------------------------------------------------------ task dialog protocol
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Close

    def reject(self):
        global current
        if self.watcher is not None:
            FreeCAD.removeDocumentObserver(self.watcher)
            self.watcher = None
        if current is self:
            current = None
        FreeCADGui.Control.closeDialog()
        FreeCADGui.Selection.clearSelection()
        return True

    accept = reject

    def doc_open(self):
        """Whether the panel's document is still open (it may have been closed under the panel)."""
        return any(d is self.doc for d in FreeCAD.listDocuments().values())

    def save(self):
        """Write the settings into the document as one undoable step; returns False (and closes the panel) when the
        document has been closed."""
        if not self.doc_open():
            FreeCAD.Console.PrintWarning("scrimmage: the document was closed; the change wasn't saved\n")
            self.document_closed()
            return False
        written = settings.save_guarded(self.doc, self.st, self.data)
        if written is None:
            self.outside_change()
            return False
        self.data = written
        return True

    def outside_change(self):
        """The stored settings changed under the panel (undo/redo, a script): don't overwrite them; close."""
        FreeCAD.Console.PrintWarning(OUTSIDE)
        self.document_closed()

    def check_outside_change(self):
        if self.doc_open() and settings.data(self.doc) != self.data:
            self.outside_change()

    def document_closed(self):
        if current is self and FreeCADGui.Control.activeDialog():
            self.reject()

    # ------------------------------------------------------------ Bodies
    def _bodies(self):
        w, lay = _box("Bodies")
        for p in self.ov["problems"]:
            lay.addWidget(_note(p, error=True))
        t = self.body_tree = QtWidgets.QTreeWidget()
        t.setColumnCount(5)
        t.setHeaderLabels(["Body / part", "kg", "Override kg", "Contact", "Friction"])
        t.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        groups = [(b["name"], [c["name"] for c in b["comps"]]) for b in self.ov["bodies"]]
        if not groups:
            groups = [("(no bodies yet)", [c["name"] for c in self.ov["components"]])]
        self.body_items = []
        for name, comps in groups:
            top = QtWidgets.QTreeWidgetItem([name, ""])
            top.setData(0, QtCore.Qt.ItemDataRole.UserRole, comps)
            t.addTopLevelItem(top)
            self.body_items.append((top, comps))
            for c in comps:
                child = QtWidgets.QTreeWidgetItem([self.labels[c], f"{self.computed[c]:.3f}"])
                child.setData(0, QtCore.Qt.ItemDataRole.UserRole, [c])
                top.addChild(child)
                t.setItemWidget(child, 2, self._override_editor(c))
                t.setItemWidget(child, 3, self._contact_editor(c))
                t.setItemWidget(child, 4, self._friction_editor(c))
            top.setExpanded(True)
        self._update_body_masses()
        for col in range(5):
            t.resizeColumnToContents(col)
        t.itemSelectionChanged.connect(self._select_in_3d)
        lay.addWidget(t)
        lay.addWidget(_note("Mass is from the material's density (aluminum when none). Mark wheels (a collision "
                            "cylinder about their axle) and parts that should collide."))
        return w

    def _override_editor(self, comp):
        ed = QtWidgets.QDoubleSpinBox()
        ed.setKeyboardTracking(False)
        ed.setRange(0.0, 10000.0)
        ed.setDecimals(3)
        ed.setMaximumWidth(90)
        ed.setSpecialValueText("—")
        ed.setValue(float(self.st.mass_overrides.get(comp, 0.0) or 0.0))
        ed.setToolTip("Mass override in kg (— for the computed mass)")
        ed.valueChanged.connect(lambda v, c=comp: self.set_mass_override(c, v))
        self.override_editors[comp] = ed
        return ed

    def _contact_editor(self, comp):
        ed = QtWidgets.QComboBox()
        for text, value in (("—", None), ("wheel", "wheel"), ("collide", "collide")):
            ed.addItem(text, value)
        ed.setCurrentIndex(max(ed.findData(self.st.contact.get(comp)), 0))
        ed.activated.connect(lambda i, c=comp, e=ed: self.set_contact(c, e.itemData(i)))
        return ed

    def set_contact(self, comp, kind):
        self._set_or_clear(self.st.contact, comp, kind)
        self.friction_editors[comp].setEnabled(kind == "wheel")

    def _friction_editor(self, comp):
        ed = QtWidgets.QDoubleSpinBox()
        ed.setKeyboardTracking(False)
        ed.setRange(0.0, 10.0)
        ed.setDecimals(2)
        ed.setMaximumWidth(80)
        ed.setSingleStep(0.1)
        ed.setSpecialValueText("default")
        ed.setValue(float(self.st.wheel_friction.get(comp, 0.0) or 0.0))
        ed.setToolTip(f"Wheel friction (default {exporter.WHEEL_FRICTION:g}); used only for wheels")
        ed.valueChanged.connect(lambda v, c=comp: self._set_or_clear(self.st.wheel_friction, c, v or None))
        ed.setEnabled(self.st.contact.get(comp) == "wheel")
        self.friction_editors[comp] = ed
        return ed

    def _set_or_clear(self, table, key, value):
        if value is None:
            table.pop(key, None)
        else:
            table[key] = value
        self.save()

    def set_mass_override(self, comp, kg):
        self._set_or_clear(self.st.mass_overrides, comp, float(kg) if kg and kg > 0 else None)
        self._update_body_masses()

    def _update_body_masses(self):
        for top, comps in self.body_items:
            kg = sum(self.st.mass_overrides.get(c) or self.computed[c] for c in comps)
            top.setText(1, f"{kg:.3f}")

    def _select_in_3d(self):
        comps = []
        for it in self.body_tree.selectedItems():
            comps += [c for c in it.data(0, QtCore.Qt.ItemDataRole.UserRole) or [] if c not in comps]
        FreeCADGui.Selection.clearSelection()
        for c in comps:
            root, sub = pick.selection_path(self.asm, c)
            FreeCADGui.Selection.addSelection(self.doc.Name, root, sub)

    # ------------------------------------------------------------ Joints (motor and encoder slots)
    def _joint_label(self, name):
        j = self.joints.get(name)
        return j["label"] if j else f"{name} (missing)"

    def _slot_names(self):
        return {s.get("slot") for s in self.st.motors + self.st.encoders + self.st.gyros}

    def _joint_choices(self, item=None):
        return [(f"{j['label']} ({j['type']})", j["name"]) for j in self.ov["tree_joints"]]

    def _joints(self):
        w, lay = _box("Joints")
        tbl = QtWidgets.QTableWidget(len(self.ov["joints"]), 4)
        tbl.setHorizontalHeaderLabels(["Joint", "Type", "Role", "Limits"])
        tbl.verticalHeader().hide()
        tbl.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for r, j in enumerate(self.ov["joints"]):
            lim = j["limits"]
            unit = "°" if j["type"] == "Revolute" else " mm"
            lim_text = "" if lim is None else " … ".join("—" if v is None else f"{v:g}{unit}" for v in lim)
            for col, text in enumerate((j["label"], j["type"], j["role"], lim_text)):
                tbl.setItem(r, col, QtWidgets.QTableWidgetItem(text))
        tbl.resizeColumnsToContents()
        tbl.setMaximumHeight(min(40 + 26 * len(self.ov["joints"]), 180))
        lay.addWidget(tbl)

        lay.addWidget(QtWidgets.QLabel("<b>Motor slots</b>"))
        motor_fields = [
            ("slot", "Slot", "text", None),
            ("joint", "Joint", "combo", self._joint_choices),
            ("motor", "Motor", "combo", [(m, m) for m in MOTOR_NAMES]),
            ("count", "Motors", "int", (1, 8)),
            ("gear_ratio", "Gear ratio", "float", (0.001, 10000.0, 3)),
            ("radius_mm", "Spool/pulley radius mm", "float", (0.1, 1000.0, 2)),
            ("invert", "Inverted", "bool", None),
            ("efficiency", "Efficiency", "float", (0.01, 1.0, 2)),
        ]
        self.motor_editor = _ListEditor(
            self.st.motors, motor_fields, self._new_motor,
            lambda s: f"{s.get('slot')} — {s.get('motor')} on {self._joint_label(s.get('joint'))}", self.save,
            add_text="Add motor", enabled=self._motor_field_enabled)
        lay.addWidget(self.motor_editor)

        lay.addWidget(QtWidgets.QLabel("<b>Encoder slots</b>"))
        enc_fields = [
            ("slot", "Slot", "text", None),
            ("joint", "Joint", "combo", self._joint_choices),
            ("on", "Measures", "combo", [("the joint", "joint"), ("the motor shaft", "motor")]),
            ("invert", "Inverted", "bool", None),
        ]
        self.encoder_editor = _ListEditor(
            self.st.encoders, enc_fields, self._new_encoder,
            lambda s: f"{s.get('slot')} — {self._joint_label(s.get('joint'))} ({s.get('on')})", self.save,
            add_text="Add encoder")
        lay.addWidget(self.encoder_editor)
        if not self.ov["tree_joints"]:
            for ed in (self.motor_editor, self.encoder_editor):
                ed.add_button.setEnabled(False)
            lay.addWidget(_note("No Revolute or Slider joints to put slots on."))
        return w

    def _motor_field_enabled(self, key, item):
        if key == "radius_mm":
            return self.joints.get(item.get("joint"), {}).get("type") == "Slider"
        return True

    def _new_motor(self, editor):
        return dict(slot=overview.unique_name("motor", self._slot_names()), joint=self.ov["tree_joints"][0]["name"],
                    motor=DEFAULT_MOTOR, count=1, gear_ratio=1.0, radius_mm=20.0, invert=False, efficiency=1.0)

    def _new_encoder(self, editor):
        return dict(slot=overview.unique_name("encoder", self._slot_names()), joint=self.ov["tree_joints"][0]["name"],
                    on="joint", invert=False)

    def add_motor(self):
        return self.motor_editor.add()

    # ------------------------------------------------------------ Gyro
    def _gyro(self):
        w, lay = _box("Gyro")
        fields = [("slot", "Slot", "text", None),
                  ("body_comp", "On part", "combo", [(c["label"], c["name"]) for c in self.ov["components"]])]
        self.gyro_editor = _ListEditor(
            self.st.gyros, fields, self._new_gyro,
            lambda s: f"{s.get('slot')} — on {self.labels.get(s.get('body_comp'), s.get('body_comp'))}", self.save,
            add_text="Add gyro")
        lay.addWidget(self.gyro_editor)
        lay.addWidget(_note("A gyro reads the heading of the body its part belongs to."))
        return w

    def _new_gyro(self, editor):
        comps = self.ov["components"]
        if not comps:
            return None
        return dict(slot=overview.unique_name("gyro", self._slot_names()), body_comp=comps[0]["name"])

    # ------------------------------------------------------------ Mounts
    def _mounts(self):
        w, lay = _box("Mounts")
        lcs = self.ov["lcs_mounts"]
        if lcs:
            lay.addWidget(_note("From coordinate systems: " + ", ".join(
                f"{m['name']}" + (f" (on {self.labels.get(m['comp'], m['comp'])})" if m["comp"] else "")
                for m in lcs)))
        fields = [("name", "Name", "text", None),
                  ("comp", "On part", "info", lambda m: self.labels.get(m.get("comp"), str(m.get("comp")))),
                  ("frame_local", "At (part frame)", "info", _frame_text)]
        self.mount_editor = _ListEditor(
            self.st.mounts, fields, self._new_mount,
            lambda m: f"{m.get('name')} — on {self.labels.get(m.get('comp'), m.get('comp'))}", self.save,
            add_text="Add from selection")
        lay.addWidget(self.mount_editor)
        lay.addWidget(_note("Select a flat face (outward normal), a round edge or a cylindrical face (center and "
                            "axis) in the 3D view, then Add from selection. A coordinate system labeled mount_<name> "
                            "is a mount too."))
        return w

    def _new_mount(self, editor):
        sel = [(s.Object, sub) for s in FreeCADGui.Selection.getSelectionEx("", 0) for sub in s.SubElementNames]
        if not sel:
            editor.say("Select a face or a round edge in the 3D view first.")
            return None
        used = {m.get("name") for m in self.st.mounts} | {m["name"] for m in self.ov["lcs_mounts"]}
        root, sub = sel[0]
        try:
            name, comp, frame = pick.pick(self.asm, root, sub, used)
        except pick.PickError as e:
            editor.say(str(e))
            return None
        if len(sel) > 1:
            editor.say(f"{len(sel)} elements were selected; used the first ({sub}).")
        return {"name": name, "comp": comp, "frame_local": [list(frame[0]), list(frame[1])]}

    def add_mount_from_selection(self):
        return self.mount_editor.add()

    # ------------------------------------------------------------ Export
    def _export(self):
        w, lay = _box("Export")
        row = QtWidgets.QHBoxLayout()
        self.folder = QtWidgets.QLineEdit(self.st.export_dir
                                          or overview.default_export_dir(self.doc.FileName, self.asm.Label))
        self.folder.setPlaceholderText("part folder to write")
        self.folder.editingFinished.connect(self._folder_changed)
        browse = QtWidgets.QPushButton("…")
        browse.setToolTip("Choose the part folder")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder)
        row.addWidget(browse)
        lay.addLayout(row)
        self.export_button = QtWidgets.QPushButton("Export")
        self.export_button.clicked.connect(self.export)
        lay.addWidget(self.export_button)
        self.result = QtWidgets.QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(120)
        self.result.setPlaceholderText("The export's summary, or what to fix, appears here.")
        lay.addWidget(self.result)
        return w

    def set_export_dir(self, path):
        self.folder.setText(path)
        self._folder_changed()

    def _folder_changed(self):
        path = self.folder.text().strip()
        if path != self.st.export_dir:
            self.st.export_dir = path
            self.save()

    def _browse(self):
        start = self.folder.text().strip() or os.path.dirname(self.doc.FileName) or os.path.expanduser("~")
        path = QtWidgets.QFileDialog.getExistingDirectory(None, "Part folder", start)
        if path:
            self.set_export_dir(path)

    def export(self):
        """Export to the chosen folder; shows the summary or the problems and returns the summary (None on failure)."""
        if not self.doc_open():
            self.document_closed()
            return None
        self._folder_changed()
        out = os.path.expanduser(self.st.export_dir)
        self.last_summary = None
        if not out:
            self.result.setPlainText("Choose the part folder to export to.")
            return None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            self.last_summary = exporter.export(self.doc, out)
            self.result.setPlainText(overview.summary_text(self.last_summary))
        except ExportError as e:
            self.result.setPlainText(overview.problems_text(e.problems))
        except Exception:
            self.result.setPlainText("The export failed unexpectedly:\n" + traceback.format_exc())
            FreeCAD.Console.PrintError(traceback.format_exc())
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        return self.last_summary


def _frame_text(m):
    try:
        (x, y, z), (qw, qx, qy, qz) = m["frame_local"]
        n = (2 * (qx * qz + qw * qy), 2 * (qy * qz - qw * qx), 1 - 2 * (qx * qx + qy * qy))
        r = lambda v: f"{0.0 if abs(v) < 5e-4 else v:.3g}"   # noqa: E731
        return f"({r(x)}, {r(y)}, {r(z)}) mm, z axis ({r(n[0])}, {r(n[1])}, {r(n[2])})"
    except (KeyError, TypeError, ValueError):
        return "damaged; pick it again"


def show():
    """Open the panel for the active document's Assembly; returns the ScrimmagePanel (None when it can't open)."""
    from . import version_problem
    old = version_problem(FreeCAD.Version())
    if old:
        QtWidgets.QMessageBox.warning(None, "scrimmage", old)
        return None
    doc = FreeCAD.ActiveDocument
    if doc is None:
        QtWidgets.QMessageBox.information(None, "scrimmage", "Open a document with an Assembly first.")
        return None
    if FreeCADGui.Control.activeDialog():
        QtWidgets.QMessageBox.information(None, "scrimmage", "Another task is open in the Tasks panel; close it "
                                                              "first (OK or Cancel there).")
        return None
    try:
        p = ScrimmagePanel(doc)
    except ExportError as e:
        QtWidgets.QMessageBox.warning(None, "scrimmage", "\n".join(e.problems))
        return None
    FreeCADGui.Control.showDialog(p)
    global current
    current = p
    return p
