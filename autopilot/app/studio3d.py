from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import secrets
import struct
import time
from typing import Optional

from fastapi import Depends, FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .glb_builder import render_scene_preview_png, scene_to_glb

PRIMITIVES = ("box", "sphere", "cylinder", "cone", "plane", "torus", "capsule")

MATERIAL_PRESETS = {
    "frosted_glass": {
        "name": "Frosted Architectural Glass", "preset": "frosted_glass", "color": "#dceeff",
        "metalness": 0.04, "roughness": 0.38, "opacity": 0.62, "transmission": 0.72, "ior": 1.5,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "clear_glass": {
        "name": "Clear Architectural Glass", "preset": "clear_glass", "color": "#e8f4ff",
        "metalness": 0.02, "roughness": 0.06, "opacity": 0.24, "transmission": 0.94, "ior": 1.52,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "polished_concrete": {
        "name": "Polished Concrete", "preset": "polished_concrete", "color": "#7d838a",
        "metalness": 0.08, "roughness": 0.22, "opacity": 1.0, "transmission": 0.0, "ior": 1.48,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "chrome": {
        "name": "Mirror Chrome", "preset": "chrome", "color": "#f4f7fb",
        "metalness": 1.0, "roughness": 0.04, "opacity": 1.0, "transmission": 0.0, "ior": 2.1,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "brushed_metal": {
        "name": "Brushed Titanium", "preset": "brushed_metal", "color": "#b8c0ca",
        "metalness": 0.88, "roughness": 0.26, "opacity": 1.0, "transmission": 0.0, "ior": 1.8,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "matte_plaster": {
        "name": "Minimalist Architectural Matte", "preset": "matte_plaster", "color": "#d6dce4",
        "metalness": 0.02, "roughness": 0.82, "opacity": 1.0, "transmission": 0.0, "ior": 1.45,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "warm_oak": {
        "name": "Natural Oak", "preset": "warm_oak", "color": "#a87c56",
        "metalness": 0.03, "roughness": 0.58, "opacity": 1.0, "transmission": 0.0, "ior": 1.46,
        "emissive": "#000000", "emissive_intensity": 0.0,
    },
    "emissive_soft": {
        "name": "Soft Studio Diffuser", "preset": "emissive_soft", "color": "#f5f9ff",
        "metalness": 0.0, "roughness": 0.2, "opacity": 1.0, "transmission": 0.0, "ior": 1.4,
        "emissive": "#eef6ff", "emissive_intensity": 1.8,
    },
}

COLOR_WORDS = {
    "white": "#f3f5f8", "black": "#1c2026", "gray": "#7d838a", "grey": "#7d838a",
    "silver": "#d6dde6", "chrome": "#f4f7fb", "gold": "#e5be67", "bronze": "#b07d4f",
    "blue": "#4f8df5", "cyan": "#56d4e8", "teal": "#38b2ac", "green": "#48bb78",
    "emerald": "#2f9e66", "red": "#e05656", "crimson": "#c53030", "orange": "#ed8936",
    "amber": "#f6ad55", "purple": "#9f7aea", "violet": "#805ad5", "pink": "#ed64a6",
}


def _vec3(val, default: list[float]) -> list[float]:
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        try:
            return [round(float(val[0]), 4), round(float(val[1]), 4), round(float(val[2]), 4)]
        except (TypeError, ValueError):
            pass
    return list(default)


def _hex(val, default: str = "#cfd6df") -> str:
    s = str(val or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", s):
        return "#" + "".join(c * 2 for c in s[1:].lower())
    return default


def normalize_material(raw: dict | None, fallback_preset: str = "matte_plaster") -> dict:
    base = copy.deepcopy(MATERIAL_PRESETS.get(
        str((raw or {}).get("preset") or fallback_preset),
        MATERIAL_PRESETS[fallback_preset],
    ))
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    if raw.get("name"):
        out["name"] = str(raw["name"])[:80]
    if raw.get("preset"):
        out["preset"] = str(raw["preset"])[:40]
    if raw.get("color"):
        out["color"] = _hex(raw["color"], base["color"])
    if raw.get("emissive"):
        out["emissive"] = _hex(raw["emissive"], base["emissive"])
    for key, lo, hi in (
        ("metalness", 0.0, 1.0), ("roughness", 0.02, 1.0), ("opacity", 0.05, 1.0),
        ("transmission", 0.0, 1.0), ("ior", 1.0, 2.4), ("emissive_intensity", 0.0, 6.0),
    ):
        if key in raw and raw[key] is not None:
            try:
                out[key] = round(max(lo, min(hi, float(raw[key]))), 4)
            except (TypeError, ValueError):
                pass
    return out


def normalize_scene(raw: dict | None, title_fallback: str = "Untitled Scene") -> dict:
    src = raw if isinstance(raw, dict) else {}
    env_raw = src.get("environment") if isinstance(src.get("environment"), dict) else {}
    cam_raw = src.get("camera") if isinstance(src.get("camera"), dict) else {}
    environment = {
        "background": _hex(env_raw.get("background"), "#11161d"),
        "exposure": round(max(0.25, min(2.5, float(env_raw.get("exposure", 1.08) or 1.08))), 3),
        "grid": bool(env_raw.get("grid", True)),
        "studio_preset": str(env_raw.get("studio_preset") or "soft_studio")[:40],
    }
    camera = {
        "position": _vec3(cam_raw.get("position"), [3.6, 2.15, 4.4]),
        "target": _vec3(cam_raw.get("target"), [0.0, 0.95, 0.0]),
        "fov": round(max(20.0, min(85.0, float(cam_raw.get("fov", 38.0) or 38.0))), 2),
    }

    lights = []
    for idx, l_raw in enumerate(src.get("lights") or []):
        if not isinstance(l_raw, dict):
            continue
        ltype = str(l_raw.get("type") or "directional").lower()
        if ltype not in ("directional", "point", "spot", "ambient", "hemisphere"):
            ltype = "directional"
        lights.append({
            "id": str(l_raw.get("id") or f"light_{idx + 1}")[:50],
            "name": str(l_raw.get("name") or f"Light {idx + 1}")[:80],
            "type": ltype,
            "color": _hex(l_raw.get("color"), "#ffffff"),
            "intensity": round(max(0.0, min(12.0, float(l_raw.get("intensity", 1.6) or 1.6))), 3),
            "position": _vec3(l_raw.get("position"), [3.0, 4.5, 3.0]),
            "cast_shadow": bool(l_raw.get("cast_shadow", ltype in ("directional", "spot"))),
        })
    if not lights:
        lights = [
            {"id": "soft_key", "name": "Soft Studio Key", "type": "directional", "color": "#fff8f0",
             "intensity": 2.1, "position": [4.2, 6.2, 4.6], "cast_shadow": True},
            {"id": "cool_fill", "name": "Cool Fill Wash", "type": "directional", "color": "#bdd8ff",
             "intensity": 0.95, "position": [-4.5, 3.4, -3.2], "cast_shadow": False},
            {"id": "ambient_studio", "name": "Studio Hemisphere", "type": "hemisphere", "color": "#dce8f8",
             "intensity": 0.65, "position": [0.0, 5.0, 0.0], "cast_shadow": False},
        ]

    objects = []
    seen_ids = set()
    for idx, o_raw in enumerate(src.get("objects") or []):
        if not isinstance(o_raw, dict):
            continue
        oid = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(o_raw.get("id") or f"obj_{idx + 1}")).strip("_")[:50] or f"obj_{idx + 1}"
        if oid in seen_ids:
            oid = f"{oid}_{idx + 1}"
        seen_ids.add(oid)
        prim = str(o_raw.get("primitive") or "box").lower()
        if prim not in PRIMITIVES:
            prim = "box"
        p_in = o_raw.get("params") if isinstance(o_raw.get("params"), dict) else {}
        params = {}
        for pk in ("width", "height", "depth", "radius", "radiusTop", "radiusBottom", "tube"):
            if pk in p_in and p_in[pk] is not None:
                try:
                    params[pk] = round(max(0.02, min(80.0, float(p_in[pk]))), 4)
                except (TypeError, ValueError):
                    pass
        objects.append({
            "id": oid,
            "name": str(o_raw.get("name") or oid.replace("_", " ").title())[:80],
            "role": str(o_raw.get("role") or "prop")[:40],
            "primitive": prim,
            "visible": bool(o_raw.get("visible", True)),
            "position": _vec3(o_raw.get("position"), [0.0, 0.5, 0.0]),
            "rotation": _vec3(o_raw.get("rotation"), [0.0, 0.0, 0.0]),
            "scale": [max(0.05, x) for x in _vec3(o_raw.get("scale"), [1.0, 1.0, 1.0])],
            "params": params,
            "material": normalize_material(o_raw.get("material")),
        })

    return {
        "title": str(src.get("title") or title_fallback)[:120],
        "environment": environment,
        "camera": camera,
        "lights": lights[:12],
        "objects": objects[:80],
    }


def futuristic_room_template(title: str = "Minimalist Futuristic Room") -> dict:
    return normalize_scene({
        "title": title,
        "environment": {"background": "#10151c", "exposure": 1.12, "grid": False, "studio_preset": "soft_studio"},
        "camera": {"position": [3.65, 2.15, 4.45], "target": [0.0, 1.05, -0.2], "fov": 38.0},
        "lights": [
            {"id": "soft_key", "name": "Soft Studio Key", "type": "directional", "color": "#f8fbff",
             "intensity": 2.05, "position": [3.8, 5.8, 4.2], "cast_shadow": True},
            {"id": "cool_fill", "name": "Diffused Skylight Fill", "type": "directional", "color": "#b8d6ff",
             "intensity": 0.92, "position": [-4.2, 3.6, 2.5], "cast_shadow": False},
            {"id": "rim_wash", "name": "Back Architectural Wash", "type": "point", "color": "#e4f0ff",
             "intensity": 1.35, "position": [0.0, 2.45, -2.1], "cast_shadow": False},
            {"id": "studio_hemi", "name": "Soft Studio Ambient", "type": "hemisphere", "color": "#dde9f8",
             "intensity": 0.68, "position": [0.0, 4.0, 0.0], "cast_shadow": False},
        ],
        "objects": [
            {
                "id": "polished_concrete_floor", "name": "Polished Concrete Floor", "role": "floor",
                "primitive": "box", "position": [0.0, -0.07, 0.0], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 8.4, "height": 0.14, "depth": 7.6},
                "material": MATERIAL_PRESETS["polished_concrete"],
            },
            {
                "id": "back_architectural_wall", "name": "Back Architectural Wall", "role": "wall",
                "primitive": "box", "position": [0.0, 1.55, -2.85], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 8.4, "height": 3.1, "depth": 0.18},
                "material": MATERIAL_PRESETS["matte_plaster"],
            },
            {
                "id": "left_fin_wall", "name": "Left Minimalist Wall", "role": "wall",
                "primitive": "box", "position": [-4.1, 1.55, -0.4], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 0.18, "height": 3.1, "depth": 5.1},
                "material": MATERIAL_PRESETS["matte_plaster"],
            },
            {
                "id": "ceiling_light_coving", "name": "Soft Studio Ceiling Diffuser", "role": "light_fixture",
                "primitive": "box", "position": [0.0, 2.92, -0.6], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 4.8, "height": 0.08, "depth": 1.6},
                "material": MATERIAL_PRESETS["emissive_soft"],
            },
            {
                "id": "frosted_glass_panel_left", "name": "Frosted Glass Panel Left", "role": "glass",
                "primitive": "box", "position": [-1.55, 1.25, -1.15], "rotation": [0.0, 0.12, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 1.35, "height": 2.45, "depth": 0.06},
                "material": MATERIAL_PRESETS["frosted_glass"],
            },
            {
                "id": "frosted_glass_panel_center", "name": "Frosted Glass Panel Center", "role": "glass",
                "primitive": "box", "position": [0.0, 1.25, -1.35], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 1.45, "height": 2.45, "depth": 0.06},
                "material": MATERIAL_PRESETS["frosted_glass"],
            },
            {
                "id": "frosted_glass_panel_right", "name": "Frosted Glass Panel Right", "role": "glass",
                "primitive": "box", "position": [1.55, 1.25, -1.15], "rotation": [0.0, -0.12, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 1.35, "height": 2.45, "depth": 0.06},
                "material": MATERIAL_PRESETS["frosted_glass"],
            },
            {
                "id": "chrome_sphere", "name": "Chrome Sphere", "role": "hero",
                "primitive": "sphere", "position": [0.0, 0.68, 0.45], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"radius": 0.68},
                "material": MATERIAL_PRESETS["chrome"],
            },
            {
                "id": "glass_frame_rail", "name": "Brushed Frame Base Rail", "role": "trim",
                "primitive": "box", "position": [0.0, 0.03, -1.22], "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "params": {"width": 4.6, "height": 0.06, "depth": 0.42},
                "material": MATERIAL_PRESETS["brushed_metal"],
            },
        ],
    })


