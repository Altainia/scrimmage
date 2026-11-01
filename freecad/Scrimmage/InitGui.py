# Scrimmage workbench. Qt/FreeCADGui are imported lazily, inside functions, so this file loads headless too.
import os

import FreeCAD
import FreeCADGui

try:
    import scrimmage_part
    _DIR = scrimmage_part.addon_dir()
except ImportError:
    _DIR = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "Scrimmage")


class _PartCommand:
    def GetResources(self):
        return {"MenuText": "scrimmage part", "ToolTip": "Export the active Assembly to a scrimmage part folder",
                "Pixmap": os.path.join(_DIR, "icons", "scrimmage.svg")}

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        from scrimmage_part import panel           # lazily: Qt and the exporter load only when the command runs
        panel.show()


class ScrimmageWorkbench(FreeCADGui.Workbench):
    MenuText = "scrimmage"
    ToolTip = "Export mechanisms to scrimmage part folders"
    Icon = os.path.join(_DIR, "icons", "scrimmage.svg")

    def Initialize(self):
        FreeCADGui.addCommand("Scrimmage_Part", _PartCommand())
        self.appendToolbar("scrimmage", ["Scrimmage_Part"])
        self.appendMenu("scrimmage", ["Scrimmage_Part"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(ScrimmageWorkbench())
