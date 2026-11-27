"""Render the authenticated inventory workspaces against synthetic stored evidence."""

from html.parser import HTMLParser
import unittest
from urllib.parse import parse_qsl, urlsplit

from workspace_pages import render


_DECLARED = '2025-01-01T09:00:00Z'
_CAPTURED = '2025-01-01T10:00:00Z'
_SIGHTED = '2025-01-01T10:05:00Z'
_ATTEMPTED = '2025-01-01T10:10:00Z'
_PROJECT = 'theatre/stage'


def _capture(*records, timestamp=_CAPTURED):
    return dict(timestamp=timestamp, records=list(records))


def _state(**sections):
    return dict(timestamp=_ATTEMPTED, outcome='Results received', imported=True,
                sections={name: {'latest': capture['timestamp'], 'complete': capture}
                          for name, capture in sections.items()},
                results={name: dict(outcome='complete', timestamp=capture['timestamp'])
                         for name, capture in sections.items()})


def _entry(id_, provider, *, scope=None, enabled=True, state=None, declarations=None, placement=None):
    return dict(id=id_, provider=provider, scope=scope or id_, read='Read approved synthetic evidence.',
                enabled=enabled, can_collect=True, import_only=False,
                state=state, declarations=declarations, placement=placement)


def _instance(name, *, cpu='1', memory=None, state='Running', **context):
    return dict(name=name, kind='container', cpu=cpu, memory=memory, state=state, **context)


def _declarations():
    return dict(source='stage <plan>', scope=_PROJECT, timestamp=_DECLARED, vms=[], containers=[
        dict(name='paper-moon', cpu='02', memory='1 GiB'),
        dict(name='tin-piano'),
        dict(name='velvet-curtain', cpu='4'),
    ])