def generate_scene_from_prompt(prompt: str) -> tuple[dict, str]:
    text = " ".join(str(prompt or "").split())
    low = text.lower()

    is_arch_room = any(k in low for k in ("room", "interior", "concrete", "glass panel", "frosted glass", "pavilion", "gallery"))
    if is_arch_room and ("sphere" in low or "glass" in low or "concrete" in low):
        scene = futuristic_room_template(_derive_title(text, "Minimalist Futuristic Room"))
        notes = ["Built architectural room shell, polished concrete floor, 3 frosted glass panels, chrome hero sphere, and soft studio lighting."]
        if "clear glass" in low and "frosted" not in low:
            for o in scene["objects"]:
                if "glass" in o["id"] or o.get("role") == "glass":
                    o["material"] = copy.deepcopy(MATERIAL_PRESETS["clear_glass"])
            notes.append("Applied clear high-transmission glass.")
        return scene, " ".join(notes)

    title = _derive_title(text, "Generated Studio Scene")
    objects = [
        {
            "id": "studio_floor", "name": "Studio Floor", "role": "floor", "primitive": "box",
            "position": [0.0, -0.06, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0],
            "params": {"width": 7.5, "height": 0.12, "depth": 7.5},
            "material": MATERIAL_PRESETS["polished_concrete" if "concrete" in low else "matte_plaster"],
        }
    ]
    if "pedestal" in low or "plinth" in low or "product" in low or "podium" in low:
        objects.append({
            "id": "pedestal", "name": "Studio Pedestal", "role": "support", "primitive": "cylinder",
            "position": [0.0, 0.35, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0],
            "params": {"radiusTop": 0.85, "radiusBottom": 0.92, "height": 0.7},
            "material": MATERIAL_PRESETS["polished_concrete"],
        })
        hero_y = 1.25
    else:
        hero_y = 0.68

    mat_choice = "chrome" if any(w in low for w in ("chrome", "mirror", "metallic", "silver")) else (
        "frosted_glass" if "frosted" in low else ("clear_glass" if "glass" in low else "brushed_metal")
    )
    hero_mat = copy.deepcopy(MATERIAL_PRESETS[mat_choice])
    for word, hex_c in COLOR_WORDS.items():
        if re.search(rf"\b{word}\b", low) and word not in ("chrome", "silver"):
            hero_mat["color"] = hex_c
            break

    if "torus" in low or "ring" in low or "infinity" in low:
        prim, params = "torus", {"radius": 0.68, "tube": 0.2}
    elif "cylinder" in low or "column" in low or "pillar" in low:
        prim, params = "cylinder", {"radiusTop": 0.45, "radiusBottom": 0.45, "height": 1.4}
    elif "cube" in low or "box" in low or "monolith" in low:
        prim, params = "box", {"width": 1.1, "height": 1.35, "depth": 1.1}
    else:
        prim, params = "sphere", {"radius": 0.68}

    objects.append({
        "id": f"hero_{prim}", "name": f"Hero {prim.title()}", "role": "hero", "primitive": prim,
        "position": [0.0, hero_y, 0.0], "rotation": [0.0, 0.35, 0.0], "scale": [1.0, 1.0, 1.0],
        "params": params, "material": hero_mat,
    })

    if "glass" in low:
        objects.append({
            "id": "glass_backdrop", "name": "Glass Backdrop Panel", "role": "glass", "primitive": "box",
            "position": [0.0, 1.2, -1.25], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0],
            "params": {"width": 3.2, "height": 2.4, "depth": 0.06},
            "material": MATERIAL_PRESETS["frosted_glass" if "frosted" in low else "clear_glass"],
        })

    scene = normalize_scene({
        "title": title,
        "environment": {"background": "#0f141b", "exposure": 1.1, "grid": "room" not in low},
        "camera": {"position": [3.4, 2.05, 4.1], "target": [0.0, 0.9, 0.0], "fov": 38.0},
        "objects": objects,
    })
    return scene, f"Created {len(scene['objects'])} PBR objects from prompt."


