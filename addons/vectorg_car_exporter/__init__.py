bl_info = {
    "name": "VectorG Car Exporter",
    "author": "VectorG",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > VectorG",
    "description": "Export VectorG vehicle packages as model.glb + config.json + audio zip",
    "category": "Import-Export",
}

import json
import math
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.app.handlers import persistent
from mathutils import Vector
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup


WHEEL_KEYS = (
    ("front", "l", True),
    ("front", "r", True),
    ("rear", "l", False),
    ("rear", "r", False),
)

WHEEL_LABELS = {
    ("front", "l"): "Front Left Wheel",
    ("front", "r"): "Front Right Wheel",
    ("rear", "l"): "Rear Left Wheel",
    ("rear", "r"): "Rear Right Wheel",
}

SOUND_SLOTS = {
    "tranny_on": {"label": "Transmission On", "default": "trany_power_high.wav", "rpm": 0, "loop": True, "volume": 0.6},
    "tranny_off": {"label": "Transmission Off", "default": "tw_offlow_4.wav", "rpm": 0, "loop": True, "volume": 0.1},
    "on_high": {"label": "On High", "default": "BAC_Mono_onhigh.wav", "rpm": 1000, "loop": True, "volume": 0.5},
    "on_mid": {"label": "On Mid", "default": "BAC_Mono_onmid.wav", "rpm": 1000, "loop": True, "volume": 0.45},
    "on_low": {"label": "On Low", "default": "BAC_Mono_onlow.wav", "rpm": 1000, "loop": True, "volume": 0.4},
    "off_high": {"label": "Off High", "default": "BAC_Mono_offveryhigh.wav", "rpm": 1000, "loop": True, "volume": 0.3},
    "off_mid": {"label": "Off Mid", "default": "BAC_Mono_offmid.wav", "rpm": 1000, "loop": True, "volume": 0.35},
    "off_low": {"label": "Off Low", "default": "BAC_Mono_offlow.wav", "rpm": 1000, "loop": True, "volume": 0.3},
    "limiter": {"label": "Limiter", "default": "limiter.wav", "rpm": 8000, "loop": True, "volume": 0.4},
    "turbo_flutter": {"label": "Turbo Flutter", "default": "turbo_flutter.wav", "rpm": 8000, "loop": False, "volume": 0.6},
}

ORIENTATION_DOT_THRESHOLD = math.cos(math.radians(1.0))
STEERING_WHEEL_DOT_THRESHOLD = math.cos(math.radians(45.0))
TORQUE_CURVE_NODE_GROUP = "_CarExporterTorqueCurve"
TORQUE_CURVE_NODE = "Torque Curve"
CAMERA_PREFIXES = ("chase", "cockpit", "hood", "roof")
GUIDE_PREFIX = "CAR_EXPORTER_GUIDE_"
GUIDE_PROP = "car_exporter_helper"
AXIS_ITEMS = (
    ("x", "X", ""),
    ("y", "Y", ""),
    ("z", "Z", ""),
    ("-x", "-X", ""),
    ("-y", "-Y", ""),
    ("-z", "-Z", ""),
)
BLENDER_AXIS_TO_GAME = {
    "x": [1, 0, 0],
    "-x": [-1, 0, 0],
    "y": [0, 0, 1],
    "-y": [0, 0, -1],
    "z": [0, 1, 0],
    "-z": [0, -1, 0],
}
GAME_AXIS_TO_BLENDER = {tuple(value): key for key, value in BLENDER_AXIS_TO_GAME.items()}
BLENDER_AXIS_LOCAL = {
    "x": (1, 0, 0),
    "-x": (-1, 0, 0),
    "y": (0, 1, 0),
    "-y": (0, -1, 0),
    "z": (0, 0, 1),
    "-z": (0, 0, -1),
}

def scene_settings(context):
    return context.scene.car_exporter


def find_object(name):
    return bpy.data.objects.get(name) if name else None


def object_config_name(obj):
    return obj.name if obj else ""


def set_object_pointer(data, prop_name, object_name):
    setattr(data, prop_name, find_object(object_name))


def object_axis(obj, local_axis):
    return (obj.matrix_world.to_quaternion() @ Vector(local_axis)).normalized()


def dot_axis(obj, local_axis, world_axis):
    return object_axis(obj, local_axis).dot(Vector(world_axis).normalized())


def abspath(path):
    return bpy.path.abspath(path) if path else ""


def relative_to_car(car_obj, obj):
    if not car_obj or not obj:
        return None
    return car_obj.matrix_world.inverted() @ obj.matrix_world.translation


def object_world_bounds_size(obj):
    if not obj:
        return None
    if obj.type == "MESH" and obj.data and obj.data.vertices:
        points = [vertex.co for vertex in obj.data.vertices]
    else:
        points = [Vector(corner) for corner in obj.bound_box]
    min_corner = Vector((
        min(point.x for point in points),
        min(point.y for point in points),
        min(point.z for point in points),
    ))
    max_corner = Vector((
        max(point.x for point in points),
        max(point.y for point in points),
        max(point.z for point in points),
    ))
    local_size = max_corner - min_corner
    scale = obj.matrix_world.to_scale()
    return Vector((
        abs(local_size.x * scale.x),
        abs(local_size.y * scale.y),
        abs(local_size.z * scale.z),
    ))


def default_collider_mass(obj):
    size = object_world_bounds_size(obj)
    if not size:
        return 0.0
    volume = max(size.x, 0.01) * max(size.y, 0.01) * max(size.z, 0.01)
    return round(max(1.0, volume * 150.0), 2)


def update_collider_object(self, _context):
    if self.object_ref:
        self.mass = default_collider_mass(self.object_ref)
    else:
        self.collider_type = "trimesh"
        self.mass = 0.0


def is_object_in_tree(root_obj, obj):
    if not root_obj or not obj:
        return False
    current = obj
    while current:
        if current == root_obj:
            return True
        current = current.parent
    return False


def validate_object_in_car_tree(errors, car_obj, label, obj):
    if not car_obj:
        return
    if obj and obj != car_obj and not is_object_in_tree(car_obj, obj):
        errors.append(f"{label} must be inside car root hierarchy")


def camera_object_poll(_self, obj):
    return obj.type == "CAMERA"


def camera_fov(settings, prefix):
    camera_obj = getattr(settings, f"{prefix}_camera_object")
    if camera_obj and camera_obj.type == "CAMERA" and camera_obj.data:
        return math.degrees(camera_obj.data.angle)
    return getattr(settings, f"{prefix}_fov")


def camera_target_name(camera_obj):
    return f"{camera_obj.name}_target"


def camera_target_child(camera_obj):
    if not camera_obj:
        return None
    target_name = camera_target_name(camera_obj)
    for child in camera_obj.children:
        if child.type == "EMPTY" and child.name == target_name:
            return child
    return None


def position_camera_target(settings, prefix, target_obj):
    distance = getattr(settings, f"{prefix}_target_distance")
    target_obj.location = (0.0, 0.0, -distance)
    target_obj.rotation_euler = (0.0, 0.0, 0.0)
    target_obj.scale = (1.0, 1.0, 1.0)


def create_camera_target_on_selection(settings, prefix):
    camera_obj = getattr(settings, f"{prefix}_camera_object")
    if not camera_obj or camera_obj.type != "CAMERA":
        return

    target_obj = camera_target_child(camera_obj)
    if target_obj is None:
        target_obj = bpy.data.objects.new(camera_target_name(camera_obj), None)
        target_obj.empty_display_type = "PLAIN_AXES"
        target_obj.empty_display_size = 0.25
        link_collection = camera_obj.users_collection[0] if camera_obj.users_collection else bpy.context.scene.collection
        link_collection.objects.link(target_obj)
        target_obj.parent = camera_obj
        target_obj.matrix_parent_inverse.identity()

    position_camera_target(settings, prefix, target_obj)


def update_existing_camera_target(settings, prefix):
    camera_obj = getattr(settings, f"{prefix}_camera_object")
    target_obj = camera_target_child(camera_obj)
    if target_obj:
        position_camera_target(settings, prefix, target_obj)


