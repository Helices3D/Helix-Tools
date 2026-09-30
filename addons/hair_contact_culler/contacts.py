# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite world-space polyline / triangle-surface contact tests.

This module is original generic implementation. It uses Blender's BVH only to
find conservative candidates; contact is tested against a finite tapered swept
sphere on every displayed segment. It is not an inside/outside volume solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _vec(value):
    result = tuple(float(value[i]) for i in range(3))
    if not all(math.isfinite(x) for x in result):
        raise ValueError("Contact coordinates must be finite")
    return result


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return math.fsum((a[0] * b[0], a[1] * b[1], a[2] * b[2]))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _line(w, v, t):
    return (w[0] + v[0] * t, w[1] + v[1] * t, w[2] + v[2] * t)


def _interval(lo, hi, c, d):
    """Intersect [lo, hi] with c + d*t >= 0."""
    if d > 0.0:
        lo = max(lo, -c / d)
    elif d < 0.0:
        hi = min(hi, -c / d)
    elif c < 0.0:
        return None
    return (lo, hi) if lo <= hi else None


def _feature_contact(w, v, radius, delta_radius, interval):
    """Exact quadratic minimum on one affine closest-feature interval."""
    if interval is None:
        return False
    lo, hi = interval
    qa = _dot(v, v) - delta_radius * delta_radius
    qb = 2.0 * (_dot(w, v) - radius * delta_radius)
    arithmetic_scale = max(*(abs(x) for x in w), *(abs(x) for x in v),
                           abs(radius), abs(delta_radius), 1.0e-100)
    distance_error = 16.0 * math.ulp(arithmetic_scale)
    candidates = [lo, hi]
    if qa > 0.0:
        stationary = -qb / (2.0 * qa)
        if lo < stationary < hi:
            candidates.append(stationary)
    for t in candidates:
        distance = _line(w, v, t)
        distance_sq = _dot(distance, distance)
        r = radius + delta_radius * t
        radius_sq = r * r
        # Evaluate the vectors themselves, not the expanded polynomial: this
        # avoids cancellation for long segments contacting very small radii.
        error = 2.0e-12 * max(distance_sq, radius_sq, 1.0e-30)
        if distance_sq <= (r + distance_error) ** 2 + error:
            return True
    return False


def _feature_first_contact(w, v, radius, delta_radius, interval):
    """First contact on a clipped affine triangle feature, or None.

    The quadratic minimum supplies a proven contact bracket. We then isolate
    its first boundary using the original vectors. Direct discriminant roots
    lose narrow contacts when a long segment crosses a very small radius.
    Distance to the affine feature minus the nonnegative linear radius is
    convex, so the bracket from a separated lower endpoint to a contact point
    contains only the first contact boundary. The contact predicate has the
    same floating-point boundary tolerance as the boolean API.
    """
    if interval is None:
        return None
    lo, hi = interval
    qa = _dot(v, v) - delta_radius * delta_radius
    qb = 2.0 * (_dot(w, v) - radius * delta_radius)
    arithmetic_scale = max(*(abs(x) for x in w), *(abs(x) for x in v),
                           abs(radius), abs(delta_radius), 1.0e-100)
    distance_error = 16.0 * math.ulp(arithmetic_scale)

    def touches(t):
        distance = _line(w, v, t)
        distance_sq = _dot(distance, distance)
        r = radius + delta_radius * t
        radius_sq = r * r
        error = 2.0e-12 * max(distance_sq, radius_sq, 1.0e-30)
        return distance_sq <= (r + distance_error) ** 2 + error

    if touches(lo):
        return lo
    candidates = [hi]
    if qa > 0.0:
        stationary = -qb / (2.0 * qa)
        if lo < stationary < hi:
            candidates.append(stationary)
    contacts = [t for t in candidates if touches(t)]
    if not contacts:
        return None
    upper = min(contacts)
    lower = lo
    # 64 iterations cover the double-precision interval [0, 1]. Termination
    # also detects adjacent representable floats, returning the contacting
    # side rather than a value just outside the surface.
    for _ in range(64):
        middle = lower + (upper - lower) * 0.5
        if middle == lower or middle == upper:
            break
        if touches(middle):
            upper = middle
        else:
            lower = middle
    return upper