def _match_objects(objects: list[dict], target_hint: str) -> list[dict]:
    hint = target_hint.lower().strip()
    if not hint:
        return []
    matched = []
    for o in objects:
        hay = f"{o.get('id', '')} {o.get('name', '')} {o.get('role', '')} {o.get('primitive', '')} {(o.get('material') or {}).get('preset', '')}".lower()
        if any(tok in hay for tok in hint.split() if len(tok) >= 3):
            matched.append(o)
    return matched


def mutate_scene_from_prompt(scene_in: dict, prompt: str) -> tuple[dict, list[str]]:
    scene = normalize_scene(copy.deepcopy(scene_in))
    text = " ".join(str(prompt or "").split())
    low = text.lower()
    changes: list[str] = []

    if any(k in low for k in ("more transparent", "clearer", "less frosted", "increase transparency", "see-through")):
        glass_objs = [o for o in scene["objects"] if "glass" in o["id"].lower() or "glass" in o["name"].lower()
                      or o.get("role") == "glass" or float((o.get("material") or {}).get("transmission", 0)) > 0.1]
        for o in glass_objs:
            m = o["material"]
            old_op = float(m.get("opacity", 0.62))
            old_tr = float(m.get("transmission", 0.72))
            old_rg = float(m.get("roughness", 0.38))
            m["opacity"] = round(max(0.14, min(0.32, old_op * 0.45)), 4)
            m["transmission"] = round(min(0.96, max(0.9, old_tr + 0.2)), 4)
            m["roughness"] = round(max(0.04, min(0.1, old_rg * 0.25)), 4)
            m["preset"] = "clear_glass"
            m["name"] = "High-Clarity Glass"
        if glass_objs:
            changes.append(f"Increased glass transparency on {len(glass_objs)} panel(s) (transmission {glass_objs[0]['material']['transmission']}, roughness {glass_objs[0]['material']['roughness']}).")

    elif any(k in low for k in ("more frosted", "less transparent", "more opaque", "blurrier glass")):
        glass_objs = _match_objects(scene["objects"], "glass")
        for o in glass_objs:
            m = o["material"]
            m["opacity"] = round(min(0.88, float(m.get("opacity", 0.4)) + 0.25), 4)
            m["roughness"] = round(min(0.65, float(m.get("roughness", 0.15)) + 0.28), 4)
            m["transmission"] = round(max(0.42, float(m.get("transmission", 0.8)) - 0.22), 4)
            m["preset"] = "frosted_glass"
        if glass_objs:
            changes.append(f"Increased glass frosting on {len(glass_objs)} panel(s).")

    move_patterns = re.finditer(
        r"(?:move|shift|slide|place|reposition)\s+(?:the\s+)?([a-z0-9_ -]{2,30}?)\s+(?:further\s+|slightly\s+|a bit\s+)?(?:to\s+the\s+|to\s+|towards\s+the\s+)?(left|right|forward|front|back|backward|up|upward|down|downward|center|centre)",
        low,
    )
    for m_re in move_patterns:
        target_name, direction = m_re.group(1).strip(), m_re.group(2).strip()
        targets = _match_objects(scene["objects"], target_name)
        if not targets and "sphere" in low:
            targets = _match_objects(scene["objects"], "sphere")
        step = 0.65 if ("slightly" in low or "a bit" in low) else (1.85 if "far" in low else 1.35)
        for obj in targets:
            pos = list(obj["position"])
            if direction == "left":
                pos[0] = round(pos[0] - step, 4)
            elif direction == "right":
                pos[0] = round(pos[0] + step, 4)
            elif direction in ("forward", "front"):
                pos[2] = round(pos[2] + step * 0.8, 4)
            elif direction in ("back", "backward"):
                pos[2] = round(pos[2] - step * 0.8, 4)
            elif direction in ("up", "upward"):
                pos[1] = round(pos[1] + step * 0.5, 4)
            elif direction in ("down", "downward"):
                pos[1] = round(max(0.1, pos[1] - step * 0.5), 4)
            elif direction in ("center", "centre"):
                pos[0], pos[2] = 0.0, 0.0
            obj["position"] = pos
            changes.append(f"Moved {obj['name']} {direction} to {pos}.")

    for scale_dir, factor in (("larger", 1.3), ("bigger", 1.3), ("scale up", 1.3), ("smaller", 0.75), ("scale down", 0.75)):
        if scale_dir in low:
            hero_targets = _match_objects(scene["objects"], "sphere hero torus cube cylinder") or scene["objects"][-1:]
            for obj in hero_targets[:1]:
                obj["scale"] = [round(s * factor, 4) for s in obj["scale"]]
                changes.append(f"Scaled {obj['name']} to {obj['scale']}.")
            break

    add_match = re.search(r"\badd\s+(?:a|an)\s+([a-z ]{3,35})", low)
    if add_match:
        phrase = add_match.group(1).strip()
        prim = next((p for p in PRIMITIVES if p in phrase), "sphere" if "orb" in phrase else "box")
        preset = "chrome" if "chrome" in phrase else ("clear_glass" if "glass" in phrase else "brushed_metal")
        mat = copy.deepcopy(MATERIAL_PRESETS[preset])
        for w, hc in COLOR_WORDS.items():
            if w in phrase:
                mat["color"] = hc
                break
        nid = f"added_{prim}_{len(scene['objects']) + 1}"
        new_obj = {
            "id": nid, "name": phrase.title()[:50], "role": "prop", "primitive": prim, "visible": True,
            "position": [1.25, 0.55, 0.45], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0],
            "params": {"radius": 0.45} if prim == "sphere" else {"width": 0.8, "height": 0.9, "depth": 0.8},
            "material": mat,
        }
        scene["objects"].append(new_obj)
        changes.append(f"Added {new_obj['name']} ({prim}).")

    for w, hc in COLOR_WORDS.items():
        if re.search(rf"\b(?:color|tint|paint|make)\b.*\b{w}\b", low):
            targets = _match_objects(scene["objects"], low) or scene["objects"][-1:]
            for o in targets[:2]:
                o["material"]["color"] = hc
            changes.append(f"Updated material tint to {w} ({hc}).")
            break

    if any(k in low for k in ("warm light", "warmer", "golden hour", "sunset")):
        for lgt in scene["lights"]:
            if lgt["type"] in ("directional", "point", "spot"):
                lgt["color"] = "#ffd8a8"
        changes.append("Shifted studio lighting temperature warmer.")
    elif any(k in low for k in ("cool light", "cooler", "icy", "moonlight")):
        for lgt in scene["lights"]:
            lgt["color"] = "#b8dcff"
        changes.append("Shifted studio lighting temperature cooler.")
    if "brighter" in low:
        for lgt in scene["lights"]:
            lgt["intensity"] = round(min(10.0, lgt["intensity"] * 1.25), 3)
        changes.append("Increased studio light intensity by 25%.")
    elif "dimmer" in low or "darker" in low or "moody" in low:
        for lgt in scene["lights"]:
            lgt["intensity"] = round(max(0.15, lgt["intensity"] * 0.72), 3)
        changes.append("Softened studio light intensity for a moodier atmosphere.")

    if any(k in low for k in ("camera", "angle", "view", "zoom", "closer", "wider", "reframe")):
        cam = scene["camera"]
        cp = list(cam["position"])
        ct = list(cam["target"])
        fov = float(cam.get("fov", 38.0))
        heroes = [o for o in scene["objects"] if o.get("role") == "hero" or "sphere" in o["id"]]
        focus_x = heroes[0]["position"][0] * 0.45 if heroes else ct[0]
        if "closer" in low or "zoom in" in low:
            cp = [round(cp[0] * 0.74, 4), round(max(1.1, cp[1] * 0.85), 4), round(cp[2] * 0.74, 4)]
            fov = max(28.0, fov - 3.0)
        elif "wider" in low or "zoom out" in low:
            cp = [round(cp[0] * 1.25, 4), round(cp[1] * 1.15, 4), round(cp[2] * 1.25, 4)]
            fov = min(52.0, fov + 5.0)
        elif "top" in low or "overhead" in low:
            cp = [round(focus_x, 4), 5.6, 2.2]
            ct = [round(focus_x, 4), 0.5, -0.2]
        elif "low angle" in low:
            cp = [round(focus_x + 2.4, 4), 0.92, 3.6]
            ct = [round(focus_x, 4), 1.15, -0.3]
        else:
            new_x = -2.15 if cp[0] >= 0 else 2.45
            cp = [round(new_x, 4), round(max(1.45, cp[1] - 0.35), 4), round(max(3.4, cp[2] - 0.55), 4)]
            ct = [round(focus_x, 4), 0.98, -0.25]
            fov = 35.0
        cam["position"], cam["target"], cam["fov"] = cp, ct, round(fov, 2)
        changes.append(f"Adjusted camera to position {cp}, target {ct}, FOV {cam['fov']} deg.")

    if not changes:
        cam = scene["camera"]
        cam["position"] = [round(cam["position"][0] * 0.92, 4), cam["position"][1], round(cam["position"][2] * 0.95, 4)]
        changes.append("Applied subtle studio composition refinement.")

    return normalize_scene(scene, scene.get("title") or "Edited Scene"), changes