class _Page(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.links = []
        self.caption = ''
        self.headings = []
        self.rows = []
        self.row_headers = []
        self.row_links = []
        self.cell_tags = set()
        self._section = None
        self._cell = None
        self._scope = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == 'a' and (url := dict(attrs).get('href')) is not None:
            self.links.append(url)
            if self._section == 'tbody' and self._scope == 'row':
                self.row_links[-1].append(url)
        if self._section == 'tbody' and self._cell is not None:
            self.cell_tags.add(tag)
        if tag in ('caption', 'thead', 'tbody'):
            self._section = tag
        if tag == 'tr' and self._section == 'tbody':
            self.rows.append([])
            self.row_headers.append([])
            self.row_links.append([])
        if tag in ('th', 'td') and self._section in ('thead', 'tbody'):
            self._cell = []
            self._scope = dict(attrs).get('scope')

    def handle_data(self, data):
        if self._section == 'caption':
            self.caption += data
        elif self._cell is not None and data.strip():
            self._cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self._cell is not None:
            text = ' '.join(self._cell)
            if self._section == 'thead':
                self.headings.append((tag, self._scope, text))
            else:
                self.rows[-1].append(text)
                self.row_headers[-1].append((tag, self._scope))
            self._cell = None
            self._scope = None
        if tag == self._section:
            self._section = None

    def by_title(self):
        return {name: cells for name, *cells in self.rows}


def _render(path, entries, **query):
    return render(path, query, entries, 'reader <test>', 'csrf"token')


def _follow(url, entries):
    target = urlsplit(url)
    return _render(target.path, entries, **dict(parse_qsl(target.query)))


class WorkspacePagesTests(unittest.TestCase):
    def assert_table(self, parsed, caption, *columns):
        self.assertEqual(parsed.caption, caption)
        self.assertEqual(parsed.headings, [('th', 'col', name) for name in columns])
        self.assertEqual(parsed.row_headers, [[('th', 'row')] + [('td', None)] * (len(columns) - 1)] * len(parsed.rows))
        self.assertFalse({'details', 'dl', 'ul', 'ol', 'pre'} & parsed.cell_tags)

    def test_incus_defaults_to_instances_from_all_enabled_sources(self):
        main = _entry('stage', 'incus', scope=_PROJECT, state=_state(
            instances=_capture(_instance('paper-moon', cpu='2', memory='1GiB', location='node-a',
                                         profiles=['profile-not-in-cell']),
                               _instance('tin-piano', state='Stopped')),
            runtime=_capture(_instance('runtime-only')),
        ))
        second = _entry('studio', 'incus', scope='theatre/studio',
                        state=_state(instances=_capture(_instance('velvet-curtain', cpu='0-3'))))
        disabled = _entry('hidden-source', 'incus', scope='hidden-scope', enabled=False,
                          state=_state(instances=_capture(_instance('hidden-instance'))), declarations=_declarations())
        entries = [second, disabled, main]
        markup = _render('/incus', entries)
        parsed = _Page(markup)
        self.assert_table(parsed, 'Incus instances', 'Instance', 'Kind', 'State', 'CPU limit (count or pin set)',
                          'Memory limit (as reported)', 'Location', 'Evidence', 'Source and scope', 'Capture time')
        self.assertEqual([row[0] for row in parsed.rows], ['paper-moon', 'tin-piano', 'velvet-curtain'])
        self.assertEqual(parsed.rows[0], ['paper-moon', 'container', 'Running', '2', '1GiB', 'node-a', 'Complete',
                                          f'stage {_PROJECT}', _CAPTURED])
        self.assertNotIn('profile-not-in-cell', markup)
        link, = parsed.row_links[0]
        self.assertEqual(urlsplit(link).path, '/incus/detail')
        detail = _follow(link, entries)
        self.assertIn('profile-not-in-cell', detail)
        self.assertIn('Scope: theatre/stage', detail)
        self.assertIn('Complete capture', detail)
        self.assertNotIn('tin-piano', detail)
        self.assertFalse(_Page(detail).rows)
        self.assertEqual(markup.count('Latest section outcome:'), 2)
        for excluded in ('runtime-only', 'hidden-source', 'hidden-scope', 'hidden-instance', 'stage &lt;plan&gt;'):
            self.assertNotIn(excluded, markup)
        self.assertEqual(_Page(_render('/incus', entries, section='instances')).rows, parsed.rows)
        filtered = _Page(_render('/incus', entries, q='CONTAINER', status='stopped'))
        self.assertEqual(filtered.rows, [parsed.rows[1]])

    def test_machine_overview_shows_one_row_per_machine_and_links_to_typed_evidence(self):
        state = _state(system=_capture(dict(asset='floor host', os_id='debian', os_version='12',
                                            kernel_release='6.1.0', architecture='x86_64')),
                       cpu=_capture(dict(view='procfs', busy_percent=0, sample_seconds=0.25)),
                       memory=_capture(dict(view='cgroup-v2', used_bytes=64 * 1024**2, limit_bytes=None)),
                       packages=_capture(dict(name='pkg-a'), dict(name='pkg-b')))
        state['results']['cpu'] = dict(outcome='failed', timestamp=_ATTEMPTED)
        entries = [_entry('waiting', 'linux'), _entry('host', 'linux', scope='stage/floor', state=state)]
        landing = _Page(_render('/machines', entries))
        self.assert_table(landing, 'Machine overview', 'Machine', 'Scope', 'OS', 'Kernel', 'CPU usage', 'Memory used',
                          'Evidence', 'Latest attempt')
        self.assertEqual([row[0] for row in landing.rows], ['host', 'waiting'])
        self.assertEqual(landing.rows[0][:6], ['host', 'stage/floor', 'debian 12', '6.1.0', '0% Stale',
                                               '64 MiB / uncapped'])
        self.assertIn('Failed', landing.rows[0][6])
        self.assertEqual(landing.rows[1][6], 'Not connected')
        filtered = _Page(_render('/machines', entries, q='DEBIAN 6.1.0', status='stale'))
        self.assertEqual(filtered.rows, [landing.rows[0]])
        link = '/machines?source=host&section=system'
        host, = landing.row_links[0]
        machine = _follow(host, entries)
        self.assertIn(link, _Page(machine).links)
        self.assertIn('Scope: stage/floor', machine)
        self.assertIn('/machines?source=host&section=packages', _Page(machine).links)
        waiting, = landing.row_links[1]
        self.assertIn('No inventory sections received', _follow(waiting, entries))
        drilldown = _Page(_follow(link, entries))
        self.assert_table(drilldown, 'System', 'Observation', 'OS', 'OS version', 'Kernel', 'Architecture', 'Evidence',
                          'Source and scope', 'Capture time')
        self.assertEqual(drilldown.rows[0][:5], ['floor host', 'debian', '12', '6.1.0', 'x86_64'])
        sample, = drilldown.row_links[0]
        self.assertIn(sample, landing.links)
        self.assertIn('<dt>Architecture</dt><dd>x86_64</dd>', _follow(sample, entries))

    def test_empty_instances_distinguish_no_enabled_source_no_capture_and_complete_empty(self):
        disabled = _entry('private-stage', 'incus', scope='private-scope', enabled=False)
        markup = _render('/incus', [disabled])
        self.assertEqual(_Page(markup).rows, [])
        self.assertIn('Enable an approved view', markup)
        self.assertNotIn('private-stage', markup)
        self.assertNotIn('private-scope', markup)
        entries = [_entry('waiting', 'incus'), _entry('empty-stage', 'incus', state=_state(instances=_capture()))]
        markup = _render('/incus', entries)
        self.assertEqual(_Page(markup).rows, [])
        self.assertIn('0 results', markup)
        self.assertIn('Not connected: no collection attempted', markup)
        self.assertIn(f'Complete capture: <time datetime="{_CAPTURED}">{_CAPTURED}</time> — 0 records', markup)
        self.assertIn('Check source and section coverage', markup)

    def test_overview_has_counts_and_freshness_without_inventory_rows(self):
        machine = _entry('guest', 'linux', state=_state(packages=_capture(dict(name='private-package'))))
        disabled = _entry('paused', 'linux', enabled=False)
        markup = _render('/', [machine, disabled])
        self.assertIn('Sources: 2 · Disabled: 1', markup)
        self.assertIn('Sections: 1 complete', markup)
        self.assertIn(f'datetime="{_CAPTURED}"', markup)
        self.assertIn(f'datetime="{_ATTEMPTED}"', markup)
        self.assertNotIn('private-package', markup)
        self.assertIn('reader &lt;test&gt;', markup)
        self.assertIn('method="post" action="/logout"', markup)
        self.assertIn('name="csrf" value="csrf&quot;token"', markup)
        self.assertTrue({'/machines', '/incus', '/unifi', '/relationships', '/collectors', '/access', '/patterns'}
                        <= set(_Page(markup).links))
        landing = _Page(_render('/machines', [machine, disabled]))
        guest, = landing.row_links[0]
        detail = _follow(guest, [machine, disabled])
        self.assertIn('/machines?source=guest&section=packages', _Page(detail).links)
        self.assertNotIn('private-package', detail)

    def test_get_search_filters_source_section_and_evidence_status(self):
        alpha = _entry('alpha', 'linux', state=_state(
            packages=_capture(dict(name='aLPha-package', version='4.2', description='<shade>'),
                              dict(name='other-package', version='4.2')),
            services=_capture(dict(name='alpha-service', version='4.2', state='masked')),
        ))
        beta = _entry('beta', 'linux', state=_state(packages=_capture(dict(name='alpha-mirror', version='4.2'))))
        markup = _render('/machines', [alpha, beta], q='ALPHA-PACKAGE 4.2', source='alpha',
                         section='packages', status='complete')
        parsed = _Page(markup)
        self.assert_table(parsed, 'Installed packages', 'Package', 'Version', 'Architecture', 'Manager',
                          'Evidence', 'Source and scope', 'Capture time')
        self.assertEqual(parsed.rows, [['aLPha-package', '4.2', '—', '—', 'Complete', 'alpha alpha', _CAPTURED]])
        link, = parsed.row_links[0]
        detail = _follow(link, [alpha, beta])
        self.assertNotIn('other-package', markup)
        self.assertIn('Showing 1–1 of 1 results', markup)
        self.assertIn('<form method="get" action="/machines"', markup)
        self.assertIn('<a href="/machines">Clear filters</a>', markup)
        self.assertNotIn('&lt;shade&gt;', markup)
        self.assertIn('<dt>Description</dt><dd>&lt;shade&gt;</dd>', detail)
        self.assertNotIn('<shade>', detail)
        self.assertNotIn('other-package', detail)
        search = _render('/machines', [alpha, beta], q='4.2', section='services', status='masked')
        self.assertEqual(_Page(search).rows[0][:3], ['alpha-service', 'masked', '—'])
        empty = _render('/machines', [alpha, beta], section='services', status='partial')
        self.assertEqual(_Page(empty).rows, [])
        self.assertIn('0 results', empty)
        self.assertIn('does not establish absence', empty)

    def test_pagination_bounds_the_response_and_carries_filters_forward(self):
        packages = _capture(*(dict(name=f'pkg-{i:03}', note=f'evidence-marker-{i:03}') for i in reversed(range(61))))
        machine = _entry('guest', 'linux', state=_state(packages=packages))
        query = dict(q='pkg-', source='guest', section='packages', status='complete')
        first = _render('/machines', [machine], **query)
        parsed_first = _Page(first)
        self.assertEqual([row[0] for row in parsed_first.rows], [f'pkg-{i:03}' for i in range(25)])
        self.assertIn('Showing 1–25 of 61 results', first)
        self.assertNotIn('pkg-025', first)
        self.assertNotIn('evidence-marker-060', first)
        next_page, = [urlsplit(url) for url in parsed_first.links if 'page=2' in url]
        self.assertEqual(dict(parse_qsl(next_page.query)), dict(query, page='2'))
        second = _render(next_page.path, [machine], **dict(parse_qsl(next_page.query)))
        self.assertEqual([row[0] for row in _Page(second).rows], [f'pkg-{i:03}' for i in range(25, 50)])
        self.assertNotIn('evidence-marker-024', second)
        self.assertNotIn('evidence-marker-050', second)
        wide = _render('/machines', [machine], **query, per_page='50')
        self.assertEqual(len(_Page(wide).rows), 50)
        oversized = _render('/machines', [machine], **query, per_page='10000', page='9' * 128)
        self.assertIn('Showing 51–61 of 61 results · Page 3 of 3', oversized)
        self.assertEqual([row[0] for row in _Page(oversized).rows], [f'pkg-{i:03}' for i in range(50, 61)])

    def test_unifi_searches_nested_fields_without_rendering_other_rows(self):
        devices = _capture(dict(id='device:one', name='side-stage', state='ONLINE', supported=False,
                                model='USW-24', firmware_version='7.1', ip_address='192.0.2.1',
                                details={'ports': [{'index': 1, 'speed_mbps': 0, 'poe': {'enabled': False}}],
                                         'observed_at': _SIGHTED}, note=None),
                           dict(id='device:two', name='other-stage', state='OFFLINE', supported=True))
        client = dict(id='unifi:site:connected_clients:client-1', name='client-only', type='WIRED',
                      mac_address='02:00:00:00:00:01', ip_address='192.0.2.2', connected_at=_SIGHTED)
        site = _entry('site', 'unifi', state=_state(managed_devices=devices, connected_clients=_capture(client)))
        landing = _Page(_render('/unifi', [site]))
        self.assert_table(landing, 'Managed devices', 'Device', 'Model', 'State', 'IP address', 'Firmware',
                          'Evidence', 'Source and scope', 'Capture time')
        self.assertEqual([row[0] for row in landing.rows], ['other-stage', 'side-stage'])
        markup = _render('/unifi', [site], status='online', q='Ethernet')
        parsed = _Page(markup)
        self.assertEqual(parsed.rows, [landing.rows[1]])
        self.assertEqual(parsed.rows[0][:6], ['side-stage', 'USW-24', 'ONLINE', '192.0.2.1', '7.1', 'Complete'])
        link, = parsed.row_links[0]
        self.assertEqual(urlsplit(link).path, '/unifi/detail')
        detail = _follow(link, [site])
        for value in ('<dt>Power over Ethernet</dt>', '<dt>Enabled</dt><dd>No</dd>',
                      '<dt>Speed mbps</dt><dd>0</dd>', '<dt>Note</dt><dd>Not reported</dd>',
                      f'datetime="{_SIGHTED}"'):
            self.assertIn(value, detail)
        for excluded in ('other-stage', 'client-only', 'Power over Ethernet', _SIGHTED):
            self.assertNotIn(excluded, markup)
        self.assertNotIn('other-stage', detail)
        self.assertIn('/unifi?source=site&section=managed_devices', _Page(detail).links)
        client_url = '/unifi?section=connected_clients'
        self.assertIn(client_url, landing.links)
        target = urlsplit(client_url)
        clients = _render(target.path, [site], **dict(parse_qsl(target.query)))
        parsed_clients = _Page(clients)
        self.assert_table(parsed_clients, 'Connected clients', 'Client', 'Type', 'IP address', 'MAC address',
                          'Connected at', 'Evidence', 'Source and scope', 'Capture time')
        self.assertEqual(parsed_clients.rows[0][:5], ['client-only', 'WIRED', '192.0.2.2', '02:00:00:00:00:01',
                                                     _SIGHTED])
        self.assertEqual(_Page(_render('/unifi', [site], source='site', q=client['id'])).rows, parsed_clients.rows)
        self.assertIn('absence is not established', clients)
        client_link, = parsed_clients.row_links[0]
        self.assertIn(client['id'], _follow(client_link, [site]))

    def test_placement_and_relationship_links_require_authorized_enabled_targets(self):
        guest = _entry('guest', 'linux', scope='theatre/paper-moon',
                       placement=dict(scope=_PROJECT, name='paper-moon'))
        wrong_scope = _entry('other-project', 'incus', scope='theatre/other',
                             state=_state(instances=_capture(_instance('paper-moon'))))
        disabled = _entry('disabled-placement-source', 'incus', scope=_PROJECT, enabled=False)
        entries = [guest, wrong_scope, disabled]
        machine = _render('/machines', entries, source='guest')
        self.assertIn('/relationships?source=guest', _Page(machine).links)
        self.assertNotIn('disabled-placement-source', machine)
        self.assertFalse(any(url.startswith('/incus?') for url in _Page(machine).links))
        for path in ('/incus', '/relationships'):
            hidden = _render(path, entries, source='disabled-placement-source')
            self.assertEqual(hidden, _render(path, entries, source='unlisted-placement-source'))
            self.assertNotIn('disabled-placement-source', hidden)
        approved = _entry('approved', 'incus', scope=_PROJECT,
                          state=_state(instances=_capture(_instance('paper-moon'), _instance('another-guest'))))
        entries += [approved, _entry('same-name', 'linux', scope='other/paper-moon')]
        links = _Page(_render('/machines', entries, source='guest')).links
        target = '/incus?source=approved&section=instances&item=paper-moon'
        self.assertIn(target, links)
        listing = _Page(_follow(target, entries))
        self.assertEqual(list(listing.by_title()), ['paper-moon'])
        self.assertIn('/machines?source=guest', listing.links)
        record, = listing.row_links[0]
        detail_links = _Page(_follow(record, entries)).links
        self.assertIn('/machines?source=guest', detail_links)
        self.assertNotIn('/machines?source=same-name', detail_links)
        relationships = _render('/relationships', entries, source='approved')
        self.assertIn('<!doctype html>', relationships)
        self.assertIn('Candidate IP overlaps', relationships)
        self.assertIn('/incus?source=approved', _Page(relationships).links)

    def test_complete_incus_comparison_uses_declared_limits_and_keeps_observations_separate(self):
        records = _capture(_instance('paper-moon', cpu='2', memory='1024MiB', architecture='aarch64'),
                           _instance('velvet-curtain'), _instance('extra-guest', state='Stopped'))
        project = _entry('stage', 'incus', scope=_PROJECT, state=_state(instances=records),
                         declarations=_declarations())
        landing = _render('/incus', [project])
        self.assertIn('/incus?source=stage&section=comparison', _Page(landing).links)
        markup = _render('/incus', [project], section='comparison')
        cards = _Page(markup).by_title()
        self.assertIn('Match', cards['paper-moon'])
        self.assertIn('Disagreement', cards['velvet-curtain'])
        self.assertIn('CPU limit (count or pin set)', cards['velvet-curtain'])
        self.assertIn('Absent', cards['tin-piano'])
        self.assertIn('Observed and undeclared', cards['extra-guest'])
        self.assertIn('stage &lt;plan&gt;', markup)
        self.assertIn(f'datetime="{_DECLARED}"', markup)
        self.assertIn(f'datetime="{_CAPTURED}"', markup)
        self.assertNotIn('aarch64', markup)
        observed = _Page(_render('/incus', [project], section='instances', item='paper-moon'))
        observed_link, = observed.row_links[0]
        self.assertIn('aarch64', _follow(observed_link, [project]))
        compared = _Page(_render('/incus', [project], section='comparison', item='velvet-curtain'))
        compared_link, = compared.row_links[0]
        detail = _follow(compared_link, [project])
        self.assertIn('<dt>Declared</dt>', detail)
        self.assertIn('<dt>Observed</dt>', detail)
        self.assertIn('stage &lt;plan&gt;', detail)
        self.assertIn(f'datetime="{_DECLARED}"', detail)
        self.assertNotIn('aarch64', detail)
        observed = _render('/incus', [project], section='instances')
        self.assertNotIn('tin-piano', observed)
        self.assertEqual(list(_Page(_render('/incus', [project], section='comparison', q='TIN', status='absent'))
                              .by_title()), ['tin-piano'])

    def test_partial_failed_and_omitted_attempts_do_not_keep_an_absence_claim(self):
        for outcome in ('partial', 'failed', 'not returned', 'no newer evidence'):
            with self.subTest(outcome=outcome):
                state = _state(instances=_capture(_instance('paper-moon')))
                state['sections']['instances']['partial'] = _capture(dict(name='partial-only'), timestamp=_SIGHTED)
                if outcome == 'not returned':
                    state['results'].clear()
                else:
                    state['results']['instances'] = dict(outcome=outcome, timestamp=_ATTEMPTED)
                project = _entry('stage', 'incus', scope=_PROJECT, state=state, declarations=_declarations())
                evidence = _render('/incus', [project], section='instances')
                self.assertIn('Stale — last verified complete capture', evidence)
                self.assertIn('Stale — retained partial sightings', evidence)
                self.assertIn(f'Latest section outcome: {outcome}', evidence)
                self.assertIn(f'datetime="{_SIGHTED}"', evidence)
                self.assertIn('partial-only', evidence)
                if outcome == 'partial':
                    self.assertIn('No sightings in this partial capture (0 records)', evidence)
                cards = _Page(_render('/incus', [project], section='comparison')).by_title()
                self.assertIn('Stale', cards['paper-moon'])
                self.assertIn('Unknown', cards['tin-piano'])
                self.assertNotIn('Absent', cards['tin-piano'])
                self.assertNotIn('partial-only', cards)

    def test_runtime_sightings_cannot_establish_presence_or_absence_in_comparison(self):
        partial = _capture(dict(name='paper-moon', kind='container', state='running'))
        state = dict(timestamp=_ATTEMPTED, outcome='Results received', imported=True,
                     sections={'runtime': {'latest': _CAPTURED, 'partial': partial}},
                     results={'runtime': dict(outcome='partial', timestamp=_CAPTURED)})
        project = _entry('stage', 'incus', scope=_PROJECT, state=state, declarations=_declarations())
        runtime = _render('/incus', [project], section='runtime', status='partial')
        self.assertIn('Partial', _Page(runtime).by_title()['paper-moon'])
        self.assertIn('Presence of omitted records is unknown', runtime)
        comparison = _Page(_render('/incus', [project], section='comparison')).by_title()
        self.assertIn('Unknown', comparison['paper-moon'])
        self.assertIn('Unknown', comparison['tin-piano'])
        disconnected = dict(project, state=None)
        self.assertIn('Not connected', _Page(_render('/incus', [disconnected], section='comparison'))
                      .by_title()['paper-moon'])

    def test_duplicate_identities_select_the_exact_capture_and_do_not_rebind(self):
        original = dict(name='mirror', id='duplicate-id', note='retained-complete-note')
        partial = dict(original, note='newer-partial-note')
        state = _state(managed_devices=_capture(original, original))
        state['sections']['managed_devices']['partial'] = _capture(partial, timestamp=_SIGHTED)
        state['results']['managed_devices'] = dict(outcome='partial', timestamp=_SIGHTED)
        site = _entry('site', 'unifi', scope='site/synthetic', state=state)
        grid = _Page(_render('/unifi', [site]))
        self.assertEqual([row[0] for row in grid.rows], ['mirror'] * 3)
        (first,), (duplicate,), (sighting,) = grid.row_links
        self.assertEqual(len({first, duplicate, sighting}), 3)
        for link in (first, duplicate):
            detail = _follow(link, [site])
            self.assertIn('retained-complete-note', detail)
            self.assertIn('Last verified complete capture', detail)
            self.assertNotIn('newer-partial-note', detail)
        detail = _follow(sighting, [site])
        self.assertIn('newer-partial-note', detail)
        self.assertIn('Partial sighting', detail)
        self.assertNotIn('retained-complete-note', detail)

        replaced = dict(site, state=_state(managed_devices=_capture(original, timestamp=_ATTEMPTED)))
        rebound = dict(site, scope='site/rebound')
        for unavailable in ([], [dict(site, enabled=False)], [replaced], [rebound]):
            with self.subTest(unavailable=unavailable):
                self.assertIsNone(_follow(first, unavailable))
                self.assertIsNone(_follow(sighting, unavailable))
        target = urlsplit(first)
        query = dict(parse_qsl(target.query))
        self.assertIsNone(_render('/machines/detail', [site], **query))
        self.assertIsNone(_render(target.path, [site], item=query['item']))
        self.assertIsNone(_render(target.path, [site], source='site', item='duplicate-id'))
        mirror = dict(site, id='other-site')
        self.assertIsNone(_render(target.path, [site, mirror], **dict(query, source='other-site')))

    def test_comparison_link_reassesses_absence_when_the_capture_is_retained(self):
        state = _state(instances=_capture(_instance('paper-moon')))
        project = _entry('stage', 'incus', scope=_PROJECT, state=state, declarations=_declarations())
        grid = _Page(_render('/incus', [project], section='comparison', item='tin-piano'))
        link, = grid.row_links[0]
        self.assertIn('>Absent</span>', _follow(link, [project]))
        failed = dict(state, results={'instances': dict(outcome='failed', timestamp=_ATTEMPTED)})
        retained = _follow(link, [dict(project, state=failed)])
        self.assertIn('>Unknown</span>', retained)
        self.assertIn('Latest section outcome: failed', retained)
        self.assertIn('did not verify current presence or absence', retained)
        self.assertNotIn('>Absent</span>', retained)

    def test_extension_and_long_structured_data_are_linked_without_unbounded_query_values(self):
        section = 'synthetic-extension-' + 'é' * 260
        long_name = 'synthetic-device-' + '界' * 320
        long_value = 'version-' + 'x' * 320
        record = dict(name=long_name, model={'ports': [dict(label='nested <port>')]},
                      firmware_version=long_value)
        site = _entry('site', 'unifi', state=_state(managed_devices=_capture(record),
                                                  **{section: _capture(dict(note='extension-only'))}))
        listing = _render('/unifi', [site])
        parsed = _Page(listing)
        self.assertIn('1 field', parsed.rows[0])
        self.assertTrue(parsed.rows[0][0].endswith('…'))
        self.assertNotIn(long_name, listing)
        self.assertNotIn(long_value, listing)
        self.assertNotIn('nested &lt;port&gt;', listing)
        detail_link, = parsed.row_links[0]
        self.assertLessEqual(len(dict(parse_qsl(urlsplit(detail_link).query))['item']), 256)
        detail = _follow(detail_link, [site])
        for value in (long_name, long_value, 'nested &lt;port&gt;'):
            self.assertIn(value, detail)
        extension, = [url for url in parsed.links if dict(parse_qsl(urlsplit(url).query)).get(
            'section', '').startswith('@')]
        extension_grid = _Page(_follow(extension, [site]))
        self.assertEqual(extension_grid.rows[0][0], 'Observation')
        link, = extension_grid.row_links[0]
        extension_detail = _follow(link, [site])
        self.assertIn('extension-only', extension_detail)
        self.assertIn(extension.replace('?section=', '?source=site&section='), _Page(extension_detail).links)

    def test_each_collected_section_links_to_only_one_full_record(self):
        sections_by_provider = (
            ('/machines', 'linux', ('system', 'packages', 'services', 'cpu', 'addresses', 'memory', 'disk',
                                    'storage', 'users', 'interfaces', 'network_links')),
            ('/incus', 'incus', ('instances', 'runtime', 'guest_addresses')),
            ('/unifi', 'unifi', ('managed_devices', 'connected_clients')),
        )
        for path, provider, sections in sections_by_provider:
            for section in sections:
                with self.subTest(provider=provider, section=section):
                    records = _capture(_instance('first-record', note='record-one-evidence'),
                                       _instance('second-record', note='record-two-evidence'))
                    entry = _entry('fixture', provider, state=_state(**{section: records}))
                    markup = _render(path, [entry], section=section)
                    parsed = _Page(markup)
                    self.assertEqual([row[0] for row in parsed.rows], ['first-record', 'second-record'])
                    self.assertNotIn('record-one-evidence', markup)
                    first, = parsed.row_links[0]
                    detail = _follow(first, [entry])
                    self.assertIn('record-one-evidence', detail)
                    self.assertNotIn('record-two-evidence', detail)
                    self.assertIn(f'{path}?source=fixture&section={section}', _Page(detail).links)

    def test_settings_routes_keep_collector_controls_and_pass_query_to_provider_guide(self):
        entries = [_entry('approved', 'linux'), _entry('paused', 'incus', enabled=False)]
        collectors = _render('/collectors', entries)
        for endpoint in ('/enable', '/disable', '/preview', '/logout'):
            self.assertIn(f'action="{endpoint}"', collectors)
        self.assertIn('Preview the UniFi template', _render('/collectors/new', entries, provider='unifi'))
        self.assertIn('approved', _render('/access', entries))
        self.assertIsNone(_render('/not-a-workspace', entries))


if __name__ == '__main__':
    unittest.main()
