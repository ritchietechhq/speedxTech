"""Shared opt-in quality profiles for the application entry points."""
import os


def apply_quality_profile(state_manager):
    tone_reference = os.environ.get('SPEED_X_TONE_REFERENCE', 'source')
    if tone_reference not in ('source', 'target'):
        raise ValueError('Tone reference must be source or target')
    os.environ['SPEED_X_TONE_REFERENCE'] = tone_reference
    profile = os.environ.get('SPEED_X_QUALITY_PROFILE', 'live')
    mode = os.environ.get('SPEED_X_SKIN_MATCH', 'masked_lab')
    blend = os.environ.get('SPEED_X_BLEND', 'alpha')
    strength = float(os.environ.get('SPEED_X_SKIN_STRENGTH', '0.8'))
    if profile not in ('live', 'offline') or mode not in ('masked_lab', 'legacy', 'off'):
        raise ValueError('Invalid quality profile or skin matching mode')
    if blend not in ('alpha', 'multiband', 'poisson') or (profile == 'live' and blend != 'alpha'):
        raise ValueError('Live requires alpha blending; offline supports alpha, multiband, poisson')
    if not 0 <= strength <= 1:
        raise ValueError('Skin strength must be between zero and one')
    shading_strength = float(os.environ.get('SPEED_X_SHADING_STRENGTH', '0.45'))
    texture_strength = float(os.environ.get('SPEED_X_TEXTURE_STRENGTH', '0.35'))
    if not (0 <= shading_strength <= 1 and 0 <= texture_strength <= 1):
        raise ValueError('Shading and texture strength must be between zero and one')
    os.environ['SPEED_X_SHADING_STRENGTH'] = str(shading_strength)
    os.environ['SPEED_X_TEXTURE_STRENGTH'] = str(texture_strength)
    model = os.environ.get('SPEED_X_SWAP_MODEL', 'hyperswap_1a_256')
    if model not in ('hyperswap_1a_256', 'hyperswap_1b_256', 'hyperswap_1c_256'):
        raise ValueError('Quality profiles support HyperSwap 1a/1b/1c')
    for key, value in {
        'face_swapper_model': model,
        'face_swapper_pixel_boost': '256x256' if profile == 'live' else '512x512',
        'face_mask_types': ['box', 'occlusion', 'region'],
        'face_mask_regions': ['skin', 'left-eyebrow', 'right-eyebrow', 'left-eye', 'right-eye',
                              'nose', 'mouth', 'upper-lip', 'lower-lip'],
    }.items():
        state_manager.init_item(key, value)
    os.environ['SPEED_X_SKIN_MATCH'] = mode
    return {'profile': profile, 'skin_match': mode, 'strength': strength, 'blend': blend, 'model': model,
            'shading_strength': shading_strength, 'texture_strength': texture_strength}
