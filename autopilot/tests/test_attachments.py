import base64
import io
import itertools
import zipfile

import httpx
import pytest

from app.agent import AgentManager
from app.api import build_admin
from app.attachments import AttachmentStore, _ext, _safe_name, parse_multipart
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lK3Q6wAAAABJRU5ErkJggg=="
)
_bounds = itertools.count(1)


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def multipart(files: list[tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----vellur" + str(next(_bounds))
    body = bytearray()
    for name, data, ctype in files:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def admin(rig):
    ctrl = rig()
    router = Router(ctrl)
    agent = AgentManager(ctrl, router)
    app = build_admin(ctrl, router=router, agent=agent)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://ai.example")
    return ctrl, agent.attachments, client


async def test_raw_upload_info_content_list_and_delete(admin):
    _, store, client = admin
    r = await client.post("/autopilot/api/attachments", headers={**WRITE, "X-Filename": "notes.txt",
                                                                 "Content-Type": "text/plain"},
                          content=b"first line")
    assert r.status_code == 200
    att = r.json()
    assert att["name"] == "notes.txt" and att["kind"] == "text" and att["has_text"]
    assert store.get(att["id"])["conv_id"] is None

    r = await client.get(f"/autopilot/api/attachments/{att['id']}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["id"] == att["id"]
    r = await client.get(f"/autopilot/api/attachments/{att['id']}/content", headers=ADMIN)
    assert r.status_code == 200 and r.content == b"first line"
    assert r.headers["x-content-type-options"] == "nosniff"

    r = await client.get("/autopilot/api/attachments", headers=ADMIN, params={"unused": "true"})
    assert [a["id"] for a in r.json()["attachments"]] == [att["id"]]

    r = await client.delete(f"/autopilot/api/attachments/{att['id']}", headers=WRITE)
    assert r.json() == {"ok": True}
    assert store.get(att["id"]) is None


async def test_multipart_upload_and_limits(admin):
    ctrl, _, client = admin
    body, ctype = multipart([("a.txt", b"alpha", "text/plain"), ("b.json", b'{"x": 1}', "application/json")])
    r = await client.post("/autopilot/api/attachments", headers={**WRITE, "Content-Type": ctype}, content=body)
    assert r.status_code == 200 and len(r.json()["attachments"]) == 2
    assert {a["kind"] for a in r.json()["attachments"]} == {"text"}

    ctrl.settings.update({"attachment_max_count": 1})
    body, ctype = multipart([("a.txt", b"alpha", "text/plain"), ("b.txt", b"beta", "text/plain")])
    r = await client.post("/autopilot/api/attachments", headers={**WRITE, "Content-Type": ctype}, content=body)
    assert r.status_code == 400

    r = await client.post("/autopilot/api/attachments", headers={**WRITE, "X-Filename": "run.exe",
                                                                 "Content-Type": "application/octet-stream"},
                          content=b"\x7fELF something")
    assert r.status_code == 400


async def test_image_is_only_sent_to_vision_models(admin):
    _, store, _ = admin
    row = store.save(PNG, "pixel.png", "image/png")
    assert row["kind"] == "image" and row["meta"]["width"] == 1 and row["meta"]["height"] == 1
    plain = store.chat_content("look", [row], vision=False)
    assert isinstance(plain, list) and "image support is not enabled" in plain[0]["text"]
    seen = store.chat_content("look", [row], vision=True)
    assert seen[1]["type"] == "image_url" and seen[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_attachment_ownership(admin):
    _, store, _ = admin
    one = store.save(b"one", "one.txt", "text/plain")
    store.select([one["id"]], conv_id=7)
    assert store.get(one["id"])["conv_id"] == 7
    with pytest.raises(ValueError):
        store.select([one["id"]], conv_id=8)
    with pytest.raises(ValueError):
        store.select([one["id"]], task_id=9)
    with pytest.raises(ValueError):
        store.select([one["id"]])

    two = store.save(b"two", "two.txt", "text/plain")
    store.select([two["id"]], task_id=9)
    with pytest.raises(ValueError):
        store.select([two["id"]], task_id=10)
    with pytest.raises(ValueError):
        store.select([two["id"]], conv_id=7)
    assert store.select([two["id"]], task_id=9)[0]["id"] == two["id"]


async def test_document_extraction_and_unsafe_archive(admin):
    _, store, _ = admin
    docx = store.save(zipped({"word/document.xml": b"<w:document><w:body><w:p><w:r><w:t>Hello DOCX</w:t></w:r></w:p></w:body></w:document>"}),
                      "letter.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "Hello DOCX" in docx["extracted"]

    xlsx = store.save(zipped({
        "xl/workbook.xml": b"<workbook/>",
        "xl/sharedStrings.xml": b"<sst><si><t>Hello XLSX</t></si></sst>",
        "xl/worksheets/sheet1.xml": b'<worksheet><sheetData><row><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>',
    }), "sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "Hello XLSX" in xlsx["extracted"]

    pptx = store.save(zipped({"ppt/slides/slide1.xml": b"<p:sld><a:t>Hello PPTX</a:t></p:sld>"}),
                      "deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    assert "Hello PPTX" in pptx["extracted"]

    pdf = store.save(b"%PDF-1.4\n1 0 obj\n<<>>\nstream\nBT (Hello PDF) Tj ET\nendstream\nendobj\n%%EOF",
                     "doc.pdf", "application/pdf")
    assert "Hello PDF" in pdf["extracted"]

    with pytest.raises(ValueError):
        store.save(zipped({"../outside.txt": b"bad"}), "evil.zip", "application/zip")


def test_names_and_workspace_paths(admin):
    _, store, _ = admin
    assert _safe_name("../../etc/passwd") == "passwd"
    assert _ext(".env") == ".env" and _ext("a.tar.gz") == ".gz"
    row = store.save(b"x", "n" * 250 + ".txt", "text/plain")
    name = store.workspace_name(row)
    assert len(name) <= 180 and name.endswith(".txt")


def test_multipart_parser_keeps_binary_data():
    data = b"a\r\n--not-the-boundary\r\nb"
    body, ctype = multipart([("bin.dat", data, "application/octet-stream")])
    assert parse_multipart(body, ctype) == [("bin.dat", data, "application/octet-stream")]