def _derive_title(prompt: str, fallback: str) -> str:
    cleaned = re.sub(r"^(create|build|make|generate|design)\s+(a|an|the)?\s*", "", prompt.strip(), flags=re.I)
    cleaned = cleaned.split(",")[0].split(".")[0].strip()
    if not cleaned:
        return fallback
    return (cleaned[:1].upper() + cleaned[1:])[:72]


SCENE_LLM_SYSTEM = (
    "You are the NOVA 3D Studio procedural scene compiler. Return ONLY valid JSON (no markdown fences) matching:\n"
    '{"title":"...", "environment":{"background":"#10151c","exposure":1.1,"grid":false}, '
    '"camera":{"position":[x,y,z],"target":[x,y,z],"fov":38}, '
    '"lights":[{"id":"...","name":"...","type":"directional|point|hemisphere","color":"#ffffff","intensity":2.0,"position":[x,y,z],"cast_shadow":true}], '
    '"objects":[{"id":"...","name":"...","role":"floor|wall|glass|hero|prop","primitive":"box|sphere|cylinder|cone|plane|torus","position":[x,y,z],"rotation":[rx,ry,rz],"scale":[1,1,1],"params":{"width":1,"height":1,"depth":1,"radius":0.5},"material":{"name":"...","preset":"frosted_glass|clear_glass|polished_concrete|chrome|brushed_metal|matte_plaster|emissive_soft","color":"#dceeff","metalness":0.0,"roughness":0.3,"opacity":1.0,"transmission":0.0,"ior":1.5,"emissive":"#000000","emissive_intensity":0.0}}]}'
)


