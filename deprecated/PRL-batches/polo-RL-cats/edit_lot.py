import sys, os, glob, math, argparse, shutil, subprocess, tempfile
from multiprocessing import Pool
from PIL import Image
import numpy as np
import cv2
import pytesseract

SCALE_BEFORE = 2500      # working resolution for deskew/crop/pad
OSD_SIZE     = 400       # thumbnail size for Tesseract OSD — fast, still reliable
SQUARE_SIZE  = 1600
NATURAL_MAX_EDGE = 2400
ANGLE_CAP    = 5.0
CROP_IF_COVERAGE_BELOW = 0.60
CROP_IF_COVERAGE_ABOVE = 0.30
OSD_CONF_TRUST = 1.0
WORKERS = 2
BG_RGB  = (255, 255, 255)


def scale_down(arr, max_edge):
    h, w = arr.shape[:2]
    if max(h, w) <= max_edge:
        return arr
    s = max_edge / max(h, w)
    return cv2.resize(arr, (int(w*s), int(h*s)), interpolation=cv2.INTER_AREA)


def rotate_array(arr, deg):
    if deg == 0:   return arr
    if deg == 90:  return cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180: return cv2.rotate(arr, cv2.ROTATE_180)
    if deg == 270: return cv2.rotate(arr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return arr

def osd_check(rgb_arr):
    """Run Tesseract OSD on a small thumbnail — fast and reliable for orientation."""
    try:
        thumb = scale_down(rgb_arr, OSD_SIZE)
        gray  = cv2.cvtColor(thumb, cv2.COLOR_RGB2GRAY)
        osd   = pytesseract.image_to_osd(
            Image.fromarray(gray),
            output_type=pytesseract.Output.DICT,
            config="--psm 0"
        )
        return int(osd.get("rotate", 0)), float(osd.get("orientation_conf", 0))
    except Exception:
        return 0, 0.0

def upright_via_osd(arr):
    best_score    = 0.0
    best_arr      = arr
    best_decision = "trust-raw(OSD-silent)"
    for deg in (0, 90, 180, 270):
        candidate = rotate_array(arr, deg)
        rot_needed, conf = osd_check(candidate)
        score = conf if rot_needed == 0 else 0.0
        if score > best_score:
            best_score    = score
            best_arr      = candidate
            best_decision = "OSD-rot{}(c={:.1f})".format(deg, conf)
    if best_score >= OSD_CONF_TRUST:
        return best_arr, best_decision
    return arr, "trust-raw(no-OSD-signal)"


def detect_skew(gray):
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi/180, 200)
    if lines is None: return 0.0
    angles = []
    for line in lines[:80]:
        rho, theta = line[0]
        deg = math.degrees(theta) - 90
        if -ANGLE_CAP <= deg <= ANGLE_CAP:
            angles.append(deg)
        elif 85 <= abs(deg) <= 95:
            d = deg - 90 if deg > 0 else deg + 90
            if -ANGLE_CAP <= d <= ANGLE_CAP:
                angles.append(d)
    return float(np.median(angles)) if angles else 0.0


def _bbox_from_contours(contours, w, h, min_area_frac):
    big = [c for c in contours if cv2.contourArea(c) > min_area_frac*w*h]
    if not big: return None
    xs, ys, xe, ye = w, h, 0, 0
    for c in big:
        x, y, cw, ch = cv2.boundingRect(c)
        xs = min(xs, x); ys = min(ys, y); xe = max(xe, x+cw); ye = max(ye, y+ch)
    return (xs, ys, xe, ye)

