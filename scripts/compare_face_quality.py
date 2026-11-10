#!/usr/bin/env python3
"""Compare aligned saved face crops; does not generate swaps or load identities."""
import argparse
import json
import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, help='Original aligned target crop')
    parser.add_argument('--mask', required=True, help='Visible skin mask, white foreground')
    parser.add_argument('--candidates', nargs='+', required=True)
    parser.add_argument('--output', required=True, help='Comparison grid PNG path')
    args = parser.parse_args()
    reference = cv2.imread(args.reference)
    mask = cv2.imread(args.mask, cv2.IMREAD_GRAYSCALE)
    if reference is None or mask is None or mask.shape != reference.shape[:2]:
        parser.error('Reference and mask must be readable and aligned')
    valid = mask > 230
    if valid.sum() < 32:
        parser.error('At least 32 visible skin pixels required')
    target_lab = cv2.cvtColor(reference.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    panels = [reference.copy()]
    report = []
    for path in args.candidates:
        image = cv2.imread(path)
        if image is None or image.shape != reference.shape:
            parser.error('Candidates must be readable and aligned at the reference resolution')
        lab = cv2.cvtColor(image.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
        # CIE76 is a diagnostic proxy, not CIEDE2000 or a perceptual quality score.
        error = np.linalg.norm(lab - target_lab, axis=-1)[valid]
        report.append({'candidate': path, 'skin_delta_e76_median': float(np.median(error)),
                       'skin_delta_e76_p95': float(np.percentile(error, 95))})
        panels.append(image.copy())
    height = reference.shape[0]
    grid = np.hstack([cv2.copyMakeBorder(panel, 32, 0, 0, 0, cv2.BORDER_CONSTANT) for panel in panels])
    width = reference.shape[1]
    for index, label in enumerate(['Target'] + [f'Candidate {i + 1}' for i in range(len(report))]):
        cv2.putText(grid, label, (index * width + 4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    if not cv2.imwrite(args.output, grid):
        parser.error('Unable to write comparison grid')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
