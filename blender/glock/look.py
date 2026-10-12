"""Materials, lighting, backdrop, cameras and render settings.

Contract: build_materials() defines every material in common.MATERIAL_NAMES (called before
the parts are built); build_scene() adds world, lights, backdrop and the Cam_* cameras and
sets render settings (called after the parts are built).
"""


def build_materials():
    pass


def build_scene():
    raise NotImplementedError
