"""Relationships derived only from the caller's enabled, visible evidence."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from ipaddress import ip_address
from itertools import combinations
from typing import NamedTuple
from urllib.parse import urlencode


_MAX_AGE = timedelta(hours=1)
_MAX_SKEW = timedelta(minutes=5)
_PATHS = {'linux': '/machines', 'incus': '/incus', 'unifi': '/unifi'}
_ADDRESS_SECTIONS = {
    'linux': (('addresses', 'asset'),),
    'incus': (('guest_addresses', 'name'),),
    'unifi': (('managed_devices', 'id'), ('connected_clients', 'id')),
}


class _Subject(NamedTuple):
    provider: str
    source: str
    scope: str
    name: str


class _Sighting(NamedTuple):
    stamp: datetime
    label: str


class _Instance(NamedTuple):
    stamp: datetime
    running: bool


def _text(value):
    return isinstance(value, str) and value.isprintable() and value == value.strip()


def _instant(value):
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
            if stamp.utcoffset() is not None:
                return stamp.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            pass
    return None


def _address(value):
    if not isinstance(value, str) or '%' in value:
        return None
    try:
        ip = ip_address(value)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_link_local or ip.is_loopback or ip.is_multicast:
        return None
    address = str(ip)
    return address if address != '255.255.255.255' else None


def _captures(entry, section):
    state = entry['state'] or {}
    history = state.get('sections', {}).get(section, {})
    for outcome in ('complete', 'partial'):
        if capture := history.get(outcome):
            value = capture['timestamp']
            current = value == history.get('latest') and state.get('outcome') != 'Collection failed'
            yield capture['records'], _instant(value), current


def _subject(entry, name):
    return _Subject(entry['provider'], entry['id'], entry['scope'], name)


def _instances(entries, now):
    by_placement = defaultdict(dict)
    for entry in entries:
        if entry['provider'] != 'incus':
            continue
        for records, stamp, current in _captures(entry, 'instances'):
            if not current or stamp is None or stamp > now:
                continue
            for row in records:
                name = row.get('name')
                state = row.get('state')
                if _text(name) and _text(state) and row.get('kind') in ('container', 'virtual-machine'):
                    by_placement[entry['scope'], name][_subject(entry, name)] = _Instance(
                        stamp, state.casefold() == 'running',
                    )
    return by_placement


def _placement(entry):
    value = entry.get('placement')
    if entry['provider'] != 'linux' or not isinstance(value, dict):
        return None
    scope, name = value.get('scope'), value.get('name')
    return (scope, name) if _text(scope) and _text(name) else None


def _address_groups(entries, instances, now):
    groups = defaultdict(dict)
    for entry in entries:
        domain = entry.get('network_domain')
        if not _text(domain):
            continue
        for section, identity in _ADDRESS_SECTIONS[entry['provider']]:
            for rows, stamp, current in _captures(entry, section):
                fresh = current and stamp is not None and timedelta(0) <= now - stamp <= _MAX_AGE
                for row in rows:
                    if (ip := _address(row.get('ip_address'))) is None:
                        continue
                    name = row.get(identity)
                    subject = _subject(entry, name if _text(name) else '')
                    subjects = groups[domain, ip]
                    # Retained rows can reveal reuse, but cannot create a candidate.
                    previous = subjects.setdefault(subject, None)
                    if not (fresh and subject.name):
                        continue
                    if subject.provider == 'incus':
                        instance = instances.get((subject.scope, name), {}).get(subject)
                        if instance is None or not instance.running:
                            continue
                    label = row.get('name') if subject.provider == 'unifi' else name
                    if previous is None or previous.stamp < stamp:
                        subjects[subject] = _Sighting(stamp, label if _text(label) else name)
    return groups


def _link(subject, label):
    query = urlencode({'source': subject.source, 'q': subject.name}, errors='replace')
    return (f'<a href="{_PATHS[subject.provider]}?{escape(query)}">{escape(label)}</a>'
            f' <small>({escape(subject.source)} · {escape(subject.scope)})</small>')


def _time(stamp):
    value = stamp.isoformat()
    return f'<time datetime="{value}">{value}</time>'


def _selected(query, left, right, *terms):
    source = query.get('source')
    if source and source not in (left.source, right.source):
        return False
    words = query.get('q', '').strip().casefold()
    return not words or any(words in value.casefold() for value in (*left, *right, *terms))


def _placements(entries, instances, query):
    for entry in entries:
        matches = instances.get(_placement(entry), {})
        if len(matches) != 1:
            continue
        (guest, instance), = matches.items()
        machine = _subject(entry, entry['scope'])
        if _selected(query, machine, guest):
            yield (f'<li>{_link(machine, machine.name)} → {_link(guest, guest.name)}. '
                   f'Operator placement; Incus instance observed at {_time(instance.stamp)}.</li>')


def _candidates(entries, instances, query, now):
    placements = {entry['id']: _placement(entry) for entry in entries if entry['provider'] == 'linux'}
    for (domain, ip), subjects in sorted(_address_groups(entries, instances, now).items()):
        providers = {subject.provider: subject for subject in subjects}
        if len(providers) < 2 or len(providers) != len(subjects) or any(not s.name for s in subjects):
            continue
        machine, guest = providers.get('linux'), providers.get('incus')
        if machine and guest:
            target = placements[machine.source]
            if target is not None and target != (guest.scope, guest.name):
                continue  # A conflicting explicit placement is stronger than an IP overlap.
            if 'unifi' in providers and (target is None or instances.get(target, {}).keys() != {guest}):
                continue  # Two different guest subjects compete for the same network sighting.
        ordered = (providers[provider] for provider in _PATHS if provider in providers)
        for left, right in combinations(ordered, 2):
            a, b = subjects[left], subjects[right]
            if (a is None or b is None or abs(a.stamp - b.stamp) > _MAX_SKEW
                    or not _selected(query, left, right, a.label, b.label, domain, ip)):
                continue
            yield (f'<li><code>{escape(ip)}</code> in {escape(domain)}: '
                   f'{_link(left, a.label)} at {_time(a.stamp)} ↔ '
                   f'{_link(right, b.label)} at {_time(b.stamp)}.</li>')


def render(entries, query):
    """Render the relationships body; the workspace supplies authorization and the page shell."""
    visible = [entry for entry in entries if entry['enabled'] is True and entry['provider'] in _PATHS]
    now = datetime.now(timezone.utc)
    instances = _instances(visible, now)
    placements = ''.join(_placements(visible, instances, query))
    candidates = ''.join(_candidates(visible, instances, query, now))
    return ('<section class="panel"><h2>Confirmed placement</h2>'
            + (f'<ul>{placements}</ul>' if placements else '<p>No confirmed placements in this view.</p>')
            + '</section><section class="panel"><h2>Candidate IP overlaps</h2>'
            '<p>A contemporaneous IP overlap in one configured network domain does not establish identity, '
            'ownership, or permission to collect.</p>'
            + (f'<ul>{candidates}</ul>' if candidates else '<p>No candidate overlaps in this view.</p>')
            + '</section>')