@dataclass(frozen=True)
class _Triangle:
    vertices: tuple
    edges: tuple
    normal: tuple
    normal_sq: float

    @classmethod
    def make(cls, triangle):
        if len(triangle) != 3:
            raise ValueError("Contact surfaces must be triangulated")
        a, b, c = (_vec(point) for point in triangle)
        edges = tuple((x, _sub(y, x)) for x, y in ((a, b), (b, c), (c, a)))
        normal = _cross(_sub(b, a), _sub(c, a))
        return cls((a, b, c), edges, normal, _dot(normal, normal))


def _segment_contact(a, b, radius, delta_radius, triangle):
    return any(_feature_contact(w, v, radius, delta_radius, interval)
               for w, v, interval in _segment_features(a, b, triangle))


def _segment_features(a, b, triangle):
    """Affine displacements and valid intervals for the triangle's features."""
    velocity = _sub(b, a)
    ta, tb, tc = triangle.vertices
    # Interior of the triangle: barycentric coordinates of the perpendicular
    # plane projection are affine in t. Clip by all three barycentric bounds.
    if triangle.normal_sq > 0.0:
        normal = triangle.normal
        normal_sq = triangle.normal_sq
        edge_ab, edge_ac = _sub(tb, ta), _sub(tc, ta)
        relative = _sub(a, ta)
        beta0 = _dot(_cross(relative, edge_ac), normal) / normal_sq
        beta1 = _dot(_cross(velocity, edge_ac), normal) / normal_sq
        gamma0 = _dot(_cross(edge_ab, relative), normal) / normal_sq
        gamma1 = _dot(_cross(edge_ab, velocity), normal) / normal_sq
        interval = (0.0, 1.0)
        for constant, slope in ((beta0, beta1), (gamma0, gamma1),
                                (1.0 - beta0 - gamma0, -beta1 - gamma1)):
            interval = _interval(*interval, constant, slope) if interval else None
        if interval:
            inverse_normal_length = 1.0 / math.sqrt(normal_sq)
            signed0 = _dot(relative, normal) * inverse_normal_length
            signed1 = _dot(velocity, normal) * inverse_normal_length
            yield (signed0, 0.0, 0.0), (signed1, 0.0, 0.0), interval
    # Each finite edge, then each vertex. These also cover zero-area triangles.
    for origin, edge in triangle.edges:
        length_sq = _dot(edge, edge)
        if length_sq == 0.0:
            continue
        relative = _sub(a, origin)
        u0 = _dot(relative, edge) / length_sq
        u1 = _dot(velocity, edge) / length_sq
        interval = _interval(0.0, 1.0, u0, u1)
        interval = _interval(*interval, 1.0 - u0, -u1) if interval else None
        if interval:
            residual0 = _sub(relative, tuple(x * u0 for x in edge))
            residual1 = _sub(velocity, tuple(x * u1 for x in edge))
            yield residual0, residual1, interval
    for vertex in triangle.vertices:
        yield _sub(a, vertex), velocity, (0.0, 1.0)


def _segment_first_contact(a, b, radius, delta_radius, triangle):
    earliest = None
    for w, v, interval in _segment_features(a, b, triangle):
        # Features that begin after an already proven contact cannot improve
        # it. Shortening the interval also reduces unnecessary bisection work.
        if earliest is not None:
            if interval[0] >= earliest:
                continue
            interval = interval[0], min(interval[1], earliest)
        contact = _feature_first_contact(w, v, radius, delta_radius, interval)
        if contact is not None:
            earliest = contact if earliest is None else min(earliest, contact)
            if earliest == 0.0:
                break
    return earliest