def update_chase_camera_object(settings, _context):
    create_camera_target_on_selection(settings, "chase")


def update_cockpit_camera_object(settings, _context):
    create_camera_target_on_selection(settings, "cockpit")


def update_hood_camera_object(settings, _context):
    create_camera_target_on_selection(settings, "hood")


def update_roof_camera_object(settings, _context):
    create_camera_target_on_selection(settings, "roof")


def update_chase_target_distance(settings, _context):
    update_existing_camera_target(settings, "chase")


def update_cockpit_target_distance(settings, _context):
    update_existing_camera_target(settings, "cockpit")


def update_hood_target_distance(settings, _context):
    update_existing_camera_target(settings, "hood")


def update_roof_target_distance(settings, _context):
    update_existing_camera_target(settings, "roof")


def ensure_camera_targets(settings):
    for prefix in CAMERA_PREFIXES:
        create_camera_target_on_selection(settings, prefix)


def guide_objects():
    return [
        obj
        for obj in bpy.data.objects
        if obj.get(GUIDE_PROP) or obj.name.startswith(GUIDE_PREFIX)
    ]


def remove_size_guide():
    for obj in guide_objects():
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)


def guide_material(name, color):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def create_guide_curve(name, splines, material, bevel_depth=0.015):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = bevel_depth
    curve.bevel_resolution = 2
    for points in splines:
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for point, co in zip(spline.points, points):
            point.co = (co[0], co[1], co[2], 1.0)

    obj = bpy.data.objects.new(name, curve)
    obj[GUIDE_PROP] = True
    obj.show_in_front = True
    obj.hide_render = True
    obj.data.materials.append(material)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def create_size_guide(settings):
    remove_size_guide()
    line_material = guide_material(f"{GUIDE_PREFIX}Lines", (0.0, 0.85, 1.0, 1.0))

    length = settings.guide_length
    width = settings.guide_width
    wheelbase = settings.guide_wheelbase
    track_width = settings.guide_track_width
    z = 0.02
    half_l = length * 0.5
    half_w = width * 0.5
    front_y = -half_l
    rear_y = half_l
    front_axle_y = -wheelbase * 0.5
    rear_axle_y = wheelbase * 0.5
    half_track = track_width * 0.5
    wheel_size = 0.35

    splines = [
        [(-half_w, front_y, z), (half_w, front_y, z), (half_w, rear_y, z), (-half_w, rear_y, z), (-half_w, front_y, z)],
        [(0.0, front_y - 0.45, z), (-0.35, front_y, z), (0.35, front_y, z), (0.0, front_y - 0.45, z)],
        [(0.0, front_y, z), (0.0, rear_y, z)],
    ]

    for _name, x, y in (
        ("Wheel_FL", half_track, front_axle_y),
        ("Wheel_FR", -half_track, front_axle_y),
        ("Wheel_RL", half_track, rear_axle_y),
        ("Wheel_RR", -half_track, rear_axle_y),
    ):
        half = wheel_size * 0.5
        splines.append([
            (x - half, y - half, z),
            (x + half, y - half, z),
            (x + half, y + half, z),
            (x - half, y + half, z),
            (x - half, y - half, z),
        ])

    create_guide_curve(
        f"{GUIDE_PREFIX}SizeGuide",
        splines,
        line_material,
    )


def with_helpers_unlinked(callback):
    helpers = guide_objects()
    states = [(obj, list(obj.users_collection)) for obj in helpers]
    try:
        for obj, collections in states:
            for collection in collections:
                collection.objects.unlink(obj)
        return callback()
    finally:
        for obj, collections in states:
            if obj.name not in bpy.data.objects:
                continue
            for collection in collections:
                if obj.name not in collection.objects.keys():
                    collection.objects.link(obj)


def validate_scene(settings):
    errors = []
    warnings = []

    if not settings.is_configured:
        errors.append("Create configuration first")
        return errors, warnings

    car_obj = settings.car_root_object
    required = [
        ("car root", settings.car_root_object),
        ("center of mass", settings.center_of_mass_object),
        ("steering wheel", settings.steering_wheel_object),
        ("chase camera", settings.chase_camera_object),
        ("cockpit camera", settings.cockpit_camera_object),
        ("hood camera", settings.hood_camera_object),
        ("roof camera", settings.roof_camera_object),
    ]

    for label, obj in required:
        if not obj:
            errors.append(f"Missing {label} object")

    if car_obj:
        for label, obj in required[1:]:
            validate_object_in_car_tree(errors, car_obj, label, obj)

    for label, prefix in (
        ("Chase camera", "chase"),
        ("Cockpit camera", "cockpit"),
        ("Hood camera", "hood"),
        ("Roof camera", "roof"),
    ):
        camera_obj = getattr(settings, f"{prefix}_camera_object")
        if not camera_obj:
            continue
        if camera_obj.type != "CAMERA":
            errors.append(f"{label} must be a Camera object")
            continue
        target_obj = camera_target_child(camera_obj)
        if target_obj and target_obj.parent != camera_obj:
            errors.append(f"{label} target must be a camera child")

    if len(settings.colliders) == 0:
        errors.append("At least one collider is required")

    collider_names = set()
    for index, collider in enumerate(settings.colliders, start=1):
        collider_name = object_config_name(collider.object_ref)
        if not collider.object_ref:
            errors.append(f"Collider {index} object is required")
            continue
        if collider_name in collider_names:
            warnings.append(f"Collider object is used more than once: {collider_name}")
        collider_names.add(collider_name)
        validate_object_in_car_tree(errors, car_obj, f"Collider {index}", collider.object_ref)

    ensure_default_wheels(settings)

    wheel_positions = {}
    for index, wheel in enumerate(settings.wheels, start=1):
        mount_obj = wheel.suspension_ref
        joint_obj = wheel.hub_ref
        wheel_obj = wheel.wheel_ref
        if not mount_obj:
            errors.append(f"Wheel {index} mount object is required")
        if not joint_obj:
            errors.append(f"Wheel {index} joint object is required")
        if not wheel_obj:
            errors.append(f"Wheel {index} spin object is required")
        validate_object_in_car_tree(errors, car_obj, f"Wheel {index} mount", mount_obj)
        validate_object_in_car_tree(errors, car_obj, f"Wheel {index} joint", joint_obj)
        validate_object_in_car_tree(errors, car_obj, f"Wheel {index} spin", wheel_obj)
        if mount_obj and joint_obj and not is_object_in_tree(mount_obj, joint_obj):
            errors.append(f"Wheel {index} joint must be inside mount hierarchy")
        if joint_obj and wheel_obj and not is_object_in_tree(joint_obj, wheel_obj):
            errors.append(f"Wheel {index} spin must be inside joint hierarchy")
        if wheel_obj:
            wheel_positions[(wheel.group, wheel.key)] = wheel_obj.matrix_world.translation.copy()
            up_alignment = object_axis(wheel_obj, BLENDER_AXIS_LOCAL[wheel.up_local_axis]).dot(Vector((0, 0, 1)))
            if up_alignment < ORIENTATION_DOT_THRESHOLD:
                warnings.append(f"{object_config_name(wheel_obj)} configured up axis should point world +Z")
            axle_alignment = abs(object_axis(wheel_obj, BLENDER_AXIS_LOCAL[wheel.spin_local_axis]).dot(Vector((1, 0, 0))))
            if axle_alignment < ORIENTATION_DOT_THRESHOLD:
                warnings.append(f"{object_config_name(wheel_obj)} configured spin axis should align with world X left/right")

    for group in ("front", "rear"):
        left_pos = wheel_positions.get((group, "l"))
        right_pos = wheel_positions.get((group, "r"))
        if left_pos is not None and right_pos is not None and left_pos.x <= right_pos.x:
            warnings.append(f"{group.title()} left wheel should be on world +X side of right wheel")

    for key in ("l", "r"):
        front_pos = wheel_positions.get(("front", key))
        rear_pos = wheel_positions.get(("rear", key))
        if front_pos is not None and rear_pos is not None and front_pos.y >= rear_pos.y:
            warnings.append(f"Front {key.upper()} wheel should be forward of rear {key.upper()} wheel on world -Y")

    steering_obj = settings.steering_wheel_object
    if steering_obj:
        steering_spin_alignment = abs(object_axis(steering_obj, BLENDER_AXIS_LOCAL[settings.steering_wheel_spin_axis]).dot(Vector((0, 1, 0))))
        if steering_spin_alignment < STEERING_WHEEL_DOT_THRESHOLD:
            warnings.append("Steering wheel configured spin axis should align with world Y forward/back")

    if settings.use_custom_sounds:
        for slot in SOUND_SLOTS:
            path = getattr(settings, f"sound_{slot}")
            if not path:
                errors.append(f"Sound slot is not assigned: {slot}")
            elif not os.path.isfile(abspath(path)):
                errors.append(f"Sound file for {slot} does not exist: {path}")

    if not settings.car_id:
        errors.append("Car ID is required")
    elif not settings.car_id.replace("_", "").replace("-", "").isalnum():
        errors.append("Car ID may only contain letters, numbers, underscore, and dash")

    return errors, warnings