async def maybe_llm_scene(router, prompt: str, current_scene: dict | None = None) -> tuple[dict | None, str]:
    if router is None:
        return None, ""
    user_msg = prompt if current_scene is None else (
        f"Current scene JSON:\n{json.dumps(current_scene, separators=(',', ':'))}\n\nApply edit instruction:\n{prompt}"
    )
    try:
        res = await router.complete(
            "vellur-coder",
            [{"role": "system", "content": SCENE_LLM_SYSTEM}, {"role": "user", "content": user_msg}],
            max_tokens=1600,
            tag="studio3d",
            mode="auto",
        )
        text = str(res.get("content") or "").strip()
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None, ""
        parsed = json.loads(m.group(0))
        if isinstance(parsed, dict) and isinstance(parsed.get("objects"), list) and parsed["objects"]:
            return normalize_scene(parsed, (current_scene or {}).get("title") or "AI Scene"), f"Synthesized with {res.get('target', 'vellur-coder')}."
    except Exception:
        return None, ""
    return None, ""


class CreateSceneBody(BaseModel):
    title: Optional[str] = None
    description: str = ""
    prompt: Optional[str] = None
    preset: Optional[str] = None
    scene: Optional[dict] = None
    task_id: Optional[int] = None
    use_ai: bool = False


