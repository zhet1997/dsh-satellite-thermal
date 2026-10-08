from __future__ import print_function

import json
import os
import platform
import traceback

import NXOpen


def main():
    task_path = os.environ["UGNXSIM_TASK"]
    with open(task_path, "r", encoding="utf-8-sig") as stream:
        task = json.load(stream)
    result = {"ok": False}
    try:
        session = NXOpen.Session.GetSession()
        result = {
            "ok": session is not None,
            "nxopen_module": getattr(NXOpen, "__file__", None),
            "python": platform.python_version(),
            "ugii_base_dir": os.environ.get("UGII_BASE_DIR"),
        }
        print("UGNXSIM_NXOPEN_PROBE_OK=1")
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    with open(task["result_path"], "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    if not result["ok"]:
        raise RuntimeError(result.get("error", "NXOpen probe failed"))


if __name__ == "__main__":
    main()