class CarColliderSettings(PropertyGroup):
    object_ref: PointerProperty(name="Object", type=bpy.types.Object, update=update_collider_object)
    collider_type: EnumProperty(name="Type", items=(("trimesh", "Trimesh", ""), ("box", "Box", "")), default="trimesh")
    mass: FloatProperty(name="Mass", default=1230.0, min=0.0)


class CarWheelSettings(PropertyGroup):
    group: StringProperty(name="Group", default="front")
    key: StringProperty(name="Key", default="l")
    steering: BoolProperty(name="Steering", default=False)
    suspension_ref: PointerProperty(name="Mount", type=bpy.types.Object)
    hub_ref: PointerProperty(name="Joint", type=bpy.types.Object)
    wheel_ref: PointerProperty(name="Spin", type=bpy.types.Object)
    up_local_axis: EnumProperty(name="Up Local Axis", items=AXIS_ITEMS, default="z")
    spin_local_axis: EnumProperty(name="Spin Local Axis", items=AXIS_ITEMS, default="x")
    suspension_stiffness: FloatProperty(name="Suspension Stiffness", default=80.0)
    damping_relaxation: FloatProperty(name="Damping Relaxation", default=2.6)
    damping_compression: FloatProperty(name="Damping Compression", default=2.0)
    radius: FloatProperty(name="Radius", default=0.3, min=0.01)
    max_brake_force: FloatProperty(name="Max Brake Force", default=5000.0, min=0.0)
    pressure: FloatProperty(name="Pressure", default=2.0, min=1.3, max=2.7)
    camber: FloatProperty(name="Camber", default=-2.0)
    toe: FloatProperty(name="Toe", default=0.35)
    side_friction_stiffness: FloatProperty(name="Side Friction", default=1.0)
    side_factor: FloatProperty(name="Side Factor", default=1.0)
    forward_factor: FloatProperty(name="Forward Factor", default=1.6)
    brake_factor: FloatProperty(name="Brake Factor", default=1.0)
    contact_damping: FloatProperty(name="Contact Damping", default=0.15)


class CarExporterSettings(PropertyGroup):
    is_configured: BoolProperty(name="Configured", default=False)
    car_id: StringProperty(name="Car ID", default="my_car")
    display_name: StringProperty(name="Display Name", default="My Car")
    car_class: StringProperty(name="Class", default="GT")
    vehicle_tag_tarmac: BoolProperty(name="Tarmac", default=True)
    vehicle_tag_offroad: BoolProperty(name="Offroad", default=True)
    car_root_object: PointerProperty(name="Car Root", type=bpy.types.Object)
    center_of_mass_object: PointerProperty(name="Center of Mass", type=bpy.types.Object)
    steering_wheel_object: PointerProperty(name="Steering Wheel", type=bpy.types.Object)
    steering_wheel_spin_axis: EnumProperty(name="Steering Wheel Spin Axis", items=AXIS_ITEMS, default="y")
    down_force: FloatProperty(name="Downforce", default=3000.0)
    air_drag: FloatProperty(name="Air Drag", default=1.0, min=0.0, max=1.0)
    anti_roll: FloatProperty(name="Anti-roll", default=0.4)
    abs: FloatProperty(name="ABS", default=1.0, min=0.0, max=1.0)
    esc: FloatProperty(name="ESC", default=0.0, min=0.0, max=1.0)
    traction_control: FloatProperty(name="Traction Control", default=1.0, min=0.0, max=1.0)
    max_steering_angle: FloatProperty(name="Max Steering Angle", default=65.0, min=1.0, max=90.0)
    use_custom_sounds: BoolProperty(name="Use Custom Sounds", default=False)
    colliders: CollectionProperty(type=CarColliderSettings)
    wheels: CollectionProperty(type=CarWheelSettings)
    guide_length: FloatProperty(name="Guide Length", default=4.5, min=0.1, unit="LENGTH")
    guide_width: FloatProperty(name="Guide Width", default=2.0, min=0.1, unit="LENGTH")
    guide_wheelbase: FloatProperty(name="Wheelbase", default=2.7, min=0.1, unit="LENGTH")
    guide_track_width: FloatProperty(name="Track Width", default=1.65, min=0.1, unit="LENGTH")

    drive: EnumProperty(name="Drive", items=(("awd", "AWD", ""), ("fwd", "FWD", ""), ("rwd", "RWD", "")), default="awd")
    hp: FloatProperty(name="HP", default=590.0, min=1.0)
    diff_ratio: FloatProperty(name="Diff Ratio", default=5.0, min=0.01)
    max_rpm: IntProperty(name="Max RPM", default=8000, min=1)
    idle_rpm: IntProperty(name="Idle RPM", default=1000, min=1)
    rev_limit: IntProperty(name="Rev Limit", default=7900, min=1)
    engine_inertia: FloatProperty(name="Engine Inertia", default=0.9, min=0.01)
    engine_friction_torque: FloatProperty(name="Friction Torque", default=70.0, min=0.0)
    clutch_response: FloatProperty(name="Clutch Response", default=12.0, min=0.0)
    turbo_enabled: BoolProperty(name="Turbo Enabled", default=True)
    turbo_boost: FloatProperty(name="Turbo Boost", default=1.35, min=1.0)
    turbo_valve: BoolProperty(name="Turbo Valve", default=False)
    max_torque: FloatProperty(name="Max Torque", default=600.0, min=1.0)

    reverse_ratio: FloatProperty(name="Reverse", default=-3.57)
    forward_gear_count: IntProperty(name="Forward Gears", default=6, min=1, max=15)
    gear_1: FloatProperty(name="Gear 1", default=4.08)
    gear_2: FloatProperty(name="Gear 2", default=2.7)
    gear_3: FloatProperty(name="Gear 3", default=1.9)
    gear_4: FloatProperty(name="Gear 4", default=1.4)
    gear_5: FloatProperty(name="Gear 5", default=1.06)
    gear_6: FloatProperty(name="Gear 6", default=0.85)
    gear_7: FloatProperty(name="Gear 7", default=0.70)
    gear_8: FloatProperty(name="Gear 8", default=0.58)
    gear_9: FloatProperty(name="Gear 9", default=0.50)
    gear_10: FloatProperty(name="Gear 10", default=0.44)
    gear_11: FloatProperty(name="Gear 11", default=0.40)
    gear_12: FloatProperty(name="Gear 12", default=0.36)
    gear_13: FloatProperty(name="Gear 13", default=0.33)
    gear_14: FloatProperty(name="Gear 14", default=0.30)
    gear_15: FloatProperty(name="Gear 15", default=0.28)

    torque_1000: FloatProperty(name="1000 RPM", default=400.0)
    torque_2000: FloatProperty(name="2000 RPM", default=500.0)
    torque_3000: FloatProperty(name="3000 RPM", default=550.0)
    torque_4000: FloatProperty(name="4000 RPM", default=580.0)
    torque_5000: FloatProperty(name="5000 RPM", default=590.0)
    torque_6000: FloatProperty(name="6000 RPM", default=580.0)
    torque_7000: FloatProperty(name="7000 RPM", default=570.0)
    torque_8000: FloatProperty(name="8000 RPM", default=500.0)

    chase_camera_object: PointerProperty(name="Chase", type=bpy.types.Object, poll=camera_object_poll, update=update_chase_camera_object)
    cockpit_camera_object: PointerProperty(name="Cockpit", type=bpy.types.Object, poll=camera_object_poll, update=update_cockpit_camera_object)
    hood_camera_object: PointerProperty(name="Hood", type=bpy.types.Object, poll=camera_object_poll, update=update_hood_camera_object)
    roof_camera_object: PointerProperty(name="Roof", type=bpy.types.Object, poll=camera_object_poll, update=update_roof_camera_object)
    chase_fov: FloatProperty(name="Chase FOV", default=70.0)
    cockpit_fov: FloatProperty(name="Cockpit FOV", default=45.0)
    hood_fov: FloatProperty(name="Hood FOV", default=50.0)
    roof_fov: FloatProperty(name="Roof FOV", default=55.0)
    chase_target_distance: FloatProperty(name="Target Distance", default=5.0, min=0.01, update=update_chase_target_distance)
    cockpit_target_distance: FloatProperty(name="Target Distance", default=1.0, min=0.01, update=update_cockpit_target_distance)
    hood_target_distance: FloatProperty(name="Target Distance", default=2.0, min=0.01, update=update_hood_target_distance)
    roof_target_distance: FloatProperty(name="Target Distance", default=2.0, min=0.01, update=update_roof_target_distance)
    chase_shake: FloatProperty(name="Shake Intensity", default=16.0)
    cockpit_shake: FloatProperty(name="Shake Intensity", default=1.0)
    hood_shake: FloatProperty(name="Shake Intensity", default=1.1)
    roof_shake: FloatProperty(name="Shake Intensity", default=1.1)

    sound_tranny_on: StringProperty(name="Transmission On", subtype="FILE_PATH", default="")
    sound_tranny_off: StringProperty(name="Transmission Off", subtype="FILE_PATH", default="")
    sound_on_high: StringProperty(name="On High", subtype="FILE_PATH", default="")
    sound_on_mid: StringProperty(name="On Mid", subtype="FILE_PATH", default="")
    sound_on_low: StringProperty(name="On Low", subtype="FILE_PATH", default="")
    sound_off_high: StringProperty(name="Off High", subtype="FILE_PATH", default="")
    sound_off_mid: StringProperty(name="Off Mid", subtype="FILE_PATH", default="")
    sound_off_low: StringProperty(name="Off Low", subtype="FILE_PATH", default="")
    sound_limiter: StringProperty(name="Limiter", subtype="FILE_PATH", default="")
    sound_turbo_flutter: StringProperty(name="Turbo Flutter", subtype="FILE_PATH", default="")