class SaveSceneBody(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    scene: dict
    note: str = "Saved from 3D Studio inspector"


class PromptMutateBody(BaseModel):
    prompt: str
    use_ai: bool = False


class SnapshotBody(BaseModel):
    data_url: Optional[str] = None
    note: str = ""


class ExportTaskBody(BaseModel):
    task_id: int
    filename: Optional[str] = None


class Studio3DStore:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.db = ctrl.db
        self.snap_dir = os.path.join(ctrl.env.data_dir, "studio3d_snapshots")
        os.makedirs(self.snap_dir, exist_ok=True)

    def _summary(self, row: dict) -> dict:
        sc = json.loads(row.get("scene_json") or "{}")
        hist = json.loads(row.get("history_json") or "[]")
        snaps = self.db.all("SELECT id,width,height,size,note,created FROM studio_snapshots WHERE scene_id=? ORDER BY created DESC LIMIT 6", (row["id"],))
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "version": row["version"],
            "task_id": row["task_id"],
            "object_count": len(sc.get("objects") or []),
            "light_count": len(sc.get("lights") or []),
            "prompt_count": len(hist),
            "last_prompt": hist[-1].get("prompt", "") if hist else "",
            "latest_snapshot": (f"/autopilot/api/studio3d/snapshots/{snaps[0]['id']}" if snaps else None),
            "export_glb_url": f"/autopilot/api/studio3d/scenes/{row['id']}/export.glb",
            "created": row["created"],
            "updated": row["updated"],
        }

    def list_scenes(self, limit: int = 50) -> list[dict]:
        rows = self.db.all("SELECT * FROM studio_scenes ORDER BY updated DESC, id DESC LIMIT ?", (max(1, min(limit, 200)),))
        return [self._summary(r) for r in rows]

    def get_scene(self, scene_id: int) -> Optional[dict]:
        row = self.db.one("SELECT * FROM studio_scenes WHERE id=?", (scene_id,))
        if not row:
            return None
        snaps = self.db.all("SELECT id,mime,width,height,size,sha256,note,created FROM studio_snapshots WHERE scene_id=? ORDER BY created DESC LIMIT 20", (scene_id,))
        for s in snaps:
            s["url"] = f"/autopilot/api/studio3d/snapshots/{s['id']}"
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "version": row["version"],
            "task_id": row["task_id"],
            "scene": normalize_scene(json.loads(row["scene_json"] or "{}"), row["title"]),
            "history": json.loads(row["history_json"] or "[]"),
            "snapshots": snaps,
            "export_glb_url": f"/autopilot/api/studio3d/scenes/{row['id']}/export.glb",
            "created": row["created"],
            "updated": row["updated"],
        }

    def create_scene(self, title: str, scene: dict, description: str = "", history_entry: dict | None = None,
                     task_id: int | None = None) -> dict:
        now = time.time()
        norm = normalize_scene(scene, title)
        final_title = (title or norm.get("title") or "Untitled Scene").strip()[:120]
        norm["title"] = final_title
        hist = [history_entry] if history_entry else []
        cur = self.db.x(
            "INSERT INTO studio_scenes(title,description,scene_json,history_json,version,task_id,created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (final_title, (description or "").strip()[:1000], json.dumps(norm), json.dumps(hist), 1, task_id, now, now),
        )
        sid = cur.lastrowid
        self.db.event("info", "studio3d", f"3D scene {sid} created: {final_title[:70]}")
        return self.get_scene(sid)

    def update_scene(self, scene_id: int, scene: dict, title: str | None = None, description: str | None = None,
                     history_entry: dict | None = None) -> Optional[dict]:
        row = self.db.one("SELECT * FROM studio_scenes WHERE id=?", (scene_id,))
        if not row:
            return None
        now = time.time()
        final_title = (title.strip()[:120] if title and title.strip() else row["title"])
        final_desc = (description.strip()[:1000] if description is not None else row["description"])
        norm = normalize_scene(scene, final_title)
        norm["title"] = final_title
        hist = json.loads(row["history_json"] or "[]")
        if history_entry:
            hist.append(history_entry)
            hist = hist[-60:]
        ver = int(row["version"] or 1) + 1
        self.db.x(
            "UPDATE studio_scenes SET title=?, description=?, scene_json=?, history_json=?, version=?, updated=? WHERE id=?",
            (final_title, final_desc, json.dumps(norm), json.dumps(hist), ver, now, scene_id),
        )
        return self.get_scene(scene_id)

    def delete_scene(self, scene_id: int) -> bool:
        snaps = self.db.all("SELECT id FROM studio_snapshots WHERE scene_id=?", (scene_id,))
        for s in snaps:
            p = os.path.join(self.snap_dir, f"{s['id']}.png")
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.db.x("DELETE FROM studio_snapshots WHERE scene_id=?", (scene_id,))
        cur = self.db.x("DELETE FROM studio_scenes WHERE id=?", (scene_id,))
        return bool(cur.rowcount)

    def save_snapshot(self, scene_id: int, raw_png: bytes, note: str = "") -> Optional[dict]:
        if not self.db.one("SELECT id FROM studio_scenes WHERE id=?", (scene_id,)):
            return None
        if len(raw_png) < 24 or raw_png[:8] != b"\x89PNG\r\n\x1a\n" or raw_png[12:16] != b"IHDR":
            raise ValueError("Invalid PNG image data")
        if len(raw_png) > 10 * 1024 * 1024:
            raise ValueError("Snapshot exceeds 10 MB limit")
        width, height = struct.unpack(">II", raw_png[16:24])
        sid = secrets.token_hex(12)
        sha = hashlib.sha256(raw_png).hexdigest()
        path = os.path.join(self.snap_dir, f"{sid}.png")
        with open(path, "wb") as f:
            f.write(raw_png)
        now = time.time()
        self.db.x(
            "INSERT INTO studio_snapshots(id,scene_id,mime,width,height,size,sha256,note,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, scene_id, "image/png", int(width), int(height), len(raw_png), sha, (note or "").strip()[:240], now),
        )
        return {
            "id": sid, "scene_id": scene_id, "mime": "image/png", "width": int(width), "height": int(height),
            "size": len(raw_png), "sha256": sha, "note": (note or "").strip()[:240],
            "url": f"/autopilot/api/studio3d/snapshots/{sid}", "created": now,
        }