def _parameters(a, b, ra, rb, allowance):
    a, b = _vec(a), _vec(b)
    ra, rb, allowance = float(ra), float(rb), float(allowance)
    if not all(math.isfinite(x) and x >= 0.0 for x in (ra, rb, allowance)):
        raise ValueError("Point radii and contact allowance must be finite and nonnegative")
    return a, b, ra + allowance, rb - ra


def segment_contact(a, b, ra, rb, triangle, allowance=0.0):
    """Whether a finite linear-radius hair segment touches a triangle.

    Positions and radii must already be in the same physical world space.
    Allowance expands both endpoint radii by the stated nonnegative distance.
    Degenerate segments and triangles are valid; NaN/negative inputs are not.
    """
    a, b, radius, delta_radius = _parameters(a, b, ra, rb, allowance)
    return _segment_contact(a, b, radius, delta_radius, _Triangle.make(triangle))


def segment_first_contact(a, b, ra, rb, triangle, allowance=0.0):
    """Return the first tapered-radius surface-contact parameter, or None.

    The result is in [0, 1] along the supplied a-to-b direction. Initial
    overlap returns 0. Reversing the segment asks for the opposite entrance
    boundary, not generally 1 minus the original result. Positions and radii
    must share world space. Allowance expands both endpoint radii. Surface
    tangencies and degenerate geometry use the boolean API's tolerance.
    """
    a, b, radius, delta_radius = _parameters(a, b, ra, rb, allowance)
    return _segment_first_contact(a, b, radius, delta_radius, _Triangle.make(triangle))


class MeshContacts:
    """Compiled world triangle BVH with an exact finite tapered narrow phase."""

    def __init__(self, triangles):
        from mathutils.bvhtree import BVHTree
        self.triangles = tuple(_Triangle.make(triangle) for triangle in triangles)
        vertices = [vertex for tri in self.triangles for vertex in tri.vertices]
        faces = [(i, i + 1, i + 2) for i in range(0, len(vertices), 3)]
        self.tree = BVHTree.FromPolygons(vertices, faces, all_triangles=True) if faces else None
        # Blender's BVH coordinates are single precision. This padding only
        # broadens candidate discovery, never the final contact decision.
        self.bvh_pad = (max((abs(x) for v in vertices for x in v), default=1.0)
                        * 8.0 * 2.0 ** -23)

    def contacts(self, a, b, ra, rb, allowance=0.0):
        """Return True on any triangle-surface contact; no enclosure test."""
        from mathutils import Vector
        a, b, radius, delta_radius = _parameters(a, b, ra, rb, allowance)
        if self.tree is None:
            return False
        midpoint = tuple((x + y) * 0.5 for x, y in zip(a, b))
        half_length = math.sqrt(_dot(_sub(b, a), _sub(b, a))) * 0.5
        max_radius = max(radius, radius + delta_radius)
        # If a triangle contacts at any t, the triangle is no farther from the
        # midpoint than half the segment length plus its largest sample radius.
        broad_range = half_length + max_radius + self.bvh_pad
        for _, _, index, _ in self.tree.find_nearest_range(Vector(midpoint), broad_range):
            if _segment_contact(a, b, radius, delta_radius, self.triangles[index]):
                return True
        return False

    def first_contact(self, a, b, ra, rb, allowance=0.0):
        """Return the earliest contact with any surface along a-to-b, or None."""
        from mathutils import Vector
        a, b, radius, delta_radius = _parameters(a, b, ra, rb, allowance)
        if self.tree is None:
            return None
        midpoint = tuple((x + y) * 0.5 for x, y in zip(a, b))
        half_length = math.sqrt(_dot(_sub(b, a), _sub(b, a))) * 0.5
        max_radius = max(radius, radius + delta_radius)
        broad_range = half_length + max_radius + self.bvh_pad
        earliest = None
        for _, _, index, _ in self.tree.find_nearest_range(Vector(midpoint), broad_range):
            contact = _segment_first_contact(a, b, radius, delta_radius,
                                             self.triangles[index])
            if contact is not None:
                earliest = contact if earliest is None else min(earliest, contact)
                if earliest == 0.0:
                    break
        return earliest
