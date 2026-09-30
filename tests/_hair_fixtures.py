"""Small native hair and garment fixtures; generated data only."""

import bpy


def make_hair(name="Test Hair", count=1):
    data = bpy.data.hair_curves.new(name)
    data.add_curves([4] * count)
    points = [(x, y + strand * 0.2, z) for strand in range(count)
              for x, y, z in ((0, 0, 0), (1, 1, 0), (2, 0, 0), (3, 1, 0))]
    data.attributes["position"].data.foreach_set("vector", [v for p in points for v in p])
    data.set_types(type="POLY")
    data.attributes.new("radius", "FLOAT", "POINT").data.foreach_set("value", [0.1] * len(points))
    data.attributes.new("test_strand_id", "INT", "CURVE").data.foreach_set("value", list(range(count)))
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_garment(name="Test Garment", x=1.5):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(x, -10, -10), (x, 10, -10), (x, 10, 10), (x, -10, 10)], [], [(0, 1, 2, 3)])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def select_objects(*objects):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[-1]
    bpy.context.view_layer.update()


def evaluated_data(source):
    bpy.context.view_layer.update()
    return source.evaluated_get(bpy.context.evaluated_depsgraph_get()).data


def positions(data):
    return [tuple(point.vector) for point in data.attributes["position"].data]