def clear_configuration_settings(settings):
    settings.is_configured = False
    settings.car_id = ""
    settings.display_name = ""
    settings.car_class = ""
    settings.vehicle_tag_tarmac = False
    settings.vehicle_tag_offroad = False
    settings.car_root_object = None
    settings.center_of_mass_object = None
    settings.steering_wheel_object = None
    settings.steering_wheel_spin_axis = "y"
    settings.colliders.clear()
    settings.wheels.clear()
    settings.down_force = 0.0
    settings.air_drag = 0.0
    settings.anti_roll = 0.0
    settings.abs = 0.0
    settings.esc = 0.0
    settings.traction_control = 0.0
    settings.max_steering_angle = 1.0
    settings.use_custom_sounds = False
    settings.drive = "awd"
    settings.hp = 1.0
    settings.diff_ratio = 0.01
    settings.max_rpm = 1
    settings.idle_rpm = 1
    settings.rev_limit = 1
    settings.turbo_enabled = False
    settings.turbo_boost = 1.0
    settings.turbo_valve = False
    settings.max_torque = 1.0
    settings.reverse_ratio = 0.0
    settings.forward_gear_count = 1
    for index in range(1, 16):
        setattr(settings, f"gear_{index}", 0.0)
    for rpm in (1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000):
        setattr(settings, f"torque_{rpm}", 0.0)
    for prefix in CAMERA_PREFIXES:
        setattr(settings, f"{prefix}_camera_object", None)
        setattr(settings, f"{prefix}_fov", 0.0)
        setattr(settings, f"{prefix}_target_distance", 0.01)
        setattr(settings, f"{prefix}_shake", 0.0)
    for slot in SOUND_SLOTS:
        setattr(settings, f"sound_{slot}", "")
    settings.guide_length = 4.5
    settings.guide_width = 2.0
    settings.guide_wheelbase = 2.7
    settings.guide_track_width = 1.65
    remove_size_guide()


def initialize_configuration_settings(settings):
    clear_configuration_settings(settings)
    settings.is_configured = True
    settings.car_id = "my_car"
    settings.display_name = "My Car"
    settings.car_class = "GT"
    settings.vehicle_tag_tarmac = True
    settings.vehicle_tag_offroad = True
    settings.down_force = 3000.0
    settings.air_drag = 1.0
    settings.anti_roll = 0.4
    settings.abs = 1.0
    settings.esc = 0.0
    settings.traction_control = 1.0
    settings.max_steering_angle = 65.0
    settings.drive = "awd"
    settings.hp = 590.0
    settings.diff_ratio = 5.0
    settings.max_rpm = 8000
    settings.idle_rpm = 1000
    settings.rev_limit = 7900
    settings.turbo_enabled = True
    settings.turbo_boost = 1.35
    settings.turbo_valve = False
    settings.max_torque = 600.0
    settings.reverse_ratio = -3.57
    settings.forward_gear_count = 6
    for index, value in {
        1: 4.08,
        2: 2.7,
        3: 1.9,
        4: 1.4,
        5: 1.06,
        6: 0.85,
        7: 0.70,
        8: 0.58,
        9: 0.50,
        10: 0.44,
        11: 0.40,
        12: 0.36,
        13: 0.33,
        14: 0.30,
        15: 0.28,
    }.items():
        setattr(settings, f"gear_{index}", value)
    for rpm, value in {
        1000: 400.0,
        2000: 500.0,
        3000: 550.0,
        4000: 580.0,
        5000: 590.0,
        6000: 580.0,
        7000: 570.0,
        8000: 500.0,
    }.items():
        setattr(settings, f"torque_{rpm}", value)
    settings.chase_fov = 70.0
    settings.cockpit_fov = 45.0
    settings.hood_fov = 50.0
    settings.roof_fov = 55.0
    settings.chase_target_distance = 5.0
    settings.cockpit_target_distance = 1.0
    settings.hood_target_distance = 2.0
    settings.roof_target_distance = 2.0
    settings.chase_shake = 16.0
    settings.cockpit_shake = 1.0
    settings.hood_shake = 1.1
    settings.roof_shake = 1.1
    reset_torque_curve_node()
    ensure_default_wheels(settings)
    create_size_guide(settings)


def wheel_config(wheel):
    return {
        "steering": bool(wheel.steering),
        "mount": {
            "obj": object_config_name(wheel.suspension_ref),
            "stiffness": wheel.suspension_stiffness,
            "dampingRelaxation": wheel.damping_relaxation,
            "dampingCompression": wheel.damping_compression,
        },
        "joint": {
            "obj": object_config_name(wheel.hub_ref),
        },
        "spin": {
            "obj": object_config_name(wheel.wheel_ref),
            "upLocalAxis": BLENDER_AXIS_TO_GAME[wheel.up_local_axis],
            "spinLocalAxis": BLENDER_AXIS_TO_GAME[wheel.spin_local_axis],
            "radius": wheel.radius,
            "maxBrakeForce": wheel.max_brake_force,
            "pressure": wheel.pressure,
            "camber": wheel.camber,
            "toe": wheel.toe,
            "sideFrictionStiffness": wheel.side_friction_stiffness,
            "sideFactor": wheel.side_factor,
            "forwardFactor": wheel.forward_factor,
            "brakeFactor": wheel.brake_factor,
            "contactDamping": wheel.contact_damping,
        },
    }


