#!/usr/bin/env python3
"""A stand-in for `opencode run --format json`, used only by tests/test_recovery_integration.py.

It behaves like a two-step agent and makes every side effect observable, so a test can prove that recovery does not
repeat finished work:
  step 1  writes step1.txt (appends one line), reports a completed `write` tool event
  pause   a long `bash` tool (`npm test`) that waits for the file `release` (the test kills processes during it)
  step 2  writes step2.txt, reports the final summary
Every invocation is counted in ../invocations (outside the repository). With --session (a resumed run) it inspects the
workspace first and skips step 1 when step1.txt already exists, as the recovery prompt instructs a real agent to do.
"""
import json
import os
import sys
import time

args = sys.argv[1:]
session = args[args.index("--session") + 1] if "--session" in args else "ses_fake"
home = os.environ.get("HOME", ".")
with open(os.path.join(home, "..", "invocations"), "a") as f:
    f.write(("resume" if "--session" in args else "first") + "\n")


def emit(typ, **part):
    print(json.dumps({"type": typ, "sessionID": session, "part": part}), flush=True)


emit("step_start")
if os.path.exists("step1.txt"):
    emit("text", text="Inspected the workspace: step 1 is already done (step1.txt exists); continuing with step 2.")
else:
    with open("step1.txt", "a") as f:
        f.write("step one\n")
    emit("tool_use", tool="write", state={"status": "completed", "input": {"filePath": "step1.txt", "content": "step one"},
                                          "output": "", "title": "step1.txt"})
    emit("tool_use", tool="bash", state={"status": "running", "input": {"command": "npm test", "description": "npm test"}})
    deadline = time.time() + 60
    while not os.path.exists("release") and time.time() < deadline:
        time.sleep(0.1)
    emit("tool_use", tool="bash", state={"status": "completed", "input": {"command": "npm test", "description": "npm test"},
                                         "output": "ok", "metadata": {"exit": 0}, "title": "npm test"})
with open("step2.txt", "a") as f:
    f.write("step two\n")
emit("tool_use", tool="write", state={"status": "completed", "input": {"filePath": "step2.txt", "content": "step two"},
                                      "output": "", "title": "step2.txt"})
emit("text", text="## Summary\nBoth steps done.\n## Done\n- step1.txt\n- step2.txt\n## Not done\nnothing\n## Tests\n"
                  "npm test passed\nSTATUS: COMPLETE")
emit("step_finish", reason="stop", tokens={"input": 10, "output": 5})
