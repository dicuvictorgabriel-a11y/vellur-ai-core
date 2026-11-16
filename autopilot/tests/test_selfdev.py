import httpx
import pytest

from app.api import build_admin

ADMIN = {"X-Vellur-Admin": "s" * 32}
MUTATE = {**ADMIN, "X-Vellur-Request": "1"}


async def test_selfdev_enforces_stage_order_guardrails_tests_and_human_review(rig):
    app = build_admin(rig())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ap") as client:
        pol = (await client.get("/autopilot/api/selfdev/policy", headers=ADMIN)).json()
        assert pol["stages"] == ["INSPECT", "PLAN", "EDIT", "TEST", "PREVIEW", "REVIEW"]
        assert pol["auto_deploy_to_production"] is False
        assert "autopilot/app/auth.py" in pol["frozen_files"]

        # Creating a run targeting frozen auth.py or .env must be blocked immediately
        bad_init = await client.post(
            "/autopilot/api/selfdev/runs",
            headers=MUTATE,
            json={"title": "Touch auth", "target_paths": ["autopilot/app/auth.py"]},
        )
        assert bad_init.status_code == 422

        bad_env = await client.post(
            "/autopilot/api/selfdev/runs",
            headers=MUTATE,
            json={"title": "Touch env", "target_paths": [".env"]},
        )
        assert bad_env.status_code == 422

        # Create a valid self-dev run in INSPECT
        created = await client.post(
            "/autopilot/api/selfdev/runs",
            headers=MUTATE,
            json={
                "title": "Improve 3D Studio preset metadata",
                "goal": "Add non-breaking preset info in studio3d.py",
                "target_paths": ["autopilot/app/studio3d.py"],
            },
        )
        assert created.status_code == 200
        run = created.json()
        rid = run["id"]
        assert run["stage"] == "INSPECT"
        assert run["production_deploy_blocked"] is True

        # Cannot skip INSPECT straight to PLAN, EDIT, TEST, PREVIEW, or REVIEW
        assert (await client.post(f"/autopilot/api/selfdev/runs/{rid}/plan", headers=MUTATE, json={"plan_text": "Skip inspect step now"})).status_code == 409
        assert (await client.post(f"/autopilot/api/selfdev/runs/{rid}/review", headers=MUTATE, json={"decision": "approve"})).status_code == 409

        # 1. INSPECT -> PLAN
        r_ins = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/inspect",
            headers=MUTATE,
            json={"summary": "Inspected studio3d.py material presets and verified frozen auth constraints.", "constraints": ["Do not modify auth.py"]},
        )
        assert r_ins.status_code == 200
        assert r_ins.json()["stage"] == "PLAN"
        assert r_ins.json()["inspection"]["files"][0]["exists"] is True

        # 2. PLAN -> EDIT (rejects live deploy commands in plan)
        bad_plan = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/plan",
            headers=MUTATE,
            json={"plan_text": "Update preset metadata and restart host", "verification_commands": ["systemctl restart caddy"]},
        )
        assert bad_plan.status_code == 422

        r_plan = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/plan",
            headers=MUTATE,
            json={
                "plan_text": "Add safe metadata helper to autopilot/app/studio3d.py and run pytest.",
                "planned_files": ["autopilot/app/studio3d.py"],
                "verification_commands": ["bash dev/test.sh tests/test_3d.py"],
            },
        )
        assert r_plan.status_code == 200
        assert r_plan.json()["stage"] == "EDIT"

        # 3. EDIT -> TEST (rejects touching secrets, path traversal, or localStorage in dashboard.html diff)
        bad_edit_traversal = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/edit",
            headers=MUTATE,
            json={"touched_files": ["../secrets/prod.key"]},
        )
        assert bad_edit_traversal.status_code == 422

        bad_edit_dash = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/edit",
            headers=MUTATE,
            json={
                "touched_files": ["autopilot/app/static/dashboard.html"],
                "diff_text": "--- a/autopilot/app/static/dashboard.html\n+++ b/autopilot/app/static/dashboard.html\n+window.localStorage.setItem('x','1');\n",
            },
        )
        assert bad_edit_dash.status_code == 422

        r_edit = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/edit",
            headers=MUTATE,
            json={"touched_files": ["autopilot/app/studio3d.py"], "summary": "Updated safe studio3d preset labels"},
        )
        assert r_edit.status_code == 200
        assert r_edit.json()["stage"] == "TEST"

        # 4. TEST -> PREVIEW (failing tests must block advancing to PREVIEW)
        fail_test = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/test",
            headers=MUTATE,
            json={"passed": False, "command": "bash dev/test.sh tests/test_3d.py", "output": "1 failed"},
        )
        assert fail_test.status_code == 422
        assert (await client.get(f"/autopilot/api/selfdev/runs/{rid}", headers=ADMIN)).json()["stage"] == "TEST"

        # Passing real local syntax/guardrail verification advances to PREVIEW
        pass_test = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/test",
            headers=MUTATE,
            json={"passed": True, "command": "py_compile autopilot/app/studio3d.py", "run_local_checks": True},
        )
        assert pass_test.status_code == 200
        assert pass_test.json()["stage"] == "PREVIEW"

        # 5. PREVIEW -> REVIEW (forbids live production URL)
        bad_prev = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/preview",
            headers=MUTATE,
            json={"preview_url": "https://ai.vellurdigital.com/autopilot/"},
        )
        assert bad_prev.status_code == 422

        ok_prev = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/preview",
            headers=MUTATE,
            json={"preview_url": "http://127.0.0.1:7070/tasks/24/preview/", "notes": "Verified staging preview locally"},
        )
        assert ok_prev.status_code == 200
        assert ok_prev.json()["stage"] == "REVIEW"

        # 6. REVIEW -> request_changes returns to EDIT; approve transitions to APPROVED without auto-deploying
        req_changes = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/review",
            headers=MUTATE,
            json={"decision": "request_changes", "reviewer_note": "Add one extra docstring note"},
        )
        assert req_changes.status_code == 200
        assert req_changes.json()["stage"] == "EDIT"

        # Advance cleanly EDIT -> TEST -> PREVIEW -> REVIEW -> APPROVED
        assert (await client.post(f"/autopilot/api/selfdev/runs/{rid}/edit", headers=MUTATE, json={"touched_files": ["autopilot/app/studio3d.py"]})).status_code == 200
        assert (await client.post(f"/autopilot/api/selfdev/runs/{rid}/test", headers=MUTATE, json={"passed": True, "run_local_checks": True})).status_code == 200
        assert (await client.post(f"/autopilot/api/selfdev/runs/{rid}/preview", headers=MUTATE, json={"preview_url": "/autopilot/3d/"})).status_code == 200
        approved = await client.post(
            f"/autopilot/api/selfdev/runs/{rid}/review",
            headers=MUTATE,
            json={"decision": "approve", "reviewer_note": "Approved for PR creation"},
        )
        assert approved.status_code == 200
        final_run = approved.json()
        assert final_run["stage"] == "APPROVED"
        assert final_run["review"]["auto_deployed"] is False
        assert final_run["production_deploy_blocked"] is True