def build_wheels_config(settings):
    ensure_default_wheels(settings)
    wheels = {}
    for wheel in settings.wheels:
        wheels.setdefault(wheel.group, {})[wheel.key] = wheel_config(wheel)
    return wheels


def sample_torque_curve(settings):
    max_rpm = max(1000, settings.max_rpm)
    sample_step = 1000
    torque_curve = {}

    sample_rpms = list(range(sample_step, max_rpm + 1, sample_step))
    if sample_rpms[-1] != max_rpm:
        sample_rpms.append(max_rpm)
    for rpm in sample_rpms:
        torque_curve[str(rpm)] = round(max(0, evaluate_torque_curve(rpm / max_rpm) * settings.max_torque), 3)

    return torque_curve


def default_torque_points():
    return [
        (0.00, 0.55),
        (0.18, 0.78),
        (0.32, 0.92),
        (0.48, 1.00),
        (0.68, 0.98),
        (0.84, 0.91),
        (1.00, 0.78),
    ]


def get_torque_curve_node(create=True):
    tree = bpy.data.node_groups.get(TORQUE_CURVE_NODE_GROUP)
    if tree is None:
        if not create:
            return None
        tree = bpy.data.node_groups.new(name=TORQUE_CURVE_NODE_GROUP, type="ShaderNodeTree")

    node = tree.nodes.get(TORQUE_CURVE_NODE)
    if node is None:
        if not create:
            return None
        node = tree.nodes.new("ShaderNodeFloatCurve")
        node.name = TORQUE_CURVE_NODE
        node.label = TORQUE_CURVE_NODE
        reset_torque_curve_node(node)

    return node


def reset_torque_curve_node(node=None):
    node = node or get_torque_curve_node()
    mapping = node.mapping
    mapping.initialize()
    mapping.use_clip = True
    mapping.clip_min_x = 0.0
    mapping.clip_max_x = 1.0
    mapping.clip_min_y = 0.0
    mapping.clip_max_y = 1.0

    curve = mapping.curves[0]
    while len(curve.points) > 2:
        curve.points.remove(curve.points[-2])

    points = default_torque_points()
    curve.points[0].location = points[0]
    curve.points[-1].location = points[-1]
    for x, y in points[1:-1]:
        curve.points.new(x, y)

    mapping.update()


def evaluate_torque_curve(rpm_ratio):
    node = get_torque_curve_node()
    mapping = node.mapping
    curve = mapping.curves[0]
    rpm_ratio = max(0.0, min(1.0, rpm_ratio))
    result = mapping.evaluate(curve, rpm_ratio)
    return max(0.0, min(1.0, result))


def initialize_car_exporter_defaults():
    try:
        get_torque_curve_node()
        for scene in bpy.data.scenes:
            if hasattr(scene, "car_exporter"):
                ensure_default_wheels(scene.car_exporter)
    except AttributeError:
        return 0.2
    return None


def apply_imported_torque_curve(settings, torque_curve):
    node = get_torque_curve_node()
    mapping = node.mapping
    mapping.initialize()
    mapping.use_clip = True
    mapping.clip_min_x = 0.0
    mapping.clip_max_x = 1.0
    mapping.clip_min_y = 0.0
    mapping.clip_max_y = 1.0

    curve = mapping.curves[0]
    while len(curve.points) > 2:
        curve.points.remove(curve.points[-2])

    max_rpm = max(1000, settings.max_rpm)
    max_torque = max(settings.max_torque, 1.0)
    points = [
        (min(max(int(rpm) / max_rpm, 0.0), 1.0), min(max(float(torque) / max_torque, 0.0), 1.0))
        for rpm, torque in sorted(torque_curve.items(), key=lambda item: int(item[0]))
    ]
    if len(points) < 2:
        points = default_torque_points()

    curve.points[0].location = points[0]
    curve.points[-1].location = points[-1]
    for x, y in points[1:-1]:
        curve.points.new(x, y)

    mapping.update()


def build_config(settings):
    sounds = {}
    if settings.use_custom_sounds:
        for slot, meta in SOUND_SLOTS.items():
            source_path = getattr(settings, f"sound_{slot}")
            if not source_path:
                continue
            source_name = Path(abspath(source_path)).name
            sounds[slot] = {
                "source": source_name,
                "rpm": meta["rpm"],
                "loop": meta["loop"],
                "volume": meta["volume"],
            }

    return {
        "id": settings.car_id,
        "displayName": settings.display_name,
        "class": settings.car_class,
        "trackTypes": [
            tag
            for tag, enabled in (
                ("tarmac", settings.vehicle_tag_tarmac),
                ("offroad", settings.vehicle_tag_offroad),
            )
            if enabled
        ],
        "type": "car",
        "engine": {
            "hp": settings.hp,
            "drive": settings.drive,
            "diffRatio": settings.diff_ratio,
            "maxRPM": settings.max_rpm,
            "idleRPM": settings.idle_rpm,
            "revLimit": settings.rev_limit,
            "inertia": settings.engine_inertia,
            "frictionTorque": settings.engine_friction_torque,
            "clutchResponse": settings.clutch_response,
            "gearRatios": {
                **{"0": 0, "-1": settings.reverse_ratio},
                **{
                    str(index): getattr(settings, f"gear_{index}")
                    for index in range(1, settings.forward_gear_count + 1)
                },
            },
            "torqueCurve": sample_torque_curve(settings),
            "turbo": {
                "enabled": settings.turbo_enabled,
                "boost": settings.turbo_boost,
                "valve": settings.turbo_valve,
                "load": 0.0,
            },
        },
        "body": {
            "obj": object_config_name(settings.car_root_object),
            "centerOfMass": object_config_name(settings.center_of_mass_object),
            "colliders": [
                {
                    "obj": object_config_name(collider.object_ref),
                    "type": collider.collider_type,
                    "mass": collider.mass,
                }
                for collider in settings.colliders
            ],
            "downForce": settings.down_force,
            "airDrag": settings.air_drag,
            "antiRoll": settings.anti_roll,
            "abs": settings.abs,
            "esc": settings.esc,
            "tractionControl": settings.traction_control,
            "maxSteeringAngle": settings.max_steering_angle,
        },
        "wheels": build_wheels_config(settings),
        "steeringWheel": {
            "obj": object_config_name(settings.steering_wheel_object),
            "spinLocalAxis": BLENDER_AXIS_TO_GAME[settings.steering_wheel_spin_axis],
        },
        "cameras": {
            "chase_cam": {
                "obj": object_config_name(settings.chase_camera_object),
                "fov": camera_fov(settings, "chase"),
                "shake": settings.chase_shake,
            },
            "cockpit_cam": {
                "obj": object_config_name(settings.cockpit_camera_object),
                "fov": camera_fov(settings, "cockpit"),
                "shake": settings.cockpit_shake,
            },
            "hood_cam": {
                "obj": object_config_name(settings.hood_camera_object),
                "fov": camera_fov(settings, "hood"),
                "shake": settings.hood_shake,
            },
            "roof_cam": {
                "obj": object_config_name(settings.roof_camera_object),
                "fov": camera_fov(settings, "roof"),
                "shake": settings.roof_shake,
            },
        },
        "sounds": sounds,
    }


def show_validation_popup(context, errors, warnings):
    title = "Car Validation Failed" if errors else "Car Validation Passed"
    icon = "ERROR" if errors else ("ERROR" if warnings else "CHECKMARK")

    def draw_popup(self, _context):
        layout = self.layout
        if errors:
            layout.label(text=f"Errors: {len(errors)}")
            for message in errors:
                layout.label(text=message, icon="ERROR")
        else:
            layout.label(text="No errors", icon="CHECKMARK")

        if warnings:
            layout.separator()
            layout.label(text=f"Warnings: {len(warnings)}")
            for message in warnings:
                layout.label(text=message, icon="ERROR")

    context.window_manager.popup_menu(draw_popup, title=title, icon=icon)