def register(app: FastAPI, ctrl, router, agent, admin, admin_write) -> Studio3DStore:
    store = Studio3DStore(ctrl)
    app.state.studio3d = store

    def bad(msg: str, status: int = 400, code: str = "invalid") -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": code, "message": msg})

    @app.get("/autopilot/api/studio3d/scenes", dependencies=[Depends(admin)])
    async def list_scenes():
        return {
            "scenes": store.list_scenes(),
            "presets": {k: {"name": v["name"], "color": v["color"], "metalness": v["metalness"],
                            "roughness": v["roughness"], "opacity": v["opacity"], "transmission": v["transmission"]}
                        for k, v in MATERIAL_PRESETS.items()},
            "primitives": list(PRIMITIVES),
        }

    @app.post("/autopilot/api/studio3d/scenes", dependencies=[Depends(admin_write)])
    async def create_scene(body: CreateSceneBody):
        now = time.time()
        prompt = (body.prompt or "").strip()
        if prompt:
            if len(prompt) > 4000:
                return bad("Prompt must be under 4000 characters.")
            ai_scene, ai_note = (await maybe_llm_scene(router, prompt)) if body.use_ai else (None, "")
            if ai_scene:
                sc, summary, mode = ai_scene, ai_note, "ai+procedural"
            else:
                sc, summary = generate_scene_from_prompt(prompt)
                mode = "procedural"
            title = (body.title or sc.get("title") or "Generated Scene").strip()
            entry = {"ts": now, "kind": "prompt_create", "mode": mode, "prompt": prompt, "summary": summary}
            return store.create_scene(title, sc, body.description or prompt[:240], entry, body.task_id)
        if body.preset == "futuristic_room":
            sc = futuristic_room_template(body.title or "Minimalist Futuristic Room")
            entry = {"ts": now, "kind": "preset", "mode": "preset", "prompt": "Preset: Minimalist Futuristic Room",
                     "summary": "Loaded architectural room preset."}
            return store.create_scene(sc["title"], sc, body.description, entry, body.task_id)
        sc = normalize_scene(body.scene or futuristic_room_template(body.title or "New Studio Scene"),
                             body.title or "New Studio Scene")
        entry = {"ts": now, "kind": "create", "mode": "manual", "prompt": "", "summary": "Initialized 3D scene."}
        return store.create_scene(body.title or sc["title"], sc, body.description, entry, body.task_id)

    @app.get("/autopilot/api/studio3d/scenes/{scene_id}", dependencies=[Depends(admin)])
    async def get_scene(scene_id: int):
        got = store.get_scene(scene_id)
        return got if got else bad("Scene not found.", 404, "not_found")

    @app.put("/autopilot/api/studio3d/scenes/{scene_id}", dependencies=[Depends(admin_write)])
    async def save_scene(scene_id: int, body: SaveSceneBody):
        entry = {"ts": time.time(), "kind": "inspector_save", "mode": "direct", "prompt": "", "summary": (body.note or "Updated scene graph")[:240]}
        got = store.update_scene(scene_id, body.scene, title=body.title, description=body.description, history_entry=entry)
        return got if got else bad("Scene not found.", 404, "not_found")

    @app.post("/autopilot/api/studio3d/scenes/{scene_id}/prompt", dependencies=[Depends(admin_write)])
    async def prompt_mutate(scene_id: int, body: PromptMutateBody):
        cur = store.get_scene(scene_id)
        if not cur:
            return bad("Scene not found.", 404, "not_found")
        prompt = (body.prompt or "").strip()
        if not 2 <= len(prompt) <= 4000:
            return bad("Prompt must be between 2 and 4000 characters.")
        ai_scene, ai_note = (await maybe_llm_scene(router, prompt, cur["scene"])) if body.use_ai else (None, "")
        if ai_scene:
            updated_sc, summary, mode = ai_scene, ai_note, "ai+procedural"
        else:
            updated_sc, changes = mutate_scene_from_prompt(cur["scene"], prompt)
            summary, mode = " ".join(changes), "procedural"
        entry = {"ts": time.time(), "kind": "prompt_edit", "mode": mode, "prompt": prompt, "summary": summary}
        out = store.update_scene(scene_id, updated_sc, history_entry=entry)
        ctrl.db.event("info", "studio3d", f"3D scene {scene_id} edited via prompt ({mode}): {prompt[:70]}")
        return out

    @app.delete("/autopilot/api/studio3d/scenes/{scene_id}", dependencies=[Depends(admin_write)])
    async def remove_scene(scene_id: int):
        if not store.delete_scene(scene_id):
            return bad("Scene not found.", 404, "not_found")
        return {"ok": True, "deleted": scene_id}

    @app.get("/autopilot/api/studio3d/scenes/{scene_id}/export.glb", dependencies=[Depends(admin)])
    async def export_glb(scene_id: int):
        cur = store.get_scene(scene_id)
        if not cur:
            return bad("Scene not found.", 404, "not_found")
        raw = scene_to_glb(cur["scene"])
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", cur["title"].lower()).strip("-") or f"scene-{scene_id}"
        return Response(
            content=raw,
            media_type="model/gltf-binary",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}-v{cur["version"]}.glb"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/autopilot/api/studio3d/scenes/{scene_id}/snapshots", dependencies=[Depends(admin_write)])
    async def create_snapshot(scene_id: int, body: SnapshotBody):
        cur = store.get_scene(scene_id)
        if not cur:
            return bad("Scene not found.", 404, "not_found")
        if body.data_url:
            s = body.data_url.strip()
            prefix = "data:image/png;base64,"
            if not s.startswith(prefix):
                return bad("Expected a data:image/png;base64 payload.")
            try:
                raw_png = base64.b64decode(s[len(prefix):], validate=True)
            except Exception:
                return bad("Malformed base64 PNG data.")
        else:
            raw_png = render_scene_preview_png(cur["scene"])
        try:
            snap = store.save_snapshot(scene_id, raw_png, body.note or f"v{cur['version']} preview")
        except ValueError as e:
            return bad(str(e))
        return snap

    @app.get("/autopilot/api/studio3d/snapshots/{snap_id}", dependencies=[Depends(admin)])
    async def serve_snapshot(snap_id: str):
        if not re.fullmatch(r"[0-9a-f]{24}", snap_id or ""):
            return bad("Snapshot not found.", 404, "not_found")
        row = ctrl.db.one("SELECT * FROM studio_snapshots WHERE id=?", (snap_id,))
        path = os.path.join(store.snap_dir, f"{snap_id}.png")
        if not row or not os.path.isfile(path):
            return bad("Snapshot not found.", 404, "not_found")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    return store
