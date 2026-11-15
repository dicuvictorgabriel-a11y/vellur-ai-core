"""Shared attachment store for workspace chat and coding-agent tasks."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import struct
import time
import zipfile
import zlib
from typing import Iterable, Optional
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .lifecycle import Controller

ID_RE = re.compile(r"^att_[a-f0-9]{32}$")
SAFE_RE = re.compile(r"[^\w.()+\- ]+", re.UNICODE)
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".log", ".sql", ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css",
    ".scss", ".sass", ".less", ".html", ".htm", ".vue", ".svelte", ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".bat", ".cmd", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".java", ".kt", ".kts", ".swift",
    ".go", ".rs", ".rb", ".php", ".pl", ".lua", ".r", ".m", ".mm", ".dart", ".scala", ".clj", ".ex", ".exs",
    ".erl", ".hs", ".fs", ".fsx", ".vb", ".asm", ".s", ".dockerfile", ".env", ".gitignore", ".editorconfig",
}
TEXT_MIMES = {
    "application/json", "application/xml", "application/javascript", "application/x-javascript",
    "application/x-sh", "application/x-httpd-php", "application/sql", "application/x-yaml",
    "application/toml", "application/rtf",
}
DOC_MIMES = {
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
MODEL_EXT = {".glb", ".gltf", ".obj", ".stl", ".blend"}
ARCHIVE_EXT = {".zip"}
ARCHIVE_MIMES = {"application/zip", "application/x-zip-compressed"}
DANGEROUS = {".exe", ".dll", ".so", ".dylib", ".bin", ".com", ".msi", ".scr", ".bat", ".cmd", ".ps1", ".jar", ".apk",
             ".app", ".deb", ".rpm", ".iso", ".img", ".dmg", ".vbs", ".wsf", ".hta"}
EXEC_MAGIC = (b"\x7fELF", b"MZ", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")
MAX_ARCHIVE_ENTRIES = 2000
MAX_ARCHIVE_UNCOMPRESSED = 120 * 1024 * 1024
MAX_ARCHIVE_RATIO = 120
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_PDF_STREAMS = 200
MAX_PDF_DECOMPRESSED = 8 * 1024 * 1024
WORKSPACE_NAME_LIMIT = 180
MAX_REQUEST_BYTES = 100 * 1024 * 1024


def _now() -> float:
    return time.time()


def _safe_name(name: str, fallback: str = "file") -> str:
    name = unquote(str(name or "")).replace("\\", "/").split("/")[-1].replace("\x00", "")
    name = SAFE_RE.sub("_", name).strip().rstrip(" .")
    if name in ("", ".", ".."):
        return fallback
    return _truncate_name(name, 180)


def _ext(name: str) -> str:
    base = os.path.basename(name or "").lower()
    if base.startswith(".") and base.count(".") == 1:
        return base
    return os.path.splitext(base)[1]


def _cut(text: str, chars: int, max_bytes: int = 240) -> str:
    out, used = [], 0
    for ch in text:
        n = len(ch.encode("utf-8"))
        if len(out) >= chars or used + n > max_bytes:
            break
        out.append(ch)
        used += n
    return "".join(out)


def _truncate_name(name: str, limit: int = 180) -> str:
    if len(name) <= limit and len(name.encode("utf-8")) <= 240:
        return name
    ext = os.path.splitext(name)[1]
    suffix = ext if ext and len(ext) < limit and len(ext.encode("utf-8")) <= 200 else ""
    stem = name[:-len(ext)] if suffix else name
    stem = _cut(stem, limit - len(suffix), 240 - len(suffix.encode("utf-8"))).rstrip(" .")
    return (stem + suffix) or "file"


def _is_text_mime(mime: str) -> bool:
    return mime.startswith("text/") or mime in TEXT_MIMES or mime.endswith("+json") or mime.endswith("+xml")


def _ooxml_mime(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return ""
    if "word/document.xml" in names:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if "xl/workbook.xml" in names:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if any(n.startswith("ppt/slides/slide") for n in names):
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return ""


def _sniff(data: bytes, name: str, declared: str) -> tuple[str, str]:
    ext = _ext(name)
    mime = (declared or "").split(";", 1)[0].strip().lower()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "image"
    if data.startswith(b"%PDF-"):
        return "application/pdf", "document"
    if data.startswith(b"PK\x03\x04"):
        ooxml = _ooxml_mime(data)
        if ooxml:
            return ooxml, "document"
        if ext == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"
        if ext == ".xlsx":
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "document"
        if ext == ".pptx":
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation", "document"
        return "application/zip", "archive"
    if data[:4] == b"glTF":
        return "model/gltf-binary", "model"
    if ext == ".gltf" and data.lstrip()[:1] in (b"{", b"["):
        return "model/gltf+json", "model"
    if ext == ".blend" or data.startswith(b"BLENDER"):
        return "application/x-blender", "model"
    if ext == ".obj":
        return "text/plain", "model"
    if ext == ".stl":
        return "model/stl", "model"
    head = data[:4096]
    looks_svg = bool(re.match(rb"(?:\xef\xbb\xbf)?\s*(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*<svg\b",
                              head, re.I | re.S))
    if ext == ".svg" or mime == "image/svg+xml" or (looks_svg and (not mime or _is_text_mime(mime))):
        return "image/svg+xml", "image"
    if _is_text_mime(mime):
        return mime, "text"
    if ext in TEXT_EXT:
        guessed = mimetypes.guess_type(name)[0]
        return (guessed if guessed and _is_text_mime(guessed) else "text/plain"), "text"
    if b"\x00" not in data[:4096]:
        guessed = mimetypes.guess_type(name)[0]
        if guessed and (_is_text_mime(guessed) or guessed.startswith("image/") or guessed in DOC_MIMES):
            return guessed, "text" if _is_text_mime(guessed) else "file"
        return "text/plain", "text"
    return mime if mime and mime != "application/octet-stream" else "application/octet-stream", "file"


def _image_size(data: bytes, mime: str) -> dict:
    try:
        if mime == "image/png" and len(data) >= 24:
            return {"width": struct.unpack(">I", data[16:20])[0], "height": struct.unpack(">I", data[20:24])[0]}
        if mime == "image/gif" and len(data) >= 10:
            return {"width": struct.unpack("<H", data[6:8])[0], "height": struct.unpack("<H", data[8:10])[0]}
        if mime == "image/webp" and len(data) >= 30:
            if data[12:16] == b"VP8X":
                return {"width": 1 + int.from_bytes(data[24:27], "little"), "height": 1 + int.from_bytes(data[27:30], "little")}
            if data[12:16] == b"VP8 " and len(data) >= 30:
                return {"width": struct.unpack("<H", data[26:28])[0] & 0x3fff,
                        "height": struct.unpack("<H", data[28:30])[0] & 0x3fff}
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return {"height": struct.unpack(">H", data[i + 5:i + 7])[0],
                            "width": struct.unpack(">H", data[i + 7:i + 9])[0]}
                i += 2 + max(0, length)
    except (ValueError, struct.error, IndexError):
        pass
    return {}


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if len(data) >= 4 and (data[1::2].count(0) >= len(data) // 4 or data[0::2].count(0) >= len(data) // 4):
        enc = "utf-16-le" if data[1::2].count(0) >= data[0::2].count(0) else "utf-16-be"
        return data.decode(enc, errors="replace")
    return data.decode("latin-1", errors="replace")


_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(",
                b")": b")", b"\\": b"\\"}


def _pdf_literal(raw: bytes) -> str:
    raw = re.sub(rb"\\([nrtbf()\\])", lambda x: _PDF_ESCAPES.get(x.group(1), x.group(1)), raw)
    return raw.decode("latin-1", errors="replace")


def _pdf_block_text(block: bytes) -> list[str]:
    parts: list[str] = []
    for m in re.finditer(rb"\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]+)>", block, re.S):
        token = m.group(0)
        if token.startswith(b"("):
            parts.append(_pdf_literal(token[1:-1]))
            continue
        digits = re.sub(rb"\s+", b"", m.group(1) or b"")
        if len(digits) % 2:
            digits += b"0"
        try:
            raw = bytes.fromhex(digits.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            continue
        if raw.startswith((b"\xfe\xff", b"\xff\xfe")):
            parts.append(raw.decode("utf-16", errors="replace"))
        else:
            parts.append(raw.decode("latin-1", errors="replace"))
    return parts


def _pdf_text(data: bytes) -> str:
    out: list[str] = []
    for i, m in enumerate(re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S)):
        if i >= MAX_PDF_STREAMS:
            break
        raw = m.group(1)
        try:
            dec = zlib.decompressobj().decompress(raw, MAX_PDF_DECOMPRESSED)
        except zlib.error:
            dec = raw[:MAX_PDF_DECOMPRESSED]
        if b"BT" not in dec:
            continue
        for block in re.findall(rb"BT(.*?)ET", dec, re.S):
            parts = _pdf_block_text(block)
            if parts:
                out.append(" ".join(parts))
    text = "\n".join(out)
    if not text.strip():
        text = "\n".join(x.decode("latin-1", errors="ignore") for x in re.findall(rb"\(([^()]{2,})\)", data))
    return text


def _zip_guard(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES or sum(max(0, n.file_size) for n in infos) > MAX_ARCHIVE_UNCOMPRESSED:
        raise ValueError("document is too large when unpacked")


def _zip_member(z: zipfile.ZipFile, name: str, cap: int = MAX_XML_BYTES) -> bytes:
    try:
        info = z.getinfo(name)
    except KeyError:
        return b""
    if info.file_size > cap:
        raise ValueError("document is too large when unpacked")
    with z.open(info) as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ValueError("document is too large when unpacked")
    return data


def _xml_text(xml: bytes) -> str:
    return html.unescape(re.sub(rb"<[^>]+>", b"", xml).decode("utf-8", errors="replace"))


def _xml_texts(xml: bytes) -> list[str]:
    return [_xml_text(x) for x in re.findall(rb"<(?:[A-Za-z0-9_]+:)?t(?:\s[^>]*)?>(.*?)</(?:[A-Za-z0-9_]+:)?t>",
                                            xml, re.S)]


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            _zip_guard(z)
            xml = _zip_member(z, "word/document.xml")
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return ""
    if not xml:
        return ""
    xml = re.sub(rb"</w:p\s*>", b"\n", xml)
    xml = re.sub(rb"<w:tab[^>]*/>", b"\t", xml)
    return _xml_text(xml)


def _sheet_key(name: str) -> tuple:
    m = re.search(r"(\d+)\.xml$", name)
    return (int(m.group(1)) if m else 0, name)


def _xlsx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            _zip_guard(z)
            shared: list[str] = []
            xml = _zip_member(z, "xl/sharedStrings.xml")
            if xml:
                for si in re.findall(rb"<si(?:\s[^>]*)?>(.*?)</si>", xml, re.S):
                    shared.append("".join(_xml_texts(si)))
            sheets = sorted((n for n in z.namelist()
                             if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)), key=_sheet_key)
            rows: list[str] = []
            for sheet in sheets:
                xml = _zip_member(z, sheet)
                for row in re.findall(rb"<row(?:\s[^>]*)?>(.*?)</row>", xml, re.S):
                    cells: list[str] = []
                    for cell in re.findall(rb"<c(?:\s[^>]*)?>.*?</c>", row, re.S):
                        typ = re.search(rb"\bt=\"([^\"]+)\"", cell)
                        t = typ.group(1) if typ else b""
                        value = re.search(rb"<v(?:\s[^>]*)?>(.*?)</v>", cell, re.S)
                        if t == b"s" and value:
                            try:
                                idx = int(_xml_text(value.group(1)).strip())
                                cells.append(shared[idx] if 0 <= idx < len(shared) else "")
                            except ValueError:
                                cells.append("")
                        elif t == b"inlineStr":
                            cells.append("".join(_xml_texts(cell)))
                        elif value:
                            cells.append(_xml_text(value.group(1)))
                        elif b"<is" in cell:
                            cells.append("".join(_xml_texts(cell)))
                    if any(c.strip() for c in cells):
                        rows.append("\t".join(cells))
            return "\n".join(rows)
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return ""


def _pptx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            _zip_guard(z)
            slides = sorted((n for n in z.namelist()
                             if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=_sheet_key)
            out: list[str] = []
            for i, slide in enumerate(slides, 1):
                texts = _xml_texts(_zip_member(z, slide))
                if texts:
                    out.append(f"Slide {i}:\n" + "\n".join(texts))
            return "\n\n".join(out)
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return ""


def _archive_info(data: bytes) -> tuple[dict, str]:
    info: dict = {"entries": []}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.infolist()
            total = sum(max(0, n.file_size) for n in names)
            if len(names) > MAX_ARCHIVE_ENTRIES or total > MAX_ARCHIVE_UNCOMPRESSED:
                raise ValueError("archive is too large when unpacked")
            for n in names[:MAX_ARCHIVE_ENTRIES]:
                rel = n.filename.replace("\\", "/")
                if (rel.startswith("/") or ".." in rel.split("/") or re.match(r"^[A-Za-z]:/", rel)
                        or (n.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("archive contains an unsafe path or link")
                if n.file_size > 20 * 1024 * 1024 or (n.compress_size and n.file_size / n.compress_size > MAX_ARCHIVE_RATIO):
                    raise ValueError("archive contains an unsafe entry")
                info["entries"].append({"name": n.filename[:240], "size": n.file_size})
            listing = set(z.namelist())
            text = ""
            if "word/document.xml" in listing:
                text = _docx_text(data)
            elif "xl/workbook.xml" in listing:
                text = _xlsx_text(data)
            elif any(n.startswith("ppt/slides/slide") for n in listing):
                text = _pptx_text(data)
    except zipfile.BadZipFile:
        return info, ""
    return info, text


def _model_info(data: bytes, mime: str) -> dict:
    info: dict = {}
    if mime in ("model/gltf+json", "model/gltf-binary"):
        try:
            if mime == "model/gltf-binary" and len(data) >= 20:
                n = struct.unpack("<I", data[12:16])[0]
                doc = json.loads(data[20:20 + n].decode("utf-8"))
            else:
                doc = json.loads(data.decode("utf-8"))
            info = {"generator": (doc.get("asset") or {}).get("generator", ""),
                    "nodes": len(doc.get("nodes") or []), "meshes": len(doc.get("meshes") or []),
                    "materials": len(doc.get("materials") or [])}
        except (ValueError, KeyError, struct.error, UnicodeDecodeError):
            pass
    elif mime == "text/plain" and data[:64].lstrip().startswith((b"v ", b"vn ", b"vt ", b"f ", b"o ", b"#", b"mtllib")):
        info = {"vertices": len(re.findall(rb"^v\s", data, re.M)), "faces": len(re.findall(rb"^f\s", data, re.M))}
    return info


class AttachmentStore:
    def __init__(self, ctrl: Controller):
        self.ctrl, self.db, self.env = ctrl, ctrl.db, ctrl.env
        self.root = os.path.join(ctrl.env.data_dir, "attachments")

    def limits(self) -> dict:
        st = self.ctrl.settings.get()
        return {"max_bytes": int(float(st.get("attachment_max_mb", 25)) * 1024 * 1024),
                "max_count": int(st.get("attachment_max_count", 10)),
                "max_total_bytes": int(float(st.get("attachment_max_total_mb", 60)) * 1024 * 1024),
                "max_text_chars": int(st.get("attachment_max_text_chars", 120000)),
                "vision_max_bytes": int(float(st.get("attachment_vision_max_mb", 10)) * 1024 * 1024)}

    def public(self, row: dict) -> dict:
        meta = row.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        return {"id": row["id"], "name": row["name"], "mime": row["mime"], "kind": row["kind"],
                "size": int(row["size"]), "created": row["created"], "sha256": row["sha256"],
                "meta": meta, "has_text": bool(row.get("extracted")),
                "url": f"/autopilot/api/attachments/{row['id']}/content",
                "preview_url": f"/autopilot/api/attachments/{row['id']}/content"}

    def get(self, aid: str) -> Optional[dict]:
        if not ID_RE.match(aid or ""):
            return None
        row = self.db.one("SELECT * FROM attachments WHERE id=?", (aid,))
        if row:
            try:
                row["meta"] = json.loads(row.get("meta") or "{}")
            except (TypeError, ValueError):
                row["meta"] = {}
        return row

    def path(self, row: dict) -> str:
        name = str(row.get("name", ""))
        if not ID_RE.match(row.get("id", "")) or not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError("invalid attachment path")
        folder = os.path.realpath(os.path.join(self.root, row["id"]))
        p = os.path.realpath(os.path.join(folder, name))
        if not p.startswith(folder + os.sep):
            raise ValueError("invalid attachment path")
        return p

    def save(self, data: bytes, filename: str, declared: str = "", conv_id: Optional[int] = None,
             task_id: Optional[int] = None) -> dict:
        lim = self.limits()
        if conv_id is not None and task_id is not None:
            raise ValueError("an attachment belongs to a conversation or to a task, not both")
        if not data:
            raise ValueError("the file is empty")
        if len(data) > lim["max_bytes"]:
            raise ValueError(f"file is larger than {lim['max_bytes'] // (1024 * 1024)} MB")
        name = _safe_name(filename)
        if _ext(name) in DANGEROUS or data.startswith(EXEC_MAGIC):
            raise ValueError("executable files are not accepted")
        mime, kind = _sniff(data, name, declared)
        if mime in ("application/x-msdownload", "application/x-executable", "application/x-sharedlib"):
            raise ValueError("executable files are not accepted")
        if mime == "application/zip" and _ext(name) != ".zip":
            name = _truncate_name(name + ".zip")
        meta: dict = {}
        text = ""
        if kind == "image":
            meta.update(_image_size(data, mime))
        elif mime == "application/pdf":
            text = _pdf_text(data)
            meta["pages"] = len(re.findall(rb"/Type\s*/Page\b", data))
        elif mime.startswith("application/vnd.openxmlformats-officedocument.wordprocessingml"):
            text = _docx_text(data)
        elif mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            text = _xlsx_text(data)
        elif mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
            text = _pptx_text(data)
        elif kind == "archive":
            meta, text = _archive_info(data)
        elif kind == "model":
            meta.update(_model_info(data, mime))
            if _ext(name) == ".gltf" and len(data) <= 8 * 1024 * 1024:
                text = _decode_text(data)
        elif kind == "text" or mime == "image/svg+xml":
            text = _decode_text(data)
        if len(text) > lim["max_text_chars"]:
            note = f"\n… [{len(text) - lim['max_text_chars']} more characters]"
            text = text[:lim["max_text_chars"] - len(note)] + note if lim["max_text_chars"] > len(note) + 20 else text[:lim["max_text_chars"]]
        aid = "att_" + secrets.token_hex(16)
        folder = os.path.join(self.root, aid)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        final = os.path.join(folder, name)
        tmp = final + ".tmp"
        row = {"id": aid, "name": name, "mime": mime, "kind": kind, "size": len(data),
               "sha256": hashlib.sha256(data).hexdigest(), "created": _now(), "conv_id": conv_id,
               "task_id": task_id, "extracted": text, "meta": json.dumps(meta)}
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, final)
            os.chmod(final, 0o600)
            self.db.x("INSERT INTO attachments(id,name,mime,kind,size,sha256,created,conv_id,task_id,extracted,meta) "
                      "VALUES(:id,:name,:mime,:kind,:size,:sha256,:created,:conv_id,:task_id,:extracted,:meta)", row)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            with contextlib.suppress(OSError):
                shutil.rmtree(folder)
            raise
        return self.get(aid)

    def select(self, ids: Iterable[str], conv_id: Optional[int] = None, task_id: Optional[int] = None) -> list[dict]:
        if conv_id is not None and task_id is not None:
            raise ValueError("an attachment belongs to a conversation or to a task, not both")
        lim = self.limits()
        out, seen = [], set()
        for aid in ids or []:
            if not isinstance(aid, str) or aid in seen:
                continue
            seen.add(aid)
            row = self.get(aid)
            if not row:
                raise ValueError("unknown attachment")
            owner_conv, owner_task = row.get("conv_id"), row.get("task_id")
            if conv_id is not None:
                if owner_task is not None:
                    raise ValueError("an attachment used by an agent task cannot be added to a conversation")
                if owner_conv not in (None, conv_id):
                    raise ValueError("an attachment can be used by only one conversation")
            elif task_id is not None:
                if owner_conv is not None:
                    raise ValueError("an attachment used in a conversation cannot be added to an agent task")
                if owner_task not in (None, task_id):
                    raise ValueError("an attachment can be used by only one agent task")
            elif owner_conv is not None or owner_task is not None:
                raise ValueError("an attachment already belongs to a conversation or task")
            out.append(row)
        if len(out) > lim["max_count"]:
            raise ValueError(f"at most {lim['max_count']} attachments per message")
        if task_id is not None and out:
            current = self.db.one("SELECT COUNT(*) AS n FROM attachments WHERE task_id=?", (task_id,))
            already = sum(1 for r in out if r.get("task_id") == task_id)
            if (current or {}).get("n", 0) + len(out) - already > lim["max_count"]:
                raise ValueError(f"at most {lim['max_count']} attachments per task")
        if conv_id is not None and out:
            marks = ",".join("?" * len(out))
            self.db.x(f"UPDATE attachments SET conv_id=? WHERE id IN ({marks})", (conv_id, *[r["id"] for r in out]))
        if task_id is not None and out:
            marks = ",".join("?" * len(out))
            self.db.x(f"UPDATE attachments SET task_id=? WHERE id IN ({marks})", (task_id, *[r["id"] for r in out]))
        return out

    def content(self, row: dict) -> bytes:
        with open(self.path(row), "rb") as f:
            return f.read()

    def delete(self, aid: str) -> bool:
        row = self.get(aid)
        if not row:
            return False
        try:
            p = self.path(row)
        except ValueError:
            p = ""
        if p:
            with contextlib.suppress(OSError):
                os.remove(p)
            with contextlib.suppress(OSError):
                os.rmdir(os.path.dirname(p))
        self.db.x("DELETE FROM attachments WHERE id=?", (aid,))
        return True

    def delete_for_conversation(self, cid: int) -> None:
        for row in self.db.all("SELECT * FROM attachments WHERE conv_id=?", (cid,)):
            self.delete(row["id"])

    def workspace_name(self, row: dict) -> str:
        prefix = row["id"][-8:] + "-"
        return prefix + _truncate_name(row["name"], WORKSPACE_NAME_LIMIT - len(prefix))

    def prompt_manifest(self, rows: list[dict], vision: bool = True) -> str:
        if not rows:
            return ""
        lim = self.limits()["max_text_chars"]
        lines = ["\n\nAttachments from the owner are in this workspace:"]
        for row in rows:
            rel = ".vellur/attachments/" + self.workspace_name(row)
            detail = f"- {rel} ({row['mime']}, {row['size']} bytes)"
            meta = row.get("meta") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except ValueError:
                    meta = {}
            if meta:
                bits = ", ".join(f"{k}={v}" for k, v in meta.items() if k not in ("entries",))
                if bits:
                    detail += " — " + bits[:300]
            lines.append(detail)
            if row.get("extracted"):
                lines.append(f"  Extracted text from {row['name']}:\n" + row["extracted"][:lim])
            elif row["kind"] == "archive":
                entries = meta.get("entries") or []
                lines.append("  Archive contents:\n" + "\n".join(
                    f"  - {e.get('name')} ({e.get('size')} bytes)" for e in entries[:200]))
            elif row["kind"] == "image":
                if vision:
                    lines.append("  This is an image. Open it with the read tool to see it; do not guess its contents.")
                else:
                    lines.append("  This is an image, but the selected model cannot see images; do not guess its contents.")
        return "\n".join(lines)

    def chat_content(self, text: str, rows: list[dict], vision: bool) -> str | list:
        if not rows:
            return text
        lim = self.limits()
        parts: list[dict] = [{"type": "text", "text": text or "(attachment)"}]
        for row in rows:
            if row["kind"] == "image" and row["mime"] != "image/svg+xml":
                if vision and row["size"] <= lim["vision_max_bytes"]:
                    try:
                        b64 = base64.b64encode(self.content(row)).decode("ascii")
                    except OSError:
                        parts[0]["text"] += f"\n\n[Image attachment `{row['name']}` is no longer available.]"
                        continue
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{row['mime']};base64,{b64}"}})
                else:
                    if not vision:
                        why = "image support is not enabled for this model"
                    else:
                        why = f"the image is larger than the {lim['vision_max_bytes'] // (1024 * 1024)} MB limit"
                    parts[0]["text"] += (f"\n\n[Image attachment `{row['name']}` ({row['mime']}, {row['size']} bytes) was "
                                         f"not sent as an image because {why}.]")
                continue
            header = f"\n\nAttachment `{row['name']}` ({row['mime']}, {row['size']} bytes)"
            if row["kind"] == "archive":
                entries = (row.get("meta") or {}).get("entries") or []
                parts[0]["text"] += header + " — archive contents:\n" + "\n".join(
                    f"- {e.get('name')} ({e.get('size')} bytes)" for e in entries[:200])
            elif row.get("extracted"):
                parts[0]["text"] += header + ":\n" + row["extracted"][:lim["max_text_chars"]]
            elif row["kind"] == "model":
                meta = row.get("meta") or {}
                parts[0]["text"] += header + " — 3D model" + (": " + ", ".join(f"{k}={v}" for k, v in meta.items()) if meta else "")
            else:
                parts[0]["text"] += header + "; its contents could not be extracted locally."
        return parts


def parse_multipart(body: bytes, content_type: str) -> list[tuple[str, bytes, str]]:
    m = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type or "", re.I)
    if not m:
        raise ValueError("multipart boundary missing")
    boundary = (m.group(1) or m.group(2) or "").strip().encode()
    if not boundary or len(boundary) > 200:
        raise ValueError("invalid multipart boundary")
    out: list[tuple[str, bytes, str]] = []
    for chunk in body.split(b"--" + boundary):
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        if not chunk or chunk == b"--":
            continue
        if b"\r\n\r\n" not in chunk:
            continue
        raw_headers, data = chunk.split(b"\r\n\r\n", 1)
        headers = {}
        for line in raw_headers.decode("latin-1", errors="replace").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        disp = headers.get("content-disposition", "")
        fn = re.search(r"filename\*?=(?:\"([^\"]*)\"|'([^']*)'|([^;\s]+))", disp, re.I)
        if not fn:
            continue
        name = next((g for g in fn.groups() if g is not None), "").strip()
        if name.lower().startswith("utf-8''"):
            name = name[7:]
        name = unquote(name)
        out.append((name, data, headers.get("content-type", "")))
    return out


async def _read_limited(request: Request, cap: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > cap:
            raise ValueError("too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def register(app: FastAPI, ctrl: Controller, store: AttachmentStore, admin, admin_write) -> None:
    @app.post("/autopilot/api/attachments", dependencies=[Depends(admin_write)])
    async def upload(request: Request):
        lim = store.limits()
        ctype = request.headers.get("content-type", "")
        multipart = ctype.lower().startswith("multipart/form-data")
        if multipart:
            cap = min(lim["max_bytes"] * lim["max_count"], lim["max_total_bytes"]) + 1024 * 1024
        else:
            cap = min(lim["max_bytes"], lim["max_total_bytes"])
        cap = min(cap, MAX_REQUEST_BYTES)
        try:
            n = int(request.headers.get("content-length") or 0)
        except ValueError:
            n = 0
        if n > cap:
            return JSONResponse(status_code=413, content={"error": "too_large", "message": "upload is too large"})
        try:
            body = await _read_limited(request, cap)
        except ValueError:
            return JSONResponse(status_code=413, content={"error": "too_large", "message": "upload is too large"})
        files: list[tuple[str, bytes, str]] = []
        try:
            if multipart:
                files = parse_multipart(body, ctype)
            else:
                name = request.headers.get("x-filename", "") or "file"
                files = [(name, body, ctype)]
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": str(e)})
        if not files:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": "no file in the upload"})
        if len(files) > lim["max_count"]:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": f"at most {lim['max_count']} files per upload"})
        if any(len(data) > lim["max_bytes"] for _, data, _ in files):
            return JSONResponse(status_code=413, content={"error": "too_large", "message": "a file is too large"})
        if sum(len(data) for _, data, _ in files) > lim["max_total_bytes"]:
            return JSONResponse(status_code=413, content={"error": "too_large", "message": "upload is too large"})
        rows = []
        try:
            for name, data, declared in files:
                rows.append(store.save(data, name, declared))
        except ValueError as e:
            for r in rows:
                store.delete(r["id"])
            return JSONResponse(status_code=400, content={"error": "invalid", "message": str(e)})
        out = {"attachments": [store.public(r) for r in rows]}
        if len(rows) == 1:
            out.update(store.public(rows[0]))
        return out

    @app.get("/autopilot/api/attachments", dependencies=[Depends(admin)])
    async def listing(conv_id: Optional[int] = None, task_id: Optional[int] = None, unused: bool = False,
                      limit: int = 100):
        if conv_id is not None and task_id is not None:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": "choose a conversation or a task"})
        where, args = [], []
        if conv_id is not None:
            where.append("conv_id=?")
            args.append(conv_id)
        if task_id is not None:
            where.append("task_id=?")
            args.append(task_id)
        if unused:
            where.append("conv_id IS NULL AND task_id IS NULL")
        sql = "SELECT * FROM attachments" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY created DESC,id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        return {"attachments": [store.public(r) for r in store.db.all(sql, tuple(args))]}

    @app.get("/autopilot/api/attachments/{aid}", dependencies=[Depends(admin)])
    async def info(aid: str):
        row = store.get(aid)
        if not row:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown attachment"})
        return store.public(row)

    @app.get("/autopilot/api/attachments/{aid}/content", dependencies=[Depends(admin)])
    async def content(aid: str):
        row = store.get(aid)
        if not row:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown attachment"})
        try:
            p = store.path(row)
            if not os.path.isfile(p):
                raise OSError("missing")
        except (OSError, ValueError):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "attachment file is missing"})
        headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
                   "Content-Disposition": "inline", "Cross-Origin-Resource-Policy": "same-origin"}
        active = {"text/html", "application/xhtml+xml", "image/svg+xml", "application/xml", "text/xml",
                  "application/javascript", "text/javascript"}
        if row["mime"] in active:
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return FileResponse(p, media_type=row["mime"], headers=headers)

    @app.delete("/autopilot/api/attachments/{aid}", dependencies=[Depends(admin_write)])
    async def remove(aid: str):
        return {"ok": store.delete(aid)}
