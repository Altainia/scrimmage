"""GUI smoke run of the scrimmage task panel. Runs INSIDE the FreeCAD GUI: `freecad gui_smoke.py`.

Environment: SCRIMMAGE_GUI_WORK (a folder FreeCAD can write; the snap needs one under ~/snap/freecad/common),
SCRIMMAGE_ADDON (freecad/Scrimmage), SCRIMMAGE_FIXTURES (tests/freecad/make_fixtures.py).

It builds the `arm` fixture into WORK, opens it, activates the workbench, runs the Scrimmage_Part command and drives
the panel through its widgets: adds a motor slot (renamed `pivot`, gear ratio 50), picks a mount from a selection
made in code (the arm's top face), sets a 2.5 kg mass override on the arm, sets the export folder and clicks Export.
It saves a screenshot of the main window to WORK/panel.png and the five sections to WORK/panel_sections.png, re-opens the saved document to check the choices were
kept, closes the document under the open panel (the panel must close), writes WORK/result.json ({"ok", "summary", "png", "errors", ...}) and quits FreeCAD.
tests/test_freecad_panel.py::test_gui_smoke runs it (SCRIMMAGE_FREECAD_GUI=1)."""

import json
import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

WORK = os.environ["SCRIMMAGE_GUI_WORK"]
ADDON = os.environ["SCRIMMAGE_ADDON"]
FIXTURES = os.environ["SCRIMMAGE_FIXTURES"]
sys.path.insert(0, ADDON)

result = {"ok": False, "errors": []}


def pump(ms=300):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def check(cond, what):
    if not cond:
        result["errors"].append(what)


def save_sections(p, path):
    """The panel's five sections side by side in one image (the task view shows only part of it at once)."""
    from PySide import QtGui
    shots = [w.grab() for w in p.form]
    img = QtGui.QPixmap(sum(s.width() for s in shots) + 8 * len(shots), max(s.height() for s in shots))
    img.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(img)
    x = 0
    for s in shots:
        painter.drawPixmap(x, 0, s)
        x += s.width() + 8
    painter.end()
    img.save(path)
    return path


def run():
    ns = {"__name__": "make_fixtures"}
    exec(compile(open(FIXTURES, encoding="utf-8").read(), FIXTURES, "exec"), ns)
    path = os.path.join(WORK, "arm.FCStd")
    ns["build"]("arm", path)
    doc = App.openDocument(path)
    Gui.getMainWindow().resize(1600, 1000)

    if "ScrimmageWorkbench" not in Gui.listWorkbenches():      # not installed in the Mod folder: register it here
        init = os.path.join(ADDON, "InitGui.py")
        exec(compile(open(init, encoding="utf-8").read(), init, "exec"), {"__name__": "InitGui"})
    Gui.activateWorkbench("ScrimmageWorkbench")
    pump()
    Gui.runCommand("Scrimmage_Part")
    pump()
    import scrimmage_part
    from scrimmage_part import panel, settings
    addon = os.path.realpath(ADDON)
    for mod in (scrimmage_part, panel):
        if not os.path.realpath(mod.__file__).startswith(addon + os.sep):
            raise RuntimeError(f"an installed Scrimmage shadows the working tree ({mod.__name__} loaded from "
                               f"{mod.__file__}, not under {ADDON}); uninstall it (remove Scrimmage from FreeCAD's "
                               "Mod folder) or reinstall it from this checkout with `scrimmage freecad install`")
    p = panel.current
    check(p is not None and Gui.Control.activeDialog(), "the panel didn't open")
    result["sections"] = [w.windowTitle() for w in p.form]

    # motor slot: Add motor, then rename it and set its gearing through the form's widgets
    p.motor_editor.add_button.click()
    slot = p.motor_editor.editors["slot"][0]
    slot.setText("pivot")
    slot.editingFinished.emit()
    p.motor_editor.editors["gear_ratio"][0].setValue(50.0)
    check(p.motor_editor.list.count() == 1, "no motor slot in the list")

    # mount: select the arm's top face as the GUI would, then Add from selection
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Assembly", "Arm.Face6")
    pump()
    p.mount_editor.add_button.click()
    check(not p.mount_editor.status.text(), f"mount pick said: {p.mount_editor.status.text()}")
    result["mounts"] = [dict(m) for m in p.st.mounts]

    # Bodies: select the Arm body row (selects its parts in 3D), then override its mass
    top = [it for it, comps in p.body_items if comps == ["Arm"]][0]
    p.body_tree.setCurrentItem(top)
    pump()
    result["selected_in_3d"] = [(s.ObjectName, list(s.SubElementNames)) for s in Gui.Selection.getSelectionEx("", 0)]
    p.override_editors["Arm"].setValue(2.5)
    # each change is one undo step in the document
    result["undo_names"] = list(doc.UndoNames)[:1]
    doc.undo()
    check(settings.get(doc).mass_overrides == {}, "undo didn't restore the settings before the override")
    pump()
    check(panel.current is None and not Gui.Control.activeDialog(), "the panel stayed open after an undo of its settings")
    doc.redo()
    check(settings.get(doc).mass_overrides == {"Arm": 2.5}, "redo didn't bring the override back")
    Gui.runCommand("Scrimmage_Part")                               # re-open: it reads the redone settings
    pump()
    p = panel.current
    check(p is not None and float(p.override_editors["Arm"].value()) == 2.5, "re-opened panel lost the override")

    # export folder and Export
    out = os.path.join(WORK, "arm_part")
    p.folder.setText(out)
    p.folder.editingFinished.emit()
    p.export_button.click()
    pump(500)
    result["summary"] = p.last_summary
    result["result_text"] = p.result.toPlainText()
    check(p.last_summary is not None, "the export failed: " + p.result.toPlainText())
    check(os.path.isfile(os.path.join(out, "part.toml")), "no part.toml written")

    Gui.SendMsgToActiveView("ViewFit")
    pump(500)
    png = os.path.join(WORK, "panel.png")
    Gui.getMainWindow().grab().save(png)
    result["png"] = png
    result["png_sections"] = save_sections(p, os.path.join(WORK, "panel_sections.png"))

    # the choices are saved in the document: save, close, re-open, open the panel again and re-export
    st = settings.get(doc)
    check(st is not None and [m["slot"] for m in st.motors] == ["pivot"], "motor slot not saved")
    check(st is not None and st.mass_overrides == {"Arm": 2.5}, "mass override not saved")
    p.reject()
    doc.save()
    App.closeDocument(doc.Name)
    pump()
    doc = App.openDocument(path)
    pump()
    Gui.runCommand("Scrimmage_Part")
    pump()
    p2 = panel.current
    check(p2 is not None and p2 is not p, "the panel didn't re-open")
    check(p2.folder.text() == out, "the export folder wasn't remembered")
    check(p2.motor_editor.list.count() == 1 and p2.mount_editor.list.count() == 1, "slots/mounts not re-loaded")
    p2.export_button.click()
    pump(300)
    check(p2.last_summary is not None and p2.last_summary["slots"] == result["summary"]["slots"],
          "re-export differs: " + p2.result.toPlainText())
    # closing the document under the open panel closes the panel
    App.closeDocument(doc.Name)
    pump()
    check(not Gui.Control.activeDialog() and panel.current is None, "the panel stayed open on a closed document")
    result["ok"] = not result["errors"]


def main():
    try:
        run()
    except BaseException:
        result["errors"].append(traceback.format_exc())
    finally:
        with open(os.path.join(WORK, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        QtCore.QTimer.singleShot(200, Gui.getMainWindow().close)


QtCore.QTimer.singleShot(1500, main)
