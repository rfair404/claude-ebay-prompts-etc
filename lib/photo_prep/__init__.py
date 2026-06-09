"""Modular photo-prep sub-steps.

Each module in this package is a single-purpose, independently runnable
CLI. Sub-steps are parallelized across images and can be chained or run
in any order. Sub-step 1 is `strip_exif`; future siblings will add
rotation, deskew, crop, frame, etc. as they're scoped.
"""
