# VectorG Blender Exporters

This folder contains the car exporter and track exporter. Track exporter usage
is documented in [TRACK_EXPORTER.md](TRACK_EXPORTER.md).

## Car Exporter

VectorG Car Exporter creates vehicle packages for the VectorG driving simulator.
Install the `vectorg_car_exporter` folder as a Blender add-on, then open
`View3D > Sidebar > VectorG`.

The addon exports a zip with:

```text
<car_id>.glb
manifest.json
sounds/
```

The package matches the game loader convention:

```text
src/files/models/vehicles/<car_id>/<car_id>.glb
src/files/models/vehicles/<car_id>/manifest.json
src/files/models/vehicles/<car_id>/sounds/
```

`manifest.json` identifies the exported model explicitly:

```json
{
  "id": "<car_id>",
  "model": "<car_id>.glb"
}
```

## Scripts Path Installation

In Blender, open `Edit > Preferences > File Paths` and add this Scripts path:

```text
/Users/firatkiral/Repo/vectorg/blender
```

Blender loads add-ons from the repository's `addons/` directory. This add-on is
located at:

```text
/Users/firatkiral/Repo/vectorg/blender/addons/vectorg_car_exporter
```

Restart Blender, open `Edit > Preferences > Add-ons`, and enable:

```text
VectorG Car Exporter
```

## Zip Installation

To create an installable archive, run from the repository root:

```bash
cd addons && zip -r ../vectorg_car_exporter.zip vectorg_car_exporter
```

Then use Blender's `Install from Disk` action and select
`vectorg_car_exporter.zip`.

## Required Scene Objects

Default object names are based on `src/files/models/vehicles/byakko_gtr/manifest.json`:

```text
body
body_collider
centerOfMass
steering_wheel

suspension_fl
suspension_fr
suspension_rl
suspension_rr

wheel_fl
wheel_fr
wheel_rl
wheel_rr

chase_cam
cockpit_cam
hood_cam
roof_cam
```

Wheel objects should be direct children of their suspension objects.

## Direction Rules

The addon validates these conventions:

```text
car local -Y = forward
car local +X = left
left wheels are on car +X
right wheels are on car -X
wheel local +X aligns with car left/right axle
steering wheel local -Y faces car forward
steering wheel local +Z aligns with car up
```

Orientation failures are warnings because some source models may need artist-side correction or intentional overrides.

## Persistent Config

All editable values are stored in `Scene.car_exporter`, so values are saved inside the `.blend` file and restored when the UI is reopened.

## Audio

Audio uses fixed logical slots instead of free-form files:

```text
Transmission On
Transmission Off
On Low / Mid / High
Off Low / Mid / High
Limiter
Turbo Flutter
```

Assigned files are copied into `sounds/`. Audio is required by default because the runtime applies fixed engine sample keys every frame. Disable `Require Audio Slots` only when intentionally exporting a visual/physics-only test package.

Only assigned files are written to `manifest.json`; the addon does not emit references to files that are not packaged.

## Export

Use `Validate Car` first, then `Export Car Zip`.

The GLB export uses Blender's built-in glTF exporter with:

```text
export_format="GLB"
use_selection=False
export_apply=True
```
