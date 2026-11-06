"""Reproduce vector-marker extraction from the public Ohtake (2007) PDF.

Optional preparation dependency: PyMuPDF. It is not needed to run the solver.
The output is digitized graph data, not the authors' original numerical table.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json


def main():
    import fitz
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    doc = fitz.open(args.pdf)
    page = doc[2]  # Printed p.441, Fig.5, one triangular-marker series (Re=30000).
    left, right = 342.98333740234375, 537.49609375
    top, bottom = 600.6068115234375, 718.6524658203125
    points = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if not (drawing["type"] == "fs" and len(drawing["items"]) == 3
                and left < rect.x0 and rect.x1 < right + 1
                and top < rect.y0 and rect.y1 < bottom):
            continue
        cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        if 348 < cx < 356 and 608 < cy < 614:  # Legend sample, not a data point.
            continue
        alpha = -5 + (cx - left) * 25 / (right - left)
        cl = -0.2 + (bottom - cy) / (bottom - top)
        points.append((alpha, cl))
    points.sort()
    if len(points) != 115:
        raise RuntimeError(f"Unexpected PDF structure: found {len(points)} markers, expected 115")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["reynolds", "alpha_deg", "cl", "digitization_alpha_bound_deg", "digitization_cl_bound"])
        for alpha, cl in points:
            writer.writerow([30000, f"{alpha:.5f}", f"{cl:.6f}", 0.12, 0.008])
    provenance = {"doi": "10.2322/jjsass.55.439", "printed_page": 441, "figure": 5,
                  "pdf_sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
                  "method": "Vector triangular marker bounding-box centers; affine calibration from plot frame",
                  "axis_frame_pdf_points": [left, top, right, bottom],
                  "axis_ranges": {"alpha_deg": [-5, 20], "cl": [-0.2, 0.8]},
                  "points": len(points),
                  "digitization_bounds": {"alpha_deg": 0.12, "cl": 0.008},
                  "bounds_meaning": "Approximate half-marker-width bounds; NOT tunnel measurement uncertainties",
                  "status": "digitized_published_graph; not an original author-supplied dataset"}
    args.output.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(f"Extracted {len(points)} markers to {args.output}")


if __name__ == "__main__":
    main()