def subject_bbox(gray, margin):
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    candidates = []
    for invert in (True, False):
        flag = cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU if invert else cv2.THRESH_BINARY+cv2.THRESH_OTSU
        _, th = cv2.threshold(blur, 0, 255, flag)
        kernel = np.ones((15, 15), np.uint8)
        closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        bb = _bbox_from_contours(contours, w, h, 0.02)
        if bb: candidates.append(bb)
    edges = cv2.Canny(blur, 20, 80)
    edges_closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((25,25), np.uint8))
    contours, _ = cv2.findContours(edges_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bb = _bbox_from_contours(contours, w, h, 0.05)
    if bb: candidates.append(bb)
    if not candidates:
        return (0, 0, w, h, False, 1.0)
    xs = min(b[0] for b in candidates)
    ys = min(b[1] for b in candidates)
    xe = max(b[2] for b in candidates)
    ye = max(b[3] for b in candidates)
    bw, bh = xe-xs, ye-ys
    if bw == 0 or bh == 0:
        return (0, 0, w, h, False, 1.0)
    coverage = (bw*bh) / (w*h)
    if coverage >= CROP_IF_COVERAGE_BELOW or coverage < CROP_IF_COVERAGE_ABOVE:
        return (0, 0, w, h, False, coverage)
    mx, my = int(bw*margin), int(bh*margin)
    return (max(0,xs-mx), max(0,ys-my), min(w,xe+mx), min(h,ye+my), True, coverage)

def square_pad(arr, size, bg):
    h, w = arr.shape[:2]
    s = size / max(h, w)
    new_w, new_h = int(w*s), int(h*s)
    resized = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((size, size, 3), bg, dtype=np.uint8)
    x_off = (size - new_w) // 2
    y_off  = (size - new_h) // 2
    canvas[y_off:y_off+new_h, x_off:x_off+new_w] = resized
    return canvas

def fit_long_edge(arr, max_edge):
    h, w = arr.shape[:2]
    if max(h, w) <= max_edge: return arr
    s = max_edge / max(h, w)
    return cv2.resize(arr, (int(w*s), int(h*s)), interpolation=cv2.INTER_AREA)


def process(args):
    path, dst_dir, style, fast = args
    margin = 0.10 if style == "paper" else 0.04
    try:
        img = Image.open(path).convert("RGB")
        arr = np.array(img)
        arr = scale_down(arr, SCALE_BEFORE)
        if fast:
            arr, decision = arr, "trust-raw(fast)"
        else:
            arr, decision = upright_via_osd(arr)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        angle = detect_skew(gray)
        if abs(angle) > 0.2:
            h, w = arr.shape[:2]
            M = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
            arr = cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        x0, y0, x1, y1, cropped, coverage = subject_bbox(gray, margin)
        arr = arr[y0:y1, x0:x1]
        if style == "paper":
            arr = square_pad(arr, SQUARE_SIZE, BG_RGB)
        else:
            arr = fit_long_edge(arr, NATURAL_MAX_EDGE)
        out_path = os.path.join(dst_dir, os.path.basename(path))
        Image.fromarray(arr).save(out_path, "JPEG", quality=90, optimize=True)
        return (path, decision, angle, cropped, coverage, "ok")
    except Exception as e:
        return (path, None, None, None, None, "err: {}".format(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--style",      choices=("paper", "natural"), default="paper")
    ap.add_argument("--chunk-size", type=int, default=4,
                    help="Files per bash call (default 4). Call repeatedly to resume.")
    ap.add_argument("--fast",       action="store_true",
                    help="Skip OSD (trust raw pixel orientation).")
    ap.add_argument("--finish",     action="store_true",
                    help="Move completed staging dir to final dst.")
    args = ap.parse_args()

    # Persistent staging dir lives next to dst — survives across bash calls.
    # Direct Python writes to the Cowork FUSE mount fail; always stage here.
    # Never rm files from final dst — rm creates FUSE whiteouts blocking writes.
    final_dst = os.path.abspath(args.dst)
    staging   = final_dst + ".staging"
    os.makedirs(staging, exist_ok=True)

    all_files = sorted(
        glob.glob(os.path.join(args.src, "*.jpg")) +
        glob.glob(os.path.join(args.src, "*.jpeg"))
    )
    if not all_files:
        print("No images in {}".format(args.src))
        return

    done      = {os.path.basename(f) for f in glob.glob(os.path.join(staging, "*.jpg"))}
    remaining = [f for f in all_files if os.path.basename(f) not in done]

    if args.finish or not remaining:
        n = len(glob.glob(os.path.join(staging, "*.jpg")))
        print("Staging: {}/{} files. Moving to {} ...".format(n, len(all_files), final_dst))
        if os.path.exists(final_dst):
            print("ERROR: dst already exists — use a fresh dir name to avoid FUSE whiteouts.")
            return
        shutil.move(staging, final_dst)
        print("Done -> {}".format(final_dst))
        return

    chunk = remaining[:args.chunk_size] if args.chunk_size > 0 else remaining
    mode  = "fast/no-OSD" if args.fast else "OSD@{}px".format(OSD_SIZE)
    print("Processing {}/{} files (chunk {}) — {} — style={}".format(
        len(chunk), len(all_files), args.chunk_size, mode, args.style))

    work = [(f, staging, args.style, args.fast) for f in chunk]
    with Pool(WORKERS) as p:
        results = p.map(process, work)

    ok_count = 0
    for path, decision, angle, cropped, coverage, status in results:
        bn = os.path.basename(path)
        if status == "ok":
            ok_count += 1
            print("  {}: {}, skew={:+.1f}, cov={:.0%}, crop={}".format(
                bn, decision, angle, coverage, "Y" if cropped else "n"))
        else:
            print("  {}: {}".format(bn, status))

    done_now  = len(done) + ok_count
    remaining_after = len(all_files) - done_now
    if remaining_after > 0:
        print("\n{}/{} staged. Run again to continue.".format(done_now, len(all_files)))
    else:
        print("\nAll {} staged. Run with --finish to move to final dst.".format(len(all_files)))


if __name__ == "__main__":
    main()
