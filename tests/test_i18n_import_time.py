"""No text is made at import time (i18n.py: a t() at import would freeze the language the process started in):
every scrimmage module, imported fresh with t(), tn() and translated() watched, calls none of them from a module's
top level."""

import subprocess
import sys

PROBE = r'''
import importlib, os, pkgutil, sys
from scrimmage import i18n
import scrimmage
PACKAGE = os.path.dirname(scrimmage.__file__)
seen, failed = [], []

def watch(fn):
    def watched(*args, **kwargs):
        frame = sys._getframe(1)     # any scrimmage module's top level on the stack: the call runs at its import
        while frame is not None:
            code = frame.f_code
            if code.co_name == "<module>" and code.co_filename.startswith(PACKAGE):
                seen.append(f"{code.co_filename}:{frame.f_lineno} {fn.__name__}({args[0]!r:.60})")
                break
            frame = frame.f_back
        return fn(*args, **kwargs)
    watched.__name__ = fn.__name__
    return watched

real = {name: getattr(i18n, name) for name in ("t", "tn", "translated")}
watched = {name: watch(fn) for name, fn in real.items()}
for name, fn in watched.items():
    setattr(i18n, name, fn)
for module in list(sys.modules.values()):     # the modules `import scrimmage` already loaded hold the real ones
    for key, value in list(vars(module).items()) if module is not None else []:
        for name, fn in real.items():
            if value is fn:
                setattr(module, key, watched[name])
for m in pkgutil.walk_packages(scrimmage.__path__, "scrimmage."):
    if m.name.endswith("__main__"):
        continue
    try:
        importlib.import_module(m.name)
    except Exception as e:          # an optional dependency (FreeCAD, matplotlib): nothing of it to check here
        failed.append(f"{m.name}: {type(e).__name__}")
print("\n".join(seen))
print("FAILED " + "; ".join(failed), file=sys.stderr)
'''


def test_no_text_is_made_at_import_time():
    r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", r.stdout
    assert r.stderr.count(";") < 10, r.stderr       # nearly every module imported
