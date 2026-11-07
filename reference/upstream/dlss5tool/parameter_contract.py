"""Mode-independent contract for the portable NR controls (not all effects).

Native catalogs and update-mode receipts remain authoritative for installed
backend capabilities. Different legacy defaults are explicit adapters, never
silently migrated settings. Importing this module starts no GUI/GPU work.
"""
from dataclasses import dataclass
from types import MappingProxyType

VERSION = 1
COMPOSITION_VERSION = 2


@dataclass(frozen=True)
class Parameter:
    key: str
    native: str
    label: str
    group: str
    default: float = 1.
    minimum: float = 0.
    maximum: float = 2.
    step: float = .01
    integer: bool = False
    native_default: float | None = None

    def descriptor(self, *, mode='local', name=None):
        from dlss5tool.i18n import tr
        return dict(name=name or self.key, label=tr(self.label),
                    default=self.default if mode == 'local' or self.native_default is None else self.native_default,
                    min=self.minimum, max=self.maximum, step=self.step, integer=self.integer,
                    group=self.group, contract_key=self.key)


_parameters = [
    Parameter('style', 'style', 'label.style', 'basic', 0, 0, 2, 1, True),
    Parameter('intensity', 'intensity', 'label.strength', 'basic', maximum=5),
    Parameter('local_tone', 'localToneStrength', 'label.local_tone', 'basic', maximum=5),
    Parameter('local_struct', 'localStructureStrength', 'label.local_structure', 'basic', maximum=5),
    Parameter('skin_struct', 'skinStructureStrength', 'label.skin_mask', 'basic', maximum=5, native_default=0),
    Parameter('output_mix', '', 'label.output_mix', 'output', maximum=5),
]
for _name in ('residualMultiplier', 'shadowStructureMultiplier', 'reflectionGlowMultiplier',
              'residualSaturation', 'residualLightness'):
    _parameters.append(Parameter('nr_residual.' + _name, _name, 'appearance.' + _name,
                                 'residual', minimum=1 if _name == 'residualMultiplier' else 0))
for _name in ('total', 'darken', 'brighten', 'color', 'luminance'):
    _parameters.append(Parameter('nr_quality.' + _name, _name, 'appearance.' + _name, 'quality'))
_parameters += [Parameter('nr_quality.feather', 'feather', 'appearance.feather', 'regions', 12, 0, 256, 1),
                Parameter('nr_quality.protection', 'protection', 'appearance.protection', 'regions', 0, 0, 1, 1, True)]
PARAMETERS = MappingProxyType({p.key: p for p in _parameters})
BASIC = tuple(p for p in _parameters if p.group == 'basic')
RESIDUAL = tuple(p.native for p in _parameters if p.group == 'residual')
QUALITY = tuple(p.native for p in _parameters if p.group == 'quality')
GROUPS = ('basic', 'residual', 'quality', 'regions')


def native_descriptor(item):
    """Shared names/groups; keep observed native defaults/ranges and unknown IDs.

The native catalog may be older or third-party. Never invent a field/capability
or widen its bounds. Original native descriptors remain available for diagnosis.
"""
    name = item['name']
    prefix = name[:6] if name.startswith(('pass2_', 'pass3_')) else ''
    field = name[len(prefix):]
    spec = next((p for p in _parameters if p.native == field and p.group in ('basic', 'residual')), None)
    if spec is None:
        return dict(item)
    result = dict(item, label=spec.descriptor()['label'], contract_key=spec.key, group=spec.group)
    result['min'] = max(spec.minimum, item.get('min', spec.minimum))
    result['max'] = min(spec.maximum, item.get('max', spec.maximum))
    return result


def update_policy(group, *, mode, native_mode=None):
    if mode == 'local':
        return 'recompute' if group == 'basic' else 'compose'
    if mode != 'realtime':
        raise ValueError('Unknown workbench mode')
    if group in ('quality', 'regions'):
        return 'live'
    # No receipt/unknown is not evidence of safe live application.
    return 'live' if native_mode == 1 else 'restart'


def feedback_state(*, error='', reason='', pending=False, unapplied=False, restart=False, active=True):
    if error:
        return 'invalid'
    if reason:
        return 'unsupported'
    if pending:
        return 'pending'
    if not active:
        return 'idle'
    if unapplied:
        return 'restart' if restart else 'dirty'
    return 'applied'


def invalidation(group):
    """Shared semantic layers; local caches/native history keep their owners."""
    return ('nr', 'composition', 'fg') if group == 'basic' else ('composition', 'fg')


def comparison_command(action):
    """Semantic command only; native presentation and local pixels stay separate."""
    if action not in ('original', 'processed', 'compare'):
        raise ValueError('Unknown comparison action')
    return action


def capabilities(*, mode, hdr=False, ready=None, nr=True):
    version = (ready or {}).get('appearance_hdr_residual_version', 0)
    hdr_fine = mode == 'local' or (type(version) is int and version >= 1)
    return dict(basic=bool(nr), residual=bool(nr and (not hdr or hdr_fine)),
                quality=bool(nr), regions=bool(nr), hdr=bool(hdr),
                residual_domain='linear-luma-chroma' if hdr else 'stored-rgb-hsl')


def diagnostic(*, mode, state, hdr=False, ready=None, nr=True, generation=None, reset=None, runtime=None):
    return dict(schema=VERSION, composition_version=COMPOSITION_VERSION, mode=mode,
                state=state, generation=generation, last_invalidation=reset,
                capabilities=capabilities(mode=mode, hdr=hdr, ready=ready, nr=nr),
                runtime=runtime, invalidation_rules={g: list(invalidation(g)) for g in GROUPS},
                comparison_commands=['original', 'processed', 'compare'])
