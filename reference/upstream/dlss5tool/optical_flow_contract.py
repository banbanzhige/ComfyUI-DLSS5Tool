"""Torch-free shared optical-flow semantics, not a shared GPU session.

CUDA and D3D11 use S10.5 source-pixel motion and clamp-edge bilinear grid
expansion. Direction, cost/confidence and quality presets remain adapter-owned.
"""
S10_5_SCALE = 32.0
GRIDS = (1, 2, 4)
REALTIME_METHODS = {1: ('amdoF', 'AMD OF'), 2: ('nvof', 'NVOF')}


def align_size(width, height):
    if width <= 0 or height <= 0:
        raise ValueError('Positive dimensions required')
    return ((int(width) + 15) // 16 * 16, (int(height) + 15) // 16 * 16)


def output_shape(width, height, grid=4):
    if grid not in GRIDS or width <= 0 or height <= 0:
        raise ValueError('Positive dimensions and NVOFA grid 1, 2, or 4 required')
    return (int(height) + grid - 1) // grid, (int(width) + grid - 1) // grid


def descriptor(api):
    if api not in ('cuda', 'd3d11'):
        raise ValueError('Unknown NVOFA adapter')
    return {'schema': 1, 'provider': 'nvofa', 'api': api, 'torch_required': False,
            'vector_units': 'source_pixels', 'fixed_point_scale': S10_5_SCALE,
            'sampling': 'bilinear_clamp', 'session_sharing': False,
            'quality_namespace': 'local_slow' if api == 'cuda' else 'realtime_1_to_5'}
