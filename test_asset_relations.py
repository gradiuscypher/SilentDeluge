from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import unittest
from urllib.parse import parse_qs, urlsplit

from asset_relations import render


def _capture(rows, age=0):
    return dict(timestamp=(_NOW - timedelta(minutes=age)).isoformat(), records=rows)


def _section(rows, age=0, outcome='complete'):
    capture = _capture(rows, age)
    return {'latest': capture['timestamp'], outcome: capture}


def _entry(source, provider, scope, *, domain='stage-vlan', enabled=True, **sections):
    return dict(id=source, provider=provider, scope=scope, enabled=enabled,
                can_collect=False, import_only=True, network_domain=domain,
                state=dict(sections=sections), placement=None)


def _machine(source='machine', address='192.0.2.7', age=0, **settings):
    return _entry(source, 'linux', 'tin-piano', **settings,
                  addresses=_section([dict(asset='tin-piano', ip_address=address)], age))


def _network(source='network', address='192.0.2.7', age=0, site='main-site', **settings):
    return _entry(source, 'unifi', site, **settings, connected_clients=_section([
        dict(id=f'unifi:{site}:connected_clients:client-1', name='Piano port', ip_address=address),
    ], age))


def _incus(scope='theatre/stage', names=('tin-piano',), **sections):
    return _entry('guests', 'incus', scope, instances=_section([
        dict(name=name, kind='container', state='Running') for name in names
    ]), **sections)


_NOW = datetime.now(timezone.utc) - timedelta(seconds=1)


