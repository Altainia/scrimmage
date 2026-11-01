"""The exporter's saved choices: a hidden `App::FeaturePython` named `ScrimmagePart` in the document, holding JSON.

The object has one `App::PropertyString` `Data`. Everything in it is keyed by FreeCAD internal names (`obj.Name`),
never labels, so renaming a part or joint keeps its settings:

    {"format": 1,
     "motors":   [{"slot", "joint", "motor", "count", "gear_ratio", "radius_mm" (Sliders), "invert", "efficiency"}],
     "encoders": [{"slot", "joint", "on": "joint" | "motor", "invert"}],
     "gyros":    [{"slot", "body_comp"}],
     "mounts":   [{"name", "comp", "frame_local": [[x, y, z] mm, [w, x, y, z]]}],   # in the component's own frame
     "mass_overrides": {comp: kg}, "contact": {comp: "wheel" | "collide"}, "wheel_friction": {comp: f},
     "export_dir": "..."}
"""

import json
from dataclasses import asdict, dataclass, field

from .errors import ExportError

FORMAT = 1
OBJECT_NAME = "ScrimmagePart"
TRANSACTION = "scrimmage settings"


@dataclass
class ScrimmageSettings:
    format: int = FORMAT
    motors: list = field(default_factory=list)
    encoders: list = field(default_factory=list)
    gyros: list = field(default_factory=list)
    mounts: list = field(default_factory=list)
    mass_overrides: dict = field(default_factory=dict)
    contact: dict = field(default_factory=dict)
    wheel_friction: dict = field(default_factory=dict)
    export_dir: str = ""

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text):
        try:
            d = json.loads(text) if text else {}
        except ValueError as e:
            raise ExportError([f"the saved scrimmage settings ({OBJECT_NAME}) are damaged ({e}); delete the "
                               f"{OBJECT_NAME} object and set the part up again"]) from None
        if not isinstance(d, dict):
            raise ExportError([f"the saved scrimmage settings ({OBJECT_NAME}) aren't a JSON object"])
        fmt = d.get("format", FORMAT)
        if not isinstance(fmt, int) or fmt > FORMAT:
            raise ExportError([f"the scrimmage settings were saved by a newer add-on (format {fmt}); update the "
                               "scrimmage add-on"])
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        s = cls(**known)
        for k in ("motors", "encoders", "gyros", "mounts"):
            if not isinstance(getattr(s, k), list) or not all(isinstance(x, dict) for x in getattr(s, k)):
                raise ExportError([f"the saved scrimmage settings have a damaged '{k}' list"])
        for k in ("mass_overrides", "contact", "wheel_friction"):
            if not isinstance(getattr(s, k), dict):
                raise ExportError([f"the saved scrimmage settings have a damaged '{k}' table"])
        return s


def _object(doc):
    for o in [doc.getObject(OBJECT_NAME)] + list(doc.Objects):     # FreeCAD renames it if the name was taken
        if (o is not None and o.TypeId == "App::FeaturePython" and "Data" in o.PropertiesList
                and o.getGroupOfProperty("Data") == "Scrimmage"):
            return o
    return None


def _create(doc):
    o = doc.addObject("App::FeaturePython", OBJECT_NAME)
    o.addProperty("App::PropertyString", "Data", "Scrimmage", "scrimmage part settings (JSON)")
    o.setPropertyStatus("Data", "Hidden")
    o.Visibility = False
    vo = getattr(o, "ViewObject", None)              # None headless
    if vo is not None:
        vo.ShowInTree = False
    return o


def get(doc, create=False):
    """The document's settings, or None when it has none (with `create`, a new empty settings object instead)."""
    o = _object(doc)
    if o is None:
        if not create:
            return None
        o = _create(doc)
        o.Data = ScrimmageSettings().to_json()
    return ScrimmageSettings.from_json(o.Data)


def save(doc, settings):
    """Write `settings` (a ScrimmageSettings) into the document's ScrimmagePart object, creating it if needed."""
    o = _object(doc) or _create(doc)
    o.Data = settings.to_json()


def save_undoable(doc, settings, name=TRANSACTION):
    """`save` inside one document transaction, so Edit → Undo (Ctrl+Z) restores the previous settings."""
    doc.openTransaction(name)
    try:
        save(doc, settings)
    except BaseException:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


def data(doc):
    """The raw JSON stored in the document's ScrimmagePart object, or None when it has none."""
    o = _object(doc)
    return o.Data if o is not None else None


def save_guarded(doc, settings, expected, name=TRANSACTION):
    """`save_undoable`, but only when the stored JSON is still `expected` (what the caller last read or wrote; None
    for no settings object). Returns the new stored JSON, or None without writing when something else changed it
    (an undo or redo, another panel, a script) — the caller's copy is stale then and must be re-read."""
    if data(doc) != expected:
        return None
    save_undoable(doc, settings, name)
    return data(doc)


def slotted_joints(settings):
    """Internal names of the joints that motor or encoder slots sit on (`tree.build` keeps them as tree joints)."""
    return {s.get("joint") for s in settings.motors + settings.encoders if isinstance(s.get("joint"), str)}