class CAR_EXPORTER_OT_validate_car(Operator):
    bl_idname = "car_exporter.validate_car"
    bl_label = "Validate Car"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = scene_settings(context)
        errors, warnings = validate_scene(settings)
        for msg in warnings:
            self.report({"WARNING"}, msg)
        for msg in errors:
            self.report({"ERROR"}, msg)
        if errors:
            show_validation_popup(context, errors, warnings)
            return {"CANCELLED"}
        show_validation_popup(context, errors, warnings)
        self.report({"INFO"}, f"Car validation passed with {len(warnings)} warning(s)")
        return {"FINISHED"}


class CAR_EXPORTER_OT_add_collider(Operator):
    bl_idname = "car_exporter.add_collider"
    bl_label = "Add Collider"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = scene_settings(context)
        collider = settings.colliders.add()
        collider.object_ref = None
        collider.collider_type = "trimesh"
        collider.mass = 0.0
        return {"FINISHED"}


class CAR_EXPORTER_OT_remove_collider(Operator):
    bl_idname = "car_exporter.remove_collider"
    bl_label = "Remove Collider"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty()

    def execute(self, context):
        settings = scene_settings(context)
        if 0 <= self.index < len(settings.colliders):
            collider = settings.colliders[self.index]
            collider.object_ref = None
            collider.collider_type = "trimesh"
            collider.mass = 0.0
            settings.colliders.remove(self.index)
        return {"FINISHED"}


class CAR_EXPORTER_OT_tooltip_label(Operator):
    bl_idname = "car_exporter.tooltip_label"
    bl_label = ""
    bl_options = {"INTERNAL"}

    tooltip: StringProperty()

    @classmethod
    def description(cls, _context, properties):
        return properties.tooltip

    def execute(self, _context):
        return {"FINISHED"}


def add_wheel_from_config(settings, group, key, data=None):
    data = data or {}
    mount = data.get("mount", {})
    joint_data = data.get("joint", {})
    spin_data = data.get("spin", {})
    wheel = settings.wheels.add()
    wheel.group = group
    wheel.key = key
    wheel.steering = bool(data.get("steering", group == "front"))
    set_object_pointer(wheel, "suspension_ref", mount.get("obj", ""))
    set_object_pointer(wheel, "hub_ref", joint_data.get("obj", ""))
    set_object_pointer(wheel, "wheel_ref", spin_data.get("obj", ""))
    wheel.up_local_axis = GAME_AXIS_TO_BLENDER.get(tuple(spin_data.get("upLocalAxis", [0, 1, 0])), "z")
    wheel.spin_local_axis = GAME_AXIS_TO_BLENDER.get(tuple(spin_data.get("spinLocalAxis", [1, 0, 0])), "x")
    wheel.suspension_stiffness = mount.get("stiffness", wheel.suspension_stiffness)
    wheel.damping_relaxation = mount.get("dampingRelaxation", wheel.damping_relaxation)
    wheel.damping_compression = mount.get("dampingCompression", wheel.damping_compression)
    wheel.radius = spin_data.get("radius", wheel.radius)
    wheel.max_brake_force = spin_data.get("maxBrakeForce", wheel.max_brake_force)
    wheel.pressure = spin_data.get("pressure", wheel.pressure)
    wheel.camber = spin_data.get("camber", wheel.camber)
    wheel.toe = spin_data.get("toe", wheel.toe)
    wheel.side_friction_stiffness = spin_data.get("sideFrictionStiffness", wheel.side_friction_stiffness)
    wheel.side_factor = spin_data.get("sideFactor", wheel.side_factor)
    wheel.forward_factor = spin_data.get("forwardFactor", wheel.forward_factor)
    wheel.brake_factor = spin_data.get("brakeFactor", wheel.brake_factor)
    wheel.contact_damping = spin_data.get("contactDamping", wheel.contact_damping)
    return wheel


def ensure_default_wheels(settings):
    expected = [(group, key) for group, key, _steering in WHEEL_KEYS]
    current = [(wheel.group, wheel.key) for wheel in settings.wheels]
    if current == expected:
        return

    existing = {
        (wheel.group, wheel.key): {
            "steering": wheel.steering,
            "suspension_ref": wheel.suspension_ref,
            "hub_ref": wheel.hub_ref,
            "wheel_ref": wheel.wheel_ref,
            "up_local_axis": wheel.up_local_axis,
            "spin_local_axis": wheel.spin_local_axis,
            "suspension_stiffness": wheel.suspension_stiffness,
            "damping_relaxation": wheel.damping_relaxation,
            "damping_compression": wheel.damping_compression,
            "radius": wheel.radius,
            "max_brake_force": wheel.max_brake_force,
            "pressure": wheel.pressure,
            "camber": wheel.camber,
            "toe": wheel.toe,
            "side_friction_stiffness": wheel.side_friction_stiffness,
            "side_factor": wheel.side_factor,
            "forward_factor": wheel.forward_factor,
            "brake_factor": wheel.brake_factor,
            "contact_damping": wheel.contact_damping,
        }
        for wheel in settings.wheels
    }
    settings.wheels.clear()
    for group, key, steering in WHEEL_KEYS:
        imported = existing.get((group, key))
        if imported:
            wheel = settings.wheels.add()
            wheel.group = group
            wheel.key = key
            wheel.steering = imported["steering"]
            wheel.suspension_ref = imported["suspension_ref"]
            wheel.hub_ref = imported["hub_ref"]
            wheel.wheel_ref = imported["wheel_ref"]
            wheel.up_local_axis = imported["up_local_axis"]
            wheel.spin_local_axis = imported["spin_local_axis"]
            wheel.suspension_stiffness = imported["suspension_stiffness"]
            wheel.damping_relaxation = imported["damping_relaxation"]
            wheel.damping_compression = imported["damping_compression"]
            wheel.radius = imported["radius"]
            wheel.max_brake_force = imported["max_brake_force"]
            wheel.pressure = imported["pressure"]
            wheel.camber = imported["camber"]
            wheel.toe = imported["toe"]
            wheel.side_friction_stiffness = imported["side_friction_stiffness"]
            wheel.side_factor = imported["side_factor"]
            wheel.forward_factor = imported["forward_factor"]
            wheel.brake_factor = imported["brake_factor"]
            wheel.contact_damping = imported["contact_damping"]
            continue

        add_wheel_from_config(settings, group, key, {
            "steering": steering,
            "mount": {
                "stiffness": 80,
                "dampingRelaxation": 2.6,
                "dampingCompression": 2.0,
            },
            "joint": {},
            "spin": {
                "upLocalAxis": [0, 1, 0],
                "spinLocalAxis": [1, 0, 0],
                "radius": 0.3,
                "maxBrakeForce": 5000,
                "pressure": 2.0,
                "camber": -2.0,
                "toe": 0.35,
                "sideFrictionStiffness": 1.0,
                "sideFactor": 1.0,
                "forwardFactor": 1.6,
                "brakeFactor": 1.0,
                "contactDamping": 0.15,
            },
        })


def schedule_defaults_initialization():
    if not bpy.app.timers.is_registered(initialize_car_exporter_defaults):
        bpy.app.timers.register(initialize_car_exporter_defaults, first_interval=0.1)


@persistent
def initialize_car_exporter_defaults_after_load(_dummy):
    schedule_defaults_initialization()



class CAR_EXPORTER_OT_reset_torque_curve(Operator):
    bl_idname = "car_exporter.reset_torque_curve"
    bl_label = "Reset Torque Curve"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        reset_torque_curve_node()
        return {"FINISHED"}


class CAR_EXPORTER_OT_create_configuration(Operator):
    bl_idname = "car_exporter.create_configuration"
    bl_label = "Create Configuration"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = scene_settings(context)
        initialize_configuration_settings(settings)
        return {"FINISHED"}


class CAR_EXPORTER_OT_remove_configuration(Operator):
    bl_idname = "car_exporter.remove_configuration"
    bl_label = "Remove Configuration"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        settings = scene_settings(context)
        clear_configuration_settings(settings)
        return {"FINISHED"}