class _Links(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.links = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            url = urlsplit(dict(attrs)['href'])
            self.links.append((url.path, parse_qs(url.query)))


class RelationshipsTest(unittest.TestCase):
    def test_canonical_ip_overlap_is_a_candidate_with_scoped_links_and_dates(self):
        machine = _machine('linux+<toy>', '2001:0DB8:0:0::7', age=1, domain='vlan<&>')
        network = _network(address='2001:db8::7', age=2, domain='vlan<&>')
        body = render([machine, network], {'q': 'piano port'})
        self.assertIn('does not establish identity, ownership, or permission', body)
        self.assertIn('2001:db8::7', body)
        self.assertIn('vlan&lt;&amp;&gt;', body)
        self.assertIn(_capture([], 1)['timestamp'], body)
        self.assertIn(_capture([], 2)['timestamp'], body)
        self.assertNotIn('<toy>', body)
        self.assertEqual(_Links(body).links, [
            ('/machines', {'source': ['linux+<toy>'], 'q': ['tin-piano']}),
            ('/unifi', {'source': ['network'], 'q': ['unifi:main-site:connected_clients:client-1']}),
        ])

    def test_ip_match_needs_the_same_explicit_domain_and_fresh_contemporaneous_captures(self):
        machine = _machine()
        for network in (_network(domain='other'), _network(domain=None), _network(age=6),
                        _network(age=-60), _network(age=61)):
            with self.subTest(network=network):
                self.assertEqual(_Links(render([machine, network], {})).links, [])
        self.assertEqual(_Links(render([_machine(age=61), _network(age=61)], {})).links, [])
        machine['state']['sections']['addresses']['latest'] = _capture([])['timestamp']
        machine['state']['sections']['addresses']['complete'] = _capture([
            dict(asset='tin-piano', ip_address='192.0.2.7')
        ], age=1)
        self.assertEqual(_Links(render([machine, _network()], {})).links, [])

    def test_rejects_nonindividual_and_special_addresses(self):
        for ip in ('0.0.0.0', '127.0.0.1', '169.254.0.1', '224.0.0.1', '255.255.255.255',
                   '::', '::1', '::ffff:127.0.0.1', 'fe80::1', 'ff02::1', '2001:db8::1%eth0',
                   '192.0.2.7/32', '192.0.2.7,192.0.2.8'):
            with self.subTest(ip=ip):
                self.assertEqual(_Links(render([_machine(address=ip), _network(address=ip)], {})).links, [])

    def test_conflicting_subjects_and_overlapping_sites_are_not_hidden_by_filters(self):
        machine, network = _machine(), _network()
        section = network['state']['sections']['connected_clients']
        section['complete']['records'].append(dict(id='second', ip_address='192.0.2.7'))
        self.assertEqual(_Links(render([machine, network], {'q': 'Piano port'})).links, [])
        network = _network()
        second_site = _network(source='another-network', site='other-site')
        self.assertEqual(_Links(render([machine, network, second_site], {'source': 'network'})).links, [])
        second_site['network_domain'] = 'other-vlan'
        self.assertEqual(len(_Links(render([machine, network, second_site], {})).links), 2)

    def test_address_reuse_in_retained_evidence_blocks_a_new_candidate(self):
        machine, network = _machine(), _network()
        section = network['state']['sections']['connected_clients']
        section['partial'] = section['complete']
        section['complete'] = _capture([dict(id='old-client', ip_address='192.0.2.7')], age=7)
        self.assertEqual(_Links(render([machine, network], {})).links, [])

    def test_guest_placement_needs_an_explicit_exact_visible_instance(self):
        machine, incus = _machine(), _incus()
        self.assertEqual(_Links(render([machine, incus], {})).links, [])
        machine['placement'] = dict(scope='theatre/stage', name='tin-piano')
        for other in ([], [_incus('other/stage')], [_incus(names=('Tin-Piano',))],
                      [_incus(names=())], [dict(incus, enabled=False)],
                      [dict(incus, state=None, declarations={'containers': [{'name': 'tin-piano'}]})]):
            with self.subTest(other=other):
                self.assertEqual(_Links(render([machine, *other], {})).links, [])
        body = render([machine, incus], {})
        self.assertIn('Confirmed placement', body)
        self.assertEqual(_Links(body).links, [
            ('/machines', {'source': ['machine'], 'q': ['tin-piano']}),
            ('/incus', {'source': ['guests'], 'q': ['tin-piano']}),
        ])

    def test_latest_failed_inventory_cannot_confirm_retained_placement(self):
        machine, incus = _machine(), _incus()
        machine['placement'] = dict(scope='theatre/stage', name='tin-piano')
        section = incus['state']['sections']['instances']
        section['complete']['timestamp'] = _capture([], age=1)['timestamp']
        self.assertEqual(_Links(render([machine, incus], {})).links, [])

    def test_disabled_sources_cannot_supply_rows_even_if_state_is_present(self):
        body = render([_machine(), _network('private-disabled', enabled=False)],
                      {'source': 'private-disabled'})
        self.assertNotIn('private-disabled', body)
        self.assertNotIn('192.0.2.7', body)
        self.assertEqual(_Links(body).links, [])

    def test_incus_runtime_addresses_need_positive_inventory_and_no_competing_guest(self):
        addresses = _section([dict(name='tin-piano', ip_address='192.0.2.7')], outcome='partial')
        incus, network = _incus(guest_addresses=addresses), _network()
        self.assertEqual(len(_Links(render([incus, network], {})).links), 2)
        self.assertEqual(_Links(render([_incus(names=(), guest_addresses=addresses), network], {})).links, [])
        machine = _machine()
        self.assertEqual(_Links(render([machine, incus, network], {})).links, [])
        machine['placement'] = dict(scope='theatre/stage', name='tin-piano')
        self.assertEqual(len(_Links(render([machine, incus, network], {})).links), 8)
        incus['state']['sections']['instances']['complete']['records'][0]['state'] = 'Stopped'
        self.assertEqual(_Links(render([incus, network], {})).links, [])

    def test_configured_incus_addresses_and_other_sections_are_not_network_evidence(self):
        incus = _incus()
        record, = incus['state']['sections']['instances']['complete']['records']
        record.update(ip_address='192.0.2.7', expanded_config={'ipv4.address': '192.0.2.7'})
        self.assertEqual(_Links(render([incus, _network()], {})).links, [])


if __name__ == '__main__':
    unittest.main()
