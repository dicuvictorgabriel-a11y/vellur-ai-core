"""Headless Blender exporter for VELLUR 3D Studio (optional).

The agent runner calls this only when a real `blender` binary is available (PATH or VELLUR_BLENDER):

    blender --background --factory-startup scene.blend --python blender_export.py -- /tmp/scene.glb

It exports the scene to GLB so the browser viewer can show it. Nothing is installed automatically and the process runs
with a timeout and an output-size limit set by the runner. This file must stay compatible with Blender 3.x and 4.x.
"""
import os
import sys


def _args():
    if "--" not in sys.argv:
        raise SystemExit("usage: blender --background scene.blend --python blender_export.py -- output.glb")
    rest = sys.argv[sys.argv.index("--") + 1:]
    if not rest:
        raise SystemExit("missing output path")
    return os.path.abspath(rest[0])


def main():
    import bpy  # imported only inside Blender

    out = _args()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    scene = bpy.context.scene

    # A predictable, neutral starting point. The .blend keeps its own materials and cameras are omitted.
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new("VELLUR Preview")
    scene.world.use_nodes = True

    options = dict(
        filepath=out,
        export_format="GLB",
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials="EXPORT",
        export_cameras=False,
        export_lights=False,
        export_texcoords=True,
        export_normals=True,
        export_animations=True,
        export_anim_single_armature=True,
        export_skins=True,
        export_morph=True,
        export_extras=False,
    )
    try:
        bpy.ops.export_scene.gltf(**options)
    except TypeError:
        # Older/newer Blender releases rename some keyword arguments; fall back to the stable minimum.
        bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=False)

    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise SystemExit("Blender did not write a GLB file")
    print(f"[vellur] exported {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()
