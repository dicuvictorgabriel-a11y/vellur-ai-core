from __future__ import annotations

import json
import math
import struct
import zlib


def hex_rgb(value: str, fallback: tuple[float, float, float] = (0.8, 0.82, 0.85)) -> tuple[float, float, float]:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return fallback
    try:
        return tuple(round((int(s[i:i + 2], 16) / 255.0) ** 2.2, 6) for i in (0, 2, 4))
    except ValueError:
        return fallback


def hex_srgb_bytes(value: str, fallback: tuple[int, int, int] = (200, 205, 212)) -> tuple[int, int, int]:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


def euler_to_quat(rx: float, ry: float, rz: float) -> list[float]:
    cx, sx = math.cos(rx * 0.5), math.sin(rx * 0.5)
    cy, sy = math.cos(ry * 0.5), math.sin(ry * 0.5)
    cz, sz = math.cos(rz * 0.5), math.sin(rz * 0.5)
    return [
        round(sx * cy * cz + cx * sy * sz, 6),
        round(cx * sy * cz - sx * cy * sz, 6),
        round(cx * cy * sz + sx * sy * cz, 6),
        round(cx * cy * cz - sx * sy * sz, 6),
    ]


def _box_mesh(w: float, h: float, d: float):
    hx, hy, hz = w * 0.5, h * 0.5, d * 0.5
    faces = [
        ((0, 0, 1), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
        ((0, 1, 0), [(-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
        ((1, 0, 0), [(hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
    ]
    pos, nrm, idx = [], [], []
    for normal, verts in faces:
        base = len(pos)
        for v in verts:
            pos.append(v)
            nrm.append(normal)
        idx.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return pos, nrm, idx


def _plane_mesh(w: float, d: float):
    hx, hz = w * 0.5, d * 0.5
    pos = [(-hx, 0.0, hz), (hx, 0.0, hz), (hx, 0.0, -hz), (-hx, 0.0, -hz)]
    nrm = [(0.0, 1.0, 0.0)] * 4
    idx = [0, 1, 2, 0, 2, 3]
    return pos, nrm, idx


def _sphere_mesh(r: float, seg_u: int = 28, seg_v: int = 18):
    pos, nrm, idx = [], [], []
    for y in range(seg_v + 1):
        v = y / seg_v
        phi = v * math.pi
        sp, cp = math.sin(phi), math.cos(phi)
        for x in range(seg_u + 1):
            u = x / seg_u
            theta = u * math.pi * 2.0
            nx, ny, nz = -math.cos(theta) * sp, cp, math.sin(theta) * sp
            pos.append((nx * r, ny * r, nz * r))
            nrm.append((nx, ny, nz))
    stride = seg_u + 1
    for y in range(seg_v):
        for x in range(seg_u):
            a = y * stride + x
            b = a + stride
            idx.extend([a, b, a + 1, b, b + 1, a + 1])
    return pos, nrm, idx


def _cylinder_mesh(rt: float, rb: float, h: float, seg: int = 24):
    pos, nrm, idx = [], [], []
    hy = h * 0.5
    slope = (rb - rt) / max(h, 1e-5)
    for y_i, (yv, rv) in enumerate(((-hy, rb), (hy, rt))):
        for i in range(seg + 1):
            th = (i / seg) * math.pi * 2.0
            ct, st = math.cos(th), math.sin(th)
            l_inv = 1.0 / math.hypot(1.0, slope)
            pos.append((ct * rv, yv, st * rv))
            nrm.append((ct * l_inv, slope * l_inv, st * l_inv))
    stride = seg + 1
    for i in range(seg):
        a, b = i, i + stride
        idx.extend([a, a + 1, b, a + 1, b + 1, b])
    for top, yv, rv, ny in ((True, hy, rt, 1.0), (False, -hy, rb, -1.0)):
        if rv <= 1e-5:
            continue
        center = len(pos)
        pos.append((0.0, yv, 0.0))
        nrm.append((0.0, ny, 0.0))
        ring = len(pos)
        for i in range(seg + 1):
            th = (i / seg) * math.pi * 2.0
            pos.append((math.cos(th) * rv, yv, math.sin(th) * rv))
            nrm.append((0.0, ny, 0.0))
        for i in range(seg):
            if top:
                idx.extend([center, ring + i + 1, ring + i])
            else:
                idx.extend([center, ring + i, ring + i + 1])
    return pos, nrm, idx


def _torus_mesh(r: float, tube: float, seg_r: int = 28, seg_t: int = 16):
    pos, nrm, idx = [], [], []
    for j in range(seg_t + 1):
        v = (j / seg_t) * math.pi * 2.0
        cv, sv = math.cos(v), math.sin(v)
        for i in range(seg_r + 1):
            u = (i / seg_r) * math.pi * 2.0
            cu, su = math.cos(u), math.sin(u)
            px = (r + tube * cv) * cu
            py = tube * sv
            pz = (r + tube * cv) * su
            pos.append((px, py, pz))
            nrm.append((cv * cu, sv, cv * su))
    stride = seg_r + 1
    for j in range(seg_t):
        for i in range(seg_r):
            a = j * stride + i
            b = a + stride
            idx.extend([a, b, a + 1, b, b + 1, a + 1])
    return pos, nrm, idx


def build_primitive_geometry(primitive: str, params: dict):
    kind = str(primitive or "box").lower()
    p = params or {}
    if kind == "sphere":
        return _sphere_mesh(max(0.02, float(p.get("radius", 0.5))))
    if kind == "plane":
        return _plane_mesh(max(0.05, float(p.get("width", 4.0))), max(0.05, float(p.get("depth", 4.0))))
    if kind in ("cylinder", "cone", "capsule"):
        rt = 0.0 if kind == "cone" else max(0.02, float(p.get("radiusTop", p.get("radius", 0.4))))
        rb = max(0.02, float(p.get("radiusBottom", p.get("radius", 0.4))))
        return _cylinder_mesh(rt, rb, max(0.04, float(p.get("height", 1.0))))
    if kind == "torus":
        return _torus_mesh(max(0.05, float(p.get("radius", 0.55))), max(0.01, float(p.get("tube", 0.16))))
    return _box_mesh(
        max(0.02, float(p.get("width", 1.0))),
        max(0.02, float(p.get("height", 1.0))),
        max(0.02, float(p.get("depth", 1.0))),
    )


def scene_to_glb(scene: dict) -> bytes:
    objects = [o for o in (scene.get("objects") or []) if o.get("visible", True)]
    if not objects:
        objects = [{"id": "placeholder", "name": "Placeholder", "primitive": "box",
                    "position": [0, 0.5, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1],
                    "params": {"width": 1, "height": 1, "depth": 1}, "material": {"color": "#9aa4b2"}}]

    bin_blob = bytearray()
    buffer_views, accessors, meshes, materials, nodes = [], [], [], [], []
    used_ext = set()

    def push_bytes(raw: bytes, target: int | None = None) -> int:
        while len(bin_blob) % 4:
            bin_blob.append(0)
        offset = len(bin_blob)
        bin_blob.extend(raw)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    for idx_o, obj in enumerate(objects):
        pos, nrm, indices = build_primitive_geometry(obj.get("primitive", "box"), obj.get("params") or {})
        pos_bytes = b"".join(struct.pack("<fff", *v) for v in pos)
        nrm_bytes = b"".join(struct.pack("<fff", *v) for v in nrm)
        use_u32 = len(pos) > 65535
        idx_bytes = b"".join(struct.pack("<I" if use_u32 else "<H", i) for i in indices)

        mins = [min(v[c] for v in pos) for c in range(3)]
        maxs = [max(v[c] for v in pos) for c in range(3)]
        bv_pos = push_bytes(pos_bytes, 34962)
        bv_nrm = push_bytes(nrm_bytes, 34962)
        bv_idx = push_bytes(idx_bytes, 34963)

        acc_pos = len(accessors)
        accessors.append({
            "bufferView": bv_pos, "componentType": 5126, "count": len(pos), "type": "VEC3",
            "min": [round(x, 6) for x in mins], "max": [round(x, 6) for x in maxs],
        })
        acc_nrm = len(accessors)
        accessors.append({"bufferView": bv_nrm, "componentType": 5126, "count": len(nrm), "type": "VEC3"})
        acc_idx = len(accessors)
        accessors.append({
            "bufferView": bv_idx, "componentType": 5125 if use_u32 else 5123,
            "count": len(indices), "type": "SCALAR",
        })

        mat = obj.get("material") or {}
        r, g, b = hex_rgb(mat.get("color", "#cfd6df"))
        opacity = max(0.05, min(1.0, float(mat.get("opacity", 1.0))))
        transmission = max(0.0, min(1.0, float(mat.get("transmission", 0.0))))
        metalness = max(0.0, min(1.0, float(mat.get("metalness", 0.1))))
        roughness = max(0.02, min(1.0, float(mat.get("roughness", 0.5))))
        ior = max(1.0, min(2.4, float(mat.get("ior", 1.5))))
        alpha = min(opacity, max(0.18, 1.0 - transmission * 0.72)) if transmission > 0.01 else opacity

        gltf_mat: dict = {
            "name": str(mat.get("name") or obj.get("name") or f"Material_{idx_o}"),
            "pbrMetallicRoughness": {
                "baseColorFactor": [r, g, b, round(alpha, 4)],
                "metallicFactor": round(metalness, 4),
                "roughnessFactor": round(roughness, 4),
            },
            "doubleSided": bool(transmission > 0.01 or opacity < 0.99 or obj.get("primitive") == "plane"),
        }
        if alpha < 0.995 or transmission > 0.01:
            gltf_mat["alphaMode"] = "BLEND"
        em_int = max(0.0, float(mat.get("emissive_intensity", 0.0)))
        if em_int > 0.01:
            er, eg, eb = hex_rgb(mat.get("emissive", "#ffffff"), (1.0, 1.0, 1.0))
            scale_em = min(1.0, em_int)
            gltf_mat["emissiveFactor"] = [round(er * scale_em, 4), round(eg * scale_em, 4), round(eb * scale_em, 4)]
        if transmission > 0.01:
            used_ext.update(("KHR_materials_transmission", "KHR_materials_ior"))
            gltf_mat["extensions"] = {
                "KHR_materials_transmission": {"transmissionFactor": round(transmission, 4)},
                "KHR_materials_ior": {"ior": round(ior, 4)},
            }
        mat_idx = len(materials)
        materials.append(gltf_mat)

        mesh_idx = len(meshes)
        meshes.append({
            "name": str(obj.get("name") or f"Mesh_{idx_o}"),
            "primitives": [{"attributes": {"POSITION": acc_pos, "NORMAL": acc_nrm}, "indices": acc_idx, "material": mat_idx}],
        })

        p3 = list(obj.get("position") or [0.0, 0.0, 0.0])[:3]
        r3 = list(obj.get("rotation") or [0.0, 0.0, 0.0])[:3]
        s3 = list(obj.get("scale") or [1.0, 1.0, 1.0])[:3]
        nodes.append({
            "name": str(obj.get("name") or obj.get("id") or f"Object_{idx_o}"),
            "mesh": mesh_idx,
            "translation": [round(float(x), 5) for x in p3],
            "rotation": euler_to_quat(float(r3[0]), float(r3[1]), float(r3[2])),
            "scale": [round(float(x), 5) for x in s3],
            "extras": {"id": obj.get("id", f"obj_{idx_o}"), "role": obj.get("role", "prop"), "primitive": obj.get("primitive", "box")},
        })

    while len(bin_blob) % 4:
        bin_blob.append(0)

    gltf_doc: dict = {
        "asset": {"version": "2.0", "generator": "NOVA 3D Studio"},
        "scene": 0,
        "scenes": [{
            "name": str(scene.get("title") or "NOVA Scene"),
            "nodes": list(range(len(nodes))),
            "extras": {
                "camera": scene.get("camera") or {},
                "lights": scene.get("lights") or [],
                "environment": scene.get("environment") or {},
            },
        }],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_blob)}],
    }
    if used_ext:
        gltf_doc["extensionsUsed"] = sorted(used_ext)

    json_bytes = json.dumps(gltf_doc, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
    header = b"glTF" + struct.pack("<II", 2, total_len)
    chunk_json = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    chunk_bin = struct.pack("<II", len(bin_blob), 0x004E4942) + bytes(bin_blob)
    return header + chunk_json + chunk_bin


def render_scene_preview_png(scene: dict, width: int = 480, height: int = 320) -> bytes:
    bg = hex_srgb_bytes((scene.get("environment") or {}).get("background", "#12161d"), (18, 22, 29))
    buf = bytearray(width * height * 3)
    for y in range(height):
        t = y / max(1, height - 1)
        r = min(255, int(bg[0] * (1.25 - 0.45 * t) + 12 * (1 - t)))
        g = min(255, int(bg[1] * (1.25 - 0.45 * t) + 16 * (1 - t)))
        b = min(255, int(bg[2] * (1.25 - 0.45 * t) + 22 * (1 - t)))
        row_s = y * width * 3
        buf[row_s:row_s + width * 3] = bytes((r, g, b)) * width

    cam = scene.get("camera") or {}
    cp = cam.get("position") or [3.6, 2.2, 4.4]
    ct = cam.get("target") or [0.0, 0.9, 0.0]
    fx, fy, fz = ct[0] - cp[0], ct[1] - cp[1], ct[2] - cp[2]
    fl = max(1e-5, math.sqrt(fx * fx + fy * fy + fz * fz))
    fx, fy, fz = fx / fl, fy / fl, fz / fl
    rx, ry, rz = -fz, 0.0, fx
    rl = max(1e-5, math.hypot(rx, rz))
    rx, rz = rx / rl, rz / rl
    ux, uy, uz = ry * fz - rz * fy, rz * fx - rx * fz, rx * fy - ry * fx
    fov_rad = math.radians(max(20.0, min(85.0, float(cam.get("fov", 38.0)))))
    focal = (height * 0.5) / math.tan(fov_rad * 0.5)

    def project(wx: float, wy: float, wz: float):
        dx, dy, dz = wx - cp[0], wy - cp[1], wz - cp[2]
        vz = dx * fx + dy * fy + dz * fz
        if vz <= 0.15:
            return None
        vx = dx * rx + dy * ry + dz * rz
        vy = dx * ux + dy * uy + dz * uz
        sx = int(width * 0.5 + (vx / vz) * focal)
        sy = int(height * 0.5 - (vy / vz) * focal)
        return sx, sy, vz

    tris = []
    for obj in scene.get("objects") or []:
        if not obj.get("visible", True):
            continue
        pos, nrm, indices = build_primitive_geometry(obj.get("primitive", "box"), obj.get("params") or {})
        ox, oy, oz = (obj.get("position") or [0, 0, 0])[:3]
        sx_o, sy_o, sz_o = (obj.get("scale") or [1, 1, 1])[:3]
        ry_rot = float((obj.get("rotation") or [0, 0, 0])[1])
        cy_r, sy_r = math.cos(ry_rot), math.sin(ry_rot)
        mat = obj.get("material") or {}
        col = hex_srgb_bytes(mat.get("color", "#cfd6df"))
        trans = float(mat.get("transmission", 0.0))
        op = float(mat.get("opacity", 1.0))
        alpha = max(0.22, min(1.0, op * (1.0 - trans * 0.55)))
        for i in range(0, len(indices), 3):
            pts, depth, shade_sum = [], 0.0, 0.0
            valid = True
            for k in indices[i:i + 3]:
                vx, vy, vz = pos[k][0] * sx_o, pos[k][1] * sy_o, pos[k][2] * sz_o
                wx = vx * cy_r + vz * sy_r + ox
                wy = vy + oy
                wz = -vx * sy_r + vz * cy_r + oz
                pr = project(wx, wy, wz)
                if pr is None:
                    valid = False
                    break
                pts.append((pr[0], pr[1]))
                depth += pr[2]
                nx, ny, nz = nrm[k]
                shade_sum += max(0.28, min(1.15, 0.45 + 0.45 * ny + 0.25 * nx + 0.15 * nz))
            if valid:
                tris.append((depth / 3.0, pts, col, shade_sum / 3.0, alpha))

    tris.sort(key=lambda item: item[0], reverse=True)
    for _, pts, col, shade, alpha in tris:
        (x0, y0), (x1, y1), (x2, y2) = pts
        min_x, max_x = max(0, min(x0, x1, x2)), min(width - 1, max(x0, x1, x2))
        min_y, max_y = max(0, min(y0, y1, y2)), min(height - 1, max(y0, y1, y2))
        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if denom == 0 or (max_x - min_x) * (max_y - min_y) > width * height:
            continue
        cr = min(255, int(col[0] * shade))
        cg = min(255, int(col[1] * shade))
        cb = min(255, int(col[2] * shade))
        inv_d = 1.0 / denom
        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) * inv_d
                w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) * inv_d
                w2 = 1.0 - w0 - w1
                if w0 >= 0 and w1 >= 0 and w2 >= 0:
                    off = (py * width + px) * 3
                    if alpha >= 0.98:
                        buf[off:off + 3] = bytes((cr, cg, cb))
                    else:
                        buf[off] = int(buf[off] * (1 - alpha) + cr * alpha)
                        buf[off + 1] = int(buf[off + 1] * (1 - alpha) + cg * alpha)
                        buf[off + 2] = int(buf[off + 2] * (1 - alpha) + cb * alpha)

    rows = [b"\x00" + bytes(buf[y * width * 3:(y + 1) * width * 3]) for y in range(height)]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + chunk(b"IEND", b"")
    )
