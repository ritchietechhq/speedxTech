"""Optional offline compositing on an already warped face ROI."""
import cv2
import numpy as np


def blend_face(target, source, mask, mode='alpha'):
    mask = np.asarray(mask, dtype=np.float32).clip(0, 1)
    if mask.ndim == 3:
        mask = mask[..., 0]
    alpha = mask[..., None]
    if mode == 'alpha':
        return (target * (1 - alpha) + source * alpha).clip(0, 255).astype(target.dtype)
    if mode == 'poisson':
        binary = (mask > 0.5).astype(np.uint8) * 255
        x, y, width, height = cv2.boundingRect(binary)
        if width < 3 or height < 3:
            return blend_face(target, source, mask)
        result = cv2.seamlessClone(source.astype(np.uint8), target.astype(np.uint8), binary,
                                   (x + width // 2, y + height // 2), cv2.NORMAL_CLONE)
    elif mode == 'multiband':
        levels = min(4, max(0, int(np.log2(min(target.shape[:2]))) - 2))
        sources, targets, masks = [source.astype(np.float32)], [target.astype(np.float32)], [mask]
        for _ in range(levels):
            sources.append(cv2.pyrDown(sources[-1]))
            targets.append(cv2.pyrDown(targets[-1]))
            masks.append(cv2.pyrDown(masks[-1]))
        result = sources[-1] * masks[-1][..., None] + targets[-1] * (1 - masks[-1][..., None])
        for index in reversed(range(levels)):
            size = sources[index].shape[1::-1]
            source_detail = sources[index] - cv2.pyrUp(sources[index + 1], dstsize=size)
            target_detail = targets[index] - cv2.pyrUp(targets[index + 1], dstsize=size)
            weight = masks[index][..., None]
            result = cv2.pyrUp(result, dstsize=size) + source_detail * weight + target_detail * (1 - weight)
    else:
        raise ValueError('Unknown blending mode')
    # Never alter target pixels outside the selected face.
    return np.where(alpha > 0, result, target).clip(0, 255).astype(target.dtype)
