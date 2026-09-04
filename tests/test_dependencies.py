"""
Guard against a third-party import that is missing from requirements.txt.

A missing dependency only shows up on a clean install, where it breaks the app
at startup -- so check that every third-party module the app imports is declared.

Run:  python3 tests/test_dependencies.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "app")
REQ = os.path.join(HERE, "..", "requirements.txt")

# Import name -> distribution name, where they differ
ALIASES = {"PIL": "pillow", "yaml": "pyyaml", "dateutil": "python-dateutil",
           "sklearn": "scikit-learn", "osgeo": "gdal", "affine": "affine"}
# Provided transitively by a declared dependency
TRANSITIVE = {"affine": "rasterio", "starlette": "fastapi", "pydantic": "fastapi"}


def local_modules():
    return {f[:-3] for f in os.listdir(APP) if f.endswith(".py")}


def declared():
    names = set()
    with open(REQ, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            names.add(re.split(r"[\[><=!;\s]", line, 1)[0].lower())
    return names


def imported():
    mods = set()
    for fn in os.listdir(APP):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(APP, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


if __name__ == "__main__":
    stdlib = set(sys.stdlib_module_names)
    local = local_modules()
    req = declared()
    missing = []
    for mod in sorted(imported()):
        if mod in stdlib or mod in local:
            continue
        dist = ALIASES.get(mod, mod).lower()
        if dist in req:
            print(f"  PASS  {mod} -> declared as {dist}")
        elif mod in TRANSITIVE and TRANSITIVE[mod] in req:
            print(f"  PASS  {mod} -> provided by {TRANSITIVE[mod]}")
        else:
            print(f"  FAIL  {mod} is imported but not in requirements.txt")
            missing.append(mod)
    print()
    if missing:
        print(f"{len(missing)} undeclared dependency/dependencies: {', '.join(missing)}")
        sys.exit(1)
    print("All third-party imports are declared.")
