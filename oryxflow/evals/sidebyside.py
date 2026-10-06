"""Every arm's output for a case, side by side, as markdown a person can read.

A rate says how often; it never says what the outputs LOOK like, and a reader who
cannot see them cannot tell a real improvement from a scorer that rewards the wrong
thing. Rendered with Jinja from the stored outputs, so writing it calls no model.
Override the layout by passing your own template (a path or a string).
"""

import datetime
import json
import re
from pathlib import Path

DEFAULT_TEMPLATE = """\
# {{ name }}: outputs side by side

{{ summary }}
{% for case in cases %}

## {{ case.name }}

**Inputs**

{{ case.inputs | block }}
{% for arm in case.arms %}

### {{ arm.arm }}{% if arm.arm == baseline %} (baseline){% endif %}
{% for r in arm.reps %}

{% if arm.reps | length > 1 %}*rep {{ r.rep }}* {% endif %}\
{% if r.error %}**FAILED:** {{ r.error }}{% else %}\
{% for k, v in r.scores.items() %}`{{ k }}={{ v }}` {% endfor %}

{{ r.output | block }}{% endif %}
{% endfor %}
{% endfor %}
{% endfor %}
"""

_FENCE = '~~~~'


def _block(value):
    """A value as readable markdown: long text fenced, short scalars inline, a mapping
    field by field -- JSON escapes newlines, which makes prose unreadable."""
    if value is None:
        return '_(none)_'
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, str) and ('\n' in item or len(item) > 80):
                parts.append('**{}**\n\n{}\n{}\n{}'.format(key, _FENCE, item, _FENCE))
            else:
                parts.append('**{}**: `{}`'.format(key, _inline(item)))
        return '\n\n'.join(parts) if parts else '_(empty)_'
    if isinstance(value, str):
        return '{}\n{}\n{}'.format(_FENCE, value, _FENCE)
    return '{}json\n{}\n{}'.format(_FENCE, json.dumps(value, indent=2, default=str),
                                   _FENCE)


def _inline(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _columns(result):
    """The columns the verdict reads -- the ones whose disagreement matters."""
    out = []
    for metric, _role in result._metrics():
        col = getattr(metric, 'column', None)
        if isinstance(col, str) and col in result.df.columns and col not in out:
            out.append(col)
    return out


def _value(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if hasattr(v, 'item'):
        try:
            return v.item()
        except Exception:
            pass
    return v


def build(result, records, show_all=False):
    """``(cases, summary)`` for the template, from an EvalResult and its stored records.

    Default selection: cases where the arms DISAGREE on a column the verdict reads, plus
    any case that failed in some arm -- the cases a person actually needs to look at. A
    case's later repeats are expanded only where their outcome differs from rep 0.
    """
    df, arm_col = result.df, result.arm_col
    cols = _columns(result)
    scores = {}
    for row in df.to_dict('records'):
        key = (row.get(arm_col), row.get('source_case_name'), int(row.get('rep') or 0))
        scores[key] = {c: _value(row.get(c)) for c in cols}

    order, by_case = [], {}
    for arm in result.arms:
        for rec in records.get(arm, []) or []:
            name = rec.get('source_case_name')
            if name not in by_case:
                by_case[name] = {}
                order.append(name)
            by_case[name].setdefault(arm, []).append(rec)

    cases, shown = [], 0
    for name in order:
        arms = by_case[name]
        failed = any(r.get('error') for recs in arms.values() for r in recs)
        first = {a: scores.get((a, name, 0)) for a in arms}
        distinct = {json.dumps(v, sort_keys=True, default=str) for v in first.values()}
        disagree = len(distinct) > 1
        if not (show_all or failed or disagree):
            continue
        shown += 1
        entry = {'name': name, 'inputs': None, 'arms': []}
        for arm in result.arms:
            recs = sorted(arms.get(arm, []), key=lambda r: int(r.get('rep') or 0))
            if not recs:
                continue
            entry['inputs'] = entry['inputs'] if entry['inputs'] is not None \
                else recs[0].get('inputs')
            base = scores.get((arm, name, 0))
            reps = []
            for rec in recs:
                rep = int(rec.get('rep') or 0)
                sc = scores.get((arm, name, rep)) or {}
                if rep and not show_all and sc == base and not rec.get('error'):
                    continue
                reps.append({'rep': rep, 'scores': sc, 'error': rec.get('error'),
                             'output': rec.get('output')})
            entry['arms'].append({'arm': arm, 'reps': reps})
        cases.append(entry)

    total = len(order)
    if show_all:
        summary = 'All {} cases.'.format(total)
    else:
        what = ', '.join('`{}`'.format(c) for c in cols) or 'the scored columns'
        summary = ('{} of {} cases: those where the arms disagree on {}, plus any that '
                   'failed. Pass `all=True` for every case.'.format(shown, total, what))
    return cases, summary


def render(result, records, path=None, show_all=False, template=None):
    """Write the side-by-side markdown and return its path."""
    try:
        import jinja2
    except ImportError:
        raise ImportError("side_by_side() needs jinja2: pip install 'oryxflow[evals]'")
    if template is None:
        source = DEFAULT_TEMPLATE
    elif '\n' not in str(template) and Path(str(template)).exists():
        source = Path(str(template)).read_text(encoding='utf-8')
    else:
        source = str(template)
    env = jinja2.Environment(keep_trailing_newline=True, autoescape=False)
    env.filters['block'] = _block
    cases, summary = build(result, records, show_all=show_all)
    text = env.from_string(source).render(
        name=result.name, summary=summary, cases=cases, baseline=result.baseline,
        arms=result.arms)
    # trailing spaces and blank-line runs are template plumbing, not content
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    if path is None:
        from oryxflow.evals.sweep import _slug
        path = Path('results') / '{}-{}-side-by-side.md'.format(
            datetime.date.today().isoformat(), _slug(result.name))
    path = Path(path)
    if path.parent != Path(''):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path
