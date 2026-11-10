"""High-frequency skin texture preservation and directional lighting transfer."""
import cv2
import numpy as np


def extract_high_frequency_texture(image, sigma=2.0):
    """Extract spatial high-frequency micro-texture (skin pores, fine stubble, grain)."""
    image_f = np.asarray(image, dtype=np.float32)
    low_freq = cv2.GaussianBlur(image_f, (0, 0), sigmaX=float(sigma), sigmaY=float(sigma))
    high_freq = image_f - low_freq
    return high_freq


def inject_skin_texture(swapped_image, texture_high, skin_mask, strength=0.35):
    """Inject natural skin pore texture into the neural swapped image strictly within the skin mask."""
    if strength <= 0:
        return swapped_image

    strength = float(np.clip(strength, 0.0, 1.0))
    swapped_f = np.asarray(swapped_image, dtype=np.float32)

    # Smooth the skin mask boundary to prevent sharp texture cutoffs
    alpha = cv2.GaussianBlur(skin_mask.astype(np.float32), (0, 0), 2.0).clip(0.0, 1.0)
    if alpha.ndim == 2:
        alpha = alpha[..., None]

    textured = swapped_f + (texture_high * (strength * alpha))
    return np.clip(textured, 0.0, 255.0).astype(np.uint8)


def transfer_directional_shading(target_crop, swapped_crop, skin_mask, strength=0.5):
    """Transfer directional lighting gradients and facial shadows from target to swapped crop."""
    if strength <= 0:
        return swapped_crop

    strength = float(np.clip(strength, 0.0, 1.0))

    # Convert to LAB for luminance separation
    target_lab = cv2.cvtColor(target_crop.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    swapped_lab = cv2.cvtColor(swapped_crop.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)

    target_l = target_lab[..., 0]
    swapped_l = swapped_lab[..., 0]

    # Blur luminance to capture macro lighting gradient (ignoring micro features)
    ksize = max(15, (target_crop.shape[0] // 16) * 2 + 1)
    target_l_smooth = cv2.GaussianBlur(target_l, (ksize, ksize), 0)
    swapped_l_smooth = cv2.GaussianBlur(swapped_l, (ksize, ksize), 0)

    # Valid skin region for computing relative shading
    valid = skin_mask > 0.5
    if np.count_nonzero(valid) < 64:
        return swapped_crop

    # Relative luminance deviation on the target
    target_mean_l = float(np.mean(target_l_smooth[valid]))
    target_shading_delta = target_l_smooth - target_mean_l

    # Apply bounded shading delta
    bounded_delta = np.clip(target_shading_delta * strength, -35.0, 35.0)

    alpha = cv2.GaussianBlur(skin_mask.astype(np.float32), (0, 0), 3.0).clip(0.0, 1.0)
    swapped_lab[..., 0] = np.clip(swapped_l + (bounded_delta * alpha), 0.0, 255.0)

    shading_matched = cv2.cvtColor(swapped_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)
    return shading_matched
