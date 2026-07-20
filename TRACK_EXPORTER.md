# VectorG Track Exporter

The track exporter creates a ZIP containing:

```text
<track_id>.glb
manifest.json
hdr/env.hdr or hdr/env.exr
maps/<layout_id>.svg
routes/<layout_id>.json
```

Install or enable `blender/addons/vectorg_track_exporter` the same way as the
car exporter. The panel is under `View3D > Sidebar > VectorG`.

The optional **HDR** field selects an `.hdr` or `.exr` image texture already
loaded in the Blender file. Packed image textures are supported.

## Workflow

1. Select **Create Track Structure**.
2. Set the track ID and name.
3. Add one or more layouts.
4. Draw or assign an optional Bezier or Poly map curve for each layout.
5. Move visual objects under the generated visual roots.
6. Parent driving collision meshes under the appropriate generated surface.
7. Parent walls, barriers, fences, and props under `OBSTACLES`.
8. Add spawn points, one start/finish volume, and ordered checkpoints.
9. Position and rotate the generated objects in the viewport. Local `-Y` is
   the forward crossing direction.
10. Select **Validate Track**, then **Export Track Zip**.

Picking a layout map curve moves it under the generated `<layout_id>_MAP` node
while preserving its world transform. The exporter calculates layout length from
that curve, projects it onto world XY for `maps/<layout_id>.svg`, and adaptively
samples its full 3D shape for `routes/<layout_id>.json`. Route samples have a
maximum spacing of 5 metres and become denser around corners and elevation
changes. SVG maps automatically rotate their principal axis horizontally unless
the layout is nearly square. Draw the curve in driving direction. Circular layouts require one cyclic
spline; point-to-point layouts require one open spline. MAP hierarchies are
excluded from the GLB. Layouts without a map curve remain valid and use the
game's placeholder map and spawn-point reset fallback.

Changing a layout's **ID** renames its generated hierarchy nodes, addon-created
spawn points, route events, and layout box colliders. Use the refresh icon next
to the ID to normalize copied objects and their names; it also assigns checkpoint
order from their order in the `EVENTS` hierarchy. Changing its display **Name**
only changes player-facing metadata.

Choose **Route Type** per layout. Circular routes use one `start_finish` event;
point-to-point routes use separate `start` and `finish` events.

Collision roots contain `tarmac`, `concrete`, `curb`, `grass`, `gravel`,
`dirt`, `mud`, `sand`, `snow`, `ice`, and `OBSTACLES` as direct children.

All meshes under a drivable surface group are colliders for that surface.
`OBSTACLES` may contain any organizational hierarchy. Obstacle meshes do not
define a driving surface.
Use **Create Static Box Collider** in the Shared or Layout section to create a
cube Empty under that scope's `OBSTACLES` root. With meshes selected, it matches
their combined world-space bounds; with no meshes selected, it creates a unit
box at the 3D cursor.

With exactly one visual mesh selected, use **Create Dynamic Box Collider** to
create a box matching its bounds under the same scope's `OBSTACLES` root. The
exporter keeps a Blender object pointer to the visual mesh and writes
`vectorg_body = "dynamic"`, `vectorg_target`, `vectorg_mass`, and
`vectorg_shape = "box"` into glTF extras. Mass is calculated from the generated
box volume using a density of `10 kg/m³`, with a minimum mass of `1 kg`.

Collision meshes without a `vectorg_shape` property are treated as trimesh
colliders.

Event sensors and box colliders use their exported node transforms. Their world
scale is interpreted as box half-extents, so scale `(5, 1, 2)` produces a box
with size `(10, 2, 4)`. Cube Empties must keep display size `1`; display size is
not exported. The addon locks display size to `1` on newly created cube Empties;
use object scale to change their dimensions.
The exporter writes `vectorg_surface`, `vectorg_shape`, event type, checkpoint
order, and hierarchy roles into glTF extras. Collision meshes are still present
in the GLB and must be hidden by the runtime after physics creation.

Removing a layout from the addon only removes its configuration entry. It does
not delete Blender objects.