class CAR_EXPORTER_OT_export_car_zip(Operator, ExportHelper):
    bl_idname = "car_exporter.export_car_zip"
    bl_label = "Export Car Zip"
    bl_options = {"REGISTER"}
    filename_ext = ".zip"

    filepath: StringProperty(name="Export Zip", subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})

    def execute(self, context):
        settings = scene_settings(context)
        errors, warnings = validate_scene(settings)
        for msg in warnings:
            self.report({"WARNING"}, msg)
        if errors:
            for msg in errors:
                self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        export_zip = Path(abspath(self.filepath))
        if not export_zip.name.lower().endswith(".zip"):
            export_zip = export_zip.with_suffix(".zip")
        export_zip.parent.mkdir(parents=True, exist_ok=True)
        ensure_camera_targets(settings)

        with tempfile.TemporaryDirectory(prefix="car_exporter_") as temp_dir:
            temp_path = Path(temp_dir)
            sounds_path = temp_path / "sounds"
            if settings.use_custom_sounds:
                sounds_path.mkdir()

            with_helpers_unlinked(lambda: bpy.ops.export_scene.gltf(
                filepath=str(temp_path / "model.glb"),
                export_format="GLB",
                use_selection=False,
                export_apply=True,
                export_cameras=True,
            ))

            config = build_config(settings)
            (temp_path / "config.json").write_text(json.dumps(config, indent=4), encoding="utf-8")

            copied = set()
            if settings.use_custom_sounds:
                for slot in SOUND_SLOTS:
                    source = getattr(settings, f"sound_{slot}")
                    if not source:
                        continue
                    source_path = Path(abspath(source))
                    if source_path.is_file() and source_path.name not in copied:
                        shutil.copy2(source_path, sounds_path / source_path.name)
                        copied.add(source_path.name)

            with zipfile.ZipFile(export_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                if settings.use_custom_sounds:
                    archive.writestr("sounds/", "")
                for path in temp_path.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(temp_path).as_posix())

        self.report({"INFO"}, f"Exported {export_zip}")
        return {"FINISHED"}

    def invoke(self, context, event):
        settings = scene_settings(context)
        if not self.filepath:
            self.filepath = f"//{settings.car_id or 'car'}.zip"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class CAR_EXPORTER_OT_import_config(Operator):
    bl_idname = "car_exporter.import_car_config"
    bl_label = "Import Car Config"
    bl_options = {"REGISTER"}

    filepath: StringProperty(name="Config JSON", subtype="FILE_PATH")

    def execute(self, context):
        settings = scene_settings(context)
        settings.is_configured = True
        data = json.loads(Path(abspath(self.filepath)).read_text(encoding="utf-8"))
        engine = data.get("engine", {})
        body = data.get("body", {})
        settings.car_id = data.get("id", data.get("name", settings.car_id))
        settings.display_name = data.get("displayName", data.get("name", settings.display_name))
        settings.car_class = data.get("class", settings.car_class)
        track_types = data.get("trackTypes")
        if isinstance(track_types, list):
            tags = set(track_types)
            settings.vehicle_tag_tarmac = "tarmac" in tags
            settings.vehicle_tag_offroad = "offroad" in tags
        settings.drive = engine.get("drive", settings.drive)
        settings.hp = engine.get("hp", settings.hp)
        settings.diff_ratio = engine.get("diffRatio", settings.diff_ratio)
        settings.max_rpm = engine.get("maxRPM", settings.max_rpm)
        settings.idle_rpm = engine.get("idleRPM", settings.idle_rpm)
        settings.rev_limit = engine.get("revLimit", settings.rev_limit)
        settings.engine_inertia = engine.get("inertia", settings.engine_inertia)
        settings.engine_friction_torque = engine.get("frictionTorque", settings.engine_friction_torque)
        settings.clutch_response = engine.get("clutchResponse", settings.clutch_response)
        set_object_pointer(settings, "car_root_object", body.get("obj", ""))
        set_object_pointer(settings, "center_of_mass_object", body.get("centerOfMass", ""))
        settings.down_force = body.get("downForce", settings.down_force)
        settings.air_drag = body.get("airDrag", settings.air_drag)
        settings.anti_roll = body.get("antiRoll", settings.anti_roll)
        settings.abs = body.get("abs", settings.abs)
        settings.esc = body.get("esc", settings.esc)
        settings.traction_control = body.get("tractionControl", settings.traction_control)
        settings.max_steering_angle = body.get("maxSteeringAngle", settings.max_steering_angle)

        colliders = body.get("colliders") or []
        settings.colliders.clear()
        for collider_data in colliders:
            collider = settings.colliders.add()
            set_object_pointer(collider, "object_ref", collider_data.get("obj", ""))
            collider.collider_type = collider_data.get("type", "trimesh")
            collider.mass = collider_data.get("mass", 0.0)

        wheels = data.get("wheels") or {}
        settings.wheels.clear()
        if isinstance(wheels, dict):
            for group, group_wheels in wheels.items():
                if isinstance(group_wheels, dict):
                    for key, wheel_data in group_wheels.items():
                        add_wheel_from_config(settings, group, key, wheel_data)
        ensure_default_wheels(settings)

        ratios = engine.get("gearRatios", {})
        settings.reverse_ratio = ratios.get("-1", settings.reverse_ratio)
        positive_gears = sorted(int(key) for key in ratios.keys() if key.isdigit() and int(key) > 0)
        if positive_gears:
            settings.forward_gear_count = min(max(positive_gears), 15)
        for index in range(1, 16):
            setattr(settings, f"gear_{index}", ratios.get(str(index), getattr(settings, f"gear_{index}")))

        torque = engine.get("torqueCurve", {})
        if torque:
            settings.max_torque = max(float(value) for value in torque.values())
            apply_imported_torque_curve(settings, torque)
        for rpm in (1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000):
            setattr(settings, f"torque_{rpm}", torque.get(str(rpm), getattr(settings, f"torque_{rpm}")))

        turbo = engine.get("turbo", {})
        settings.turbo_enabled = turbo.get("enabled", settings.turbo_enabled)
        settings.turbo_boost = turbo.get("boost", settings.turbo_boost)
        settings.turbo_valve = turbo.get("valve", settings.turbo_valve)
        steering_wheel = data.get("steeringWheel", "")
        if isinstance(steering_wheel, dict):
            set_object_pointer(settings, "steering_wheel_object", steering_wheel.get("obj", ""))
            settings.steering_wheel_spin_axis = GAME_AXIS_TO_BLENDER.get(
                tuple(steering_wheel.get("spinLocalAxis", [0, 0, -1])),
                "y",
            )
        else:
            set_object_pointer(settings, "steering_wheel_object", steering_wheel)
            settings.steering_wheel_spin_axis = "y"

        cameras = data.get("cameras", {})
        for name, attr in (
            ("chase_cam", "chase"),
            ("cockpit_cam", "cockpit"),
            ("hood_cam", "hood"),
            ("roof_cam", "roof"),
        ):
            camera = cameras.get(name, {})
            set_object_pointer(settings, f"{attr}_camera_object", camera.get("obj", ""))
            setattr(settings, f"{attr}_fov", camera.get("fov", getattr(settings, f"{attr}_fov")))
            setattr(settings, f"{attr}_shake", camera.get("shake", getattr(settings, f"{attr}_shake")))

        create_size_guide(settings)
        self.report({"INFO"}, "Imported config values into scene settings")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


def draw_split_prop(layout, data, prop_name, label=None, **kwargs):
    split = layout.split(factor=0.4, align=True)
    property_label = label or data.bl_rna.properties[prop_name].name
    split.label(text=property_label)
    split.prop(data, prop_name, text="", **kwargs)


def draw_split_label(layout, label, value, tooltip=""):
    split = layout.split(factor=0.4, align=True)
    split.label(text=label)
    value_row = split.row(align=True)
    value_row.label(text=value)
    if tooltip:
        help_op = value_row.operator("car_exporter.tooltip_label", text="", icon="HELP", emboss=False)
        help_op.tooltip = tooltip


