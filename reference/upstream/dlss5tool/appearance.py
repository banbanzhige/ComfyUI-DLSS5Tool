"""Portable appearance contract, independent of GUI, capture and model workers."""
from copy import deepcopy
import math
from dlss5tool.i18n import tr
from dlss5tool.parameter_contract import PARAMETERS, QUALITY, RESIDUAL, GROUPS

VERSION = 1
QUALITY_FIELDS = QUALITY
DEFAULT_QUALITY = {k: PARAMETERS['nr_quality.' + k].default for k in QUALITY_FIELDS} | {
    'protection': False, 'feather': PARAMETERS['nr_quality.feather'].default, 'regions': []}
RESIDUAL_FIELDS = RESIDUAL
DEFAULT_RESIDUAL = {k: PARAMETERS['nr_residual.' + k].default for k in RESIDUAL_FIELDS}
DEFAULTS = {'nr_quality': DEFAULT_QUALITY, 'nr_residual': DEFAULT_RESIDUAL}


def number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(tr('realtime.error.quality_value', field=name))
    return float(value)


def quality(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - DEFAULT_QUALITY.keys():
        raise ValueError(tr('realtime.error.quality'))
    result = deepcopy(DEFAULT_QUALITY) | deepcopy(value)
    for key in (*QUALITY_FIELDS, 'feather'):
        spec = PARAMETERS['nr_quality.' + key]
        result[key] = number(result[key], spec.minimum, spec.maximum, key)
    if type(result['protection']) is not bool:
        raise ValueError(tr('realtime.error.regions'))
    rects = result['regions']
    if not isinstance(rects, list) or len(rects) > 4:
        raise ValueError(tr('realtime.error.regions'))
    for r in rects:
        if not isinstance(r, list) or len(r) != 4:
            raise ValueError(tr('realtime.error.rectangles'))
        for v in r:
            number(v, 0, 1, 'region')
        if r[2] <= r[0] or r[3] <= r[1]:
            raise ValueError(tr('realtime.error.rectangles'))
    return result


def residual(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - DEFAULT_RESIDUAL.keys():
        raise ValueError('Invalid NR residual fields')
    return {k: number(value.get(k, PARAMETERS['nr_residual.' + k].default),
                      PARAMETERS['nr_residual.' + k].minimum, PARAMETERS['nr_residual.' + k].maximum, k)
            for k in RESIDUAL_FIELDS}


def settings(values, *, strict=True):
    result = {}
    for key, validate in (('nr_quality', quality), ('nr_residual', residual)):
        try:
            result[key] = validate(values.get(key))
        except (ValueError, TypeError):
            if strict:
                raise
            result[key] = validate()
    return result


def active(values):
    q, r = quality(values.get('nr_quality')), residual(values.get('nr_residual'))
    return (any(q[k] != 1 for k in QUALITY_FIELDS) or
            bool(q['protection'] and q['regions']) or any(v != 1 for v in r.values()))


def source_only(values):
    """Whether the residual formula is identically the composition input.

    This is algebraic, not a tolerance or a colour-profile-specific shortcut.
    Validate *all* look fields even when a zero factor makes them unused.
    """
    q = settings(values)['nr_quality']
    return (q['total'] == 0 or q['darken'] == q['brighten'] == 0
            or q['color'] == q['luminance'] == 0)


def basic_zero(values):
    """Exact native zero-control contract; one zero slider is insufficient."""
    keys = ('intensity', 'local_tone', 'local_struct', 'skin_struct')
    return (all(type(values.get(k)) in (int, float) and values[k] == 0 for k in keys)
            and values.get('use_auto_mask', False) in (False, 0)
            and values.get('style', 0) in (0, 1, 2)
            and values.get('dlss_runtime', '') in ('', '__bundled__'))


def enhancement_bypass(values):
    """Shared SDR/HDR execution policy: source, native-zero, or no bypass.

    Raw-cache producers explicitly carry _nr_bypass because their neutralised
    look otherwise loses the consumer's zero-effect intent. View overlays need
    NR even at zero legacy mix; quality-zero still removes NR in every view.
    """
    zero_residual = source_only(values)
    mix = number(values.get('output_mix', 1), 0, 5, 'output_mix')
    view = values.get('output_view', 0)
    if type(view) is not int or view not in (0, 1, 2):
        raise ValueError('Unsupported output view')
    inherited = values.get('_nr_bypass')
    if inherited not in (None, 'source', 'native-zero'):
        raise ValueError('Invalid raw enhancement stage')
    if inherited is not None:
        return inherited
    if zero_residual or (view == 0 and mix == 0):
        return 'source'
    if basic_zero(values):
        return 'native-zero'
    return None


def enhancement_required(values):
    return enhancement_bypass(values) is None


def bypass_frame(frame, reason):
    """Owned zero-effect output, preserving the existing native value contract.

    A zero-control NR call still clamps float RGB to [0, 1], including scRGB.
    A *post-composition* zero instead returns the untouched composition input.
    Neither operation changes alpha or quantises HDR to an integer format.
    """
    import numpy as np
    if reason not in ('source', 'native-zero'):
        raise ValueError('No enhancement bypass selected')
    output = frame.copy()
    if reason == 'native-zero' and output.dtype.kind == 'f':
        np.clip(output[..., :3], 0, 1, out=output[..., :3])
    return output


def raw_settings(values):
    """Neutral raw pixels, with the consumer's execution identity retained."""
    return dict(values, **deepcopy(DEFAULTS), _nr_bypass=enhancement_bypass(values))


def preflight(values, *, hdr=False):
    # HDR fine controls are linear-light residual operations, not SDR HSL.
    return settings(values)
