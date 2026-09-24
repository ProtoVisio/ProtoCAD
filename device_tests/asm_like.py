"""Формы для проверок, общие со сборкой."""

from protocad import kernel


def bolt_shape(radius=1.5, length=10.0, head_radius=2.75, head=2.0):
    shaft = kernel.cylinder(radius, length)
    cap = kernel.cylinder(head_radius, head, origin=(0.0, 0.0, length))
    return kernel.fuse(shaft, cap)
