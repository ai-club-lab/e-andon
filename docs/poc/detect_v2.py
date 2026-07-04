"""CV anomaly detection v2 — adds lattice/spacing-gap signal.
Signals: (1) cy offset from row median, (2) rotation deviation,
(3) spacing gap ~2x median => a part left its slot (shift-up merges w/ rail).
"""
import cv2, numpy as np
VIDEO = "video/factory_01.mov"
OUT = "/private/tmp/claude-501/-Users-sotarosuzuki-------gcp-agentai-2026/1b991929-0eaa-4f7b-a061-7a9388d64a31/scratchpad"
ROI_Y0, ROI_Y1 = 250, 430
AREA_MIN, AREA_MAX = 1800, 8000
ASPECT_MAX = 1.6
T_OFF, T_ANG, GAP_R = 12.0, 12.0, 1.5

def norm_angle(w, h, ang):
    a = ang + 90 if w < h else ang
    while a > 45: a -= 90
    while a < -45: a += 90
    return a

def find_parts(frame):
    roi = frame[ROI_Y0:ROI_Y1]
    gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    parts = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < AREA_MIN or area > AREA_MAX:
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(c)
        if min(w, h) == 0 or max(w, h) / min(w, h) > ASPECT_MAX:
            continue
        parts.append({"cx": float(cx), "cy": float(cy + ROI_Y0),
                      "angle": float(norm_angle(w, h, ang))})
    parts.sort(key=lambda p: p["cx"])
    return parts

def analyze(parts):
    if len(parts) < 4:
        return None
    ys = np.array([p["cy"] for p in parts])
    angs = np.array([p["angle"] for p in parts])
    cxs = np.array([p["cx"] for p in parts])
    base_y, base_ang = float(np.median(ys)), float(np.median(angs))
    gaps = np.diff(cxs)
    med_gap = float(np.median(gaps))
    flags = []
    for i, p in enumerate(parts):
        why = []
        if abs(p["cy"] - base_y) > T_OFF:
            why.append(f"offset {p['cy']-base_y:+.0f}px")
        if abs(p["angle"] - base_ang) > T_ANG:
            why.append(f"rot {p['angle']-base_ang:+.0f}deg")
        if why:
            flags.append({"cx": p["cx"], "cy": p["cy"], "why": ", ".join(why)})
    gap_flags = []
    for i, g in enumerate(gaps):
        if g > GAP_R * med_gap:
            xmid = (cxs[i] + cxs[i + 1]) / 2
            gap_flags.append({"cx": float(xmid), "cy": base_y,
                              "why": f"missing/displaced (gap {g/med_gap:.1f}x)"})
    return {"base_y": base_y, "base_ang": base_ang, "med_gap": med_gap,
            "n": len(parts), "flags": flags, "gap_flags": gap_flags}

def main():
    cap = cv2.VideoCapture(VIDEO)
    fi, saved = -1, 0
    anomaly_frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        fi += 1
        parts = find_parts(frame)
        res = analyze(parts)
        if not res:
            continue
        allf = res["flags"] + res["gap_flags"]
        if allf:
            anomaly_frames.append(fi)
            if fi % 4 == 0 and saved < 10:
                img = frame.copy()
                cv2.line(img, (0, int(res["base_y"])), (img.shape[1], int(res["base_y"])), (0, 180, 0), 1)
                for p in parts:
                    cv2.circle(img, (int(p["cx"]), int(p["cy"])), 4, (0, 180, 0), -1)
                for f in allf:
                    cv2.circle(img, (int(f["cx"]), int(f["cy"])), 10, (0, 0, 255), 2)
                    cv2.putText(img, f["why"], (int(f["cx"]) - 60, int(f["cy"]) + 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
                cv2.imwrite(f"{OUT}/v2_{fi:03d}.jpg", img)
                saved += 1
    cap.release()
    # summarize contiguous anomaly runs
    runs = []
    for f in anomaly_frames:
        if runs and f - runs[-1][1] <= 2:
            runs[-1][1] = f
        else:
            runs.append([f, f])
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if False else fi + 1
    print(f"total frames={total}")
    print(f"anomaly frames={len(anomaly_frames)}  runs={[(a,b) for a,b in runs]}")

if __name__ == "__main__":
    main()