def draw_vehicle_tags(layout, settings):
    split = layout.split(factor=0.4, align=True)
    split.label(text="Track Types")
    tag_buttons = split.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
    tag_buttons.prop(settings, "vehicle_tag_tarmac", text="Tarmac", toggle=True)
    tag_buttons.prop(settings, "vehicle_tag_offroad", text="Offroad", toggle=True)


def draw_torque_curve(layout, settings):
    draw_split_prop(layout, settings, "max_torque")
    layout.operator("car_exporter.reset_torque_curve", text="Reset Curve")
    node = get_torque_curve_node(create=True)
    if node:
        layout.template_curve_mapping(node, "mapping", type="NONE")
    else:
        layout.label(text="Torque curve initializing")


def draw_colliders(layout, settings):
    header = layout.row(align=True)
    header.label(text="")
    header.operator("car_exporter.add_collider", text="Add", icon="ADD")

    if len(settings.colliders) == 0:
        layout.label(text="No colliders configured")
        return

    for index, collider in enumerate(settings.colliders):
        row = layout.row(align=True)
        row.label(text=f"Collider {index + 1}")
        remove = row.operator("car_exporter.remove_collider", text="", icon="REMOVE")
        remove.index = index
        draw_split_prop(layout, collider, "object_ref", label="Object")
        draw_split_prop(layout, collider, "collider_type", label="Type")
        draw_split_prop(layout, collider, "mass")


def draw_wheels(layout, settings):
    if len(settings.wheels) == 0:
        ensure_default_wheels(settings)

    for index, wheel in enumerate(settings.wheels):
        if index > 0:
            layout.separator()
        row = layout.row(align=True)
        row.label(text=WHEEL_LABELS.get((wheel.group, wheel.key), f"Wheel {index + 1}"))
        draw_split_prop(layout, wheel, "steering")
        draw_split_prop(layout, wheel, "suspension_ref")
        draw_split_prop(layout, wheel, "hub_ref")
        draw_split_prop(layout, wheel, "wheel_ref", label="Spin")
        draw_split_prop(layout, wheel, "up_local_axis")
        draw_split_prop(layout, wheel, "spin_local_axis")
        draw_split_prop(layout, wheel, "radius")
        draw_split_prop(layout, wheel, "pressure")
        draw_split_prop(layout, wheel, "camber")
        draw_split_prop(layout, wheel, "toe")
        row = layout.row(align=True)
        row.label(text="Sim")
        draw_split_prop(layout, wheel, "suspension_stiffness")
        draw_split_prop(layout, wheel, "damping_relaxation")
        draw_split_prop(layout, wheel, "damping_compression")
        draw_split_prop(layout, wheel, "max_brake_force")
        draw_split_prop(layout, wheel, "side_friction_stiffness")
        draw_split_prop(layout, wheel, "side_factor")
        draw_split_prop(layout, wheel, "forward_factor")
        draw_split_prop(layout, wheel, "brake_factor")
        draw_split_prop(layout, wheel, "contact_damping")


def draw_cameras(layout, settings):
    cameras = (
        ("Chase Cam", "chase"),
        ("Cockpit Cam", "cockpit"),
        ("Hood Cam", "hood"),
        ("Roof Cam", "roof"),
    )

    for index, (label, prefix) in enumerate(cameras):
        if index > 0:
            layout.separator()
        row = layout.row(align=True)
        row.label(text=label)
        draw_split_prop(layout, settings, f"{prefix}_camera_object", label="Object")
        if not getattr(settings, f"{prefix}_camera_object"):
            continue
        draw_split_prop(layout, settings, f"{prefix}_target_distance", label="Target Distance")
        draw_split_prop(layout, settings, f"{prefix}_shake", label="Shake Intensity")
        draw_split_label(layout, "FOV", f"{camera_fov(settings, prefix):.1f}", tooltip="Adjust FOV from Camera Properties")


class CAR_EXPORTER_PT_car_export(Panel):
    bl_label = "VectorG Car Exporter"
    bl_idname = "CAR_EXPORTER_PT_car_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VectorG"

    def draw(self, context):
        layout = self.layout
        settings = scene_settings(context)

        if not settings.is_configured:
            layout.operator("car_exporter.create_configuration", icon="ADD")
            return

        box = layout.box()
        box.label(text="Package")
        draw_split_prop(box, settings, "car_id")
        draw_split_prop(box, settings, "display_name")
        draw_split_prop(box, settings, "car_class")
        draw_vehicle_tags(box, settings)

        box = layout.box()
        box.label(text="Body")
        draw_split_prop(box, settings, "car_root_object")

        box = layout.box()
        box.label(text="Steering Wheel")
        draw_split_prop(box, settings, "steering_wheel_object")
        draw_split_prop(box, settings, "steering_wheel_spin_axis")

        box = layout.box()
        box.label(text="Engine")
        for prop in (
            "drive",
            "hp",
            "diff_ratio",
            "idle_rpm",
            "max_rpm",
            "rev_limit",
            "engine_inertia",
            "engine_friction_torque",
            "clutch_response",
        ):
            draw_split_prop(box, settings, prop)
        draw_split_prop(box, settings, "turbo_enabled")
        draw_split_prop(box, settings, "turbo_boost")
        draw_split_prop(box, settings, "turbo_valve")

        box = layout.box()
        box.label(text="Torque Curve")
        draw_torque_curve(box, settings)

        box = layout.box()
        box.label(text="Gears")
        draw_split_prop(box, settings, "reverse_ratio")
        draw_split_prop(box, settings, "forward_gear_count")
        for index in range(1, settings.forward_gear_count + 1):
            draw_split_prop(box, settings, f"gear_{index}")

        box = layout.box()
        box.label(text="Body Physics")
        draw_split_prop(box, settings, "center_of_mass_object")
        for prop in ("down_force", "air_drag", "anti_roll", "abs", "esc", "traction_control", "max_steering_angle"):
            draw_split_prop(box, settings, prop)

        box = layout.box()
        box.label(text="Colliders")
        draw_colliders(box, settings)

        box = layout.box()
        box.label(text="Wheels")
        draw_wheels(box, settings)

        box = layout.box()
        box.label(text="Cameras")
        draw_cameras(box, settings)

        box = layout.box()
        box.label(text="Audio")
        draw_split_prop(box, settings, "use_custom_sounds")
        if settings.use_custom_sounds:
            for slot, meta in SOUND_SLOTS.items():
                draw_split_prop(box, settings, f"sound_{slot}", label=meta["label"])

        box = layout.box()
        box.operator("car_exporter.remove_configuration", icon="TRASH")

        box = layout.box()
        row = box.row()
        row.operator("car_exporter.validate_car", icon="CHECKMARK")
        row.operator("car_exporter.import_car_config", icon="IMPORT")
        box.operator("car_exporter.export_car_zip", icon="EXPORT")


classes = (
    CarColliderSettings,
    CarWheelSettings,
    CarExporterSettings,
    CAR_EXPORTER_OT_validate_car,
    CAR_EXPORTER_OT_add_collider,
    CAR_EXPORTER_OT_remove_collider,
    CAR_EXPORTER_OT_tooltip_label,
    CAR_EXPORTER_OT_reset_torque_curve,
    CAR_EXPORTER_OT_create_configuration,
    CAR_EXPORTER_OT_remove_configuration,
    CAR_EXPORTER_OT_export_car_zip,
    CAR_EXPORTER_OT_import_config,
    CAR_EXPORTER_PT_car_export,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.car_exporter = PointerProperty(type=CarExporterSettings)
    if initialize_car_exporter_defaults_after_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(initialize_car_exporter_defaults_after_load)
    schedule_defaults_initialization()


def unregister():
    if bpy.app.timers.is_registered(initialize_car_exporter_defaults):
        bpy.app.timers.unregister(initialize_car_exporter_defaults)
    if initialize_car_exporter_defaults_after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(initialize_car_exporter_defaults_after_load)
    del bpy.types.Scene.car_exporter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
