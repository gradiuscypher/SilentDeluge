from html.parser import HTMLParser
import unittest

from web_design import dashboard_page, page, patterns


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.events = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.events.append(("start", tag, dict(attrs)))

    def handle_endtag(self, tag):
        self.events.append(("end", tag))

    def handle_data(self, data):
        if data.strip():
            self.events.append(("text", data.strip()))

    def start_tags(self, tag):
        return [event[2] for event in self.events if event[:2] == ("start", tag)]

    def elements(self, tag):
        starts = []
        for index, event in enumerate(self.events):
            if event[:2] == ("start", tag):
                starts.append(index)
            elif event == ("end", tag):
                start = starts.pop()
                yield self.events[start][2], self.events[start + 1:index]


class WebDesignTest(unittest.TestCase):
    def test_shell_treats_title_as_text_and_renders_caller_markup_in_main(self):
        title = '<img src=x onerror=alert(1)> & "Inventory"'
        doc = Markup(page(title, "<h1>Evidence <em>review</em></h1>"))
        title_start = doc.events.index(("start", "title", {}))
        title_end = doc.events.index(("end", "title"))
        self.assertEqual(doc.events[title_start + 1:title_end], [("text", title)])

        main, = doc.start_tags("main")
        main_start = doc.events.index(("start", "main", main))
        main_end = doc.events.index(("end", "main"))
        self.assertIn(("start", "em", {}), doc.events[main_start + 1:main_end])

    def test_skip_link_targets_focusable_main_in_both_shells(self):
        for html in (
            page("Inventory", "<h1>Inventory</h1>"),
            dashboard_page("/", "Inventory", "<p>Evidence</p>", "operator", "token"),
        ):
            doc = Markup(html)
            main, = doc.start_tags("main")
            first_link = doc.start_tags("a")[0]
            self.assertEqual(first_link["href"], f"#{main['id']}")
            self.assertEqual(main.get("tabindex"), "-1")

    def test_sign_in_shell_has_no_workspace_links_or_account_actions(self):
        doc = Markup(page("Sign in", "<h1>Sign in</h1>"))
        self.assertEqual(doc.start_tags("nav"), [])
        self.assertNotIn("/logout", [form.get("action") for form in doc.start_tags("form")])

    def test_dashboard_title_is_the_only_h1_and_account_can_submit_logout(self):
        title = 'Inventory <review> & "evidence"'
        username = 'Example <user> "name"'
        csrf = 'token"&<value>'
        doc = Markup(dashboard_page("/", title, "<h2>Coverage</h2>", username, csrf))
        h1, = doc.start_tags("h1")
        main, = doc.start_tags("main")
        h1_start = doc.events.index(("start", "h1", h1))
        self.assertGreater(h1_start, doc.events.index(("start", "main", main)))
        self.assertLess(h1_start, doc.events.index(("end", "main")))
        self.assertEqual(doc.events[h1_start + 1], ("text", title))
        self.assertIn(("text", username), doc.events)
        self.assertEqual(doc.start_tags("review"), [])
        self.assertEqual(doc.start_tags("user"), [])

        (form, fields), = doc.elements("form")
        self.assertEqual((form.get("method"), form.get("action")), ("post", "/logout"))
        token, = [event[2] for event in fields if event[:2] == ("start", "input")]
        self.assertEqual((token["type"], token["name"], token["value"]), ("hidden", "csrf", csrf))
        submit, = [event[2] for event in fields if event[:2] == ("start", "button")]
        self.assertEqual(submit.get("type"), "submit")
        self.assertIn(("text", "Log out"), fields)

    def test_dashboard_has_one_named_icon_and_text_link_per_section(self):
        doc = Markup(dashboard_page("/incus?section=runtime", "Incus", "", "operator", "token"))
        (nav, contents), = doc.elements("nav")
        self.assertEqual(nav.get("aria-label"), "Workspace")
        links = []
        for index, event in enumerate(contents):
            if event[:2] != ("start", "a"):
                continue
            attrs = event[2]
            end = contents.index(("end", "a"), index)
            parts = contents[index + 1:end]
            label, = [item[1] for item in parts if item[0] == "text"]
            icon, = [item[2] for item in parts if item[:2] == ("start", "svg")]
            self.assertEqual(icon["aria-hidden"], "true")
            links.append((attrs["href"], label, attrs.get("aria-current")))
        self.assertEqual(links, [
            ("/", "Overview", None), ("/machines", "Machines", None),
            ("/incus", "Incus", "page"), ("/unifi", "UniFi", None),
            ("/relationships", "Relationships", None), ("/collectors", "Collectors", None),
            ("/access", "Access", None), ("/patterns", "Design patterns", None),
        ])

    def test_mobile_disclosure_starts_closed_and_controls_navigation_and_account_actions(self):
        doc = Markup(dashboard_page("/incus", "Incus", "", "example operator", "token"))
        (disclosure, contents), = doc.elements("details")
        self.assertNotIn("open", disclosure)
        self.assertEqual(contents[0][:2], ("start", "summary"))
        summary = contents[0][2]
        self.assertIn(("text", "Menu"), contents[:contents.index(("end", "summary"))])
        (panel, menu), = [(attrs, parts) for attrs, parts in doc.elements("div")
                          if attrs.get("id") == summary["aria-controls"]]
        self.assertEqual(doc.events[doc.events.index(("end", "details")) + 1], ("start", "div", panel))
        nav, = doc.start_tags("nav")
        self.assertIn(("start", "nav", nav), menu)
        self.assertIn(("text", "example operator"), menu)
        logout, = doc.start_tags("form")
        self.assertIn(("start", "form", logout), menu)

    def test_dashboard_nested_route_identifies_parent_without_marking_it_current_page(self):
        doc = Markup(dashboard_page("/collectors/new", "New collector", "", "operator", "token"))
        current = [(link["href"], link["aria-current"]) for link in doc.start_tags("a") if "aria-current" in link]
        self.assertEqual(current, [("/collectors", "location")])

    def test_patterns_share_dashboard_navigation_and_only_authenticated_example_offers_logout(self):
        for doc, authenticated in ((Markup(patterns()), False),
                                   (Markup(patterns("example operator", "example token")), True)):
            (contents,) = [parts for nav, parts in doc.elements("nav") if nav.get("aria-label") == "Workspace"]
            current, = [event[2]["href"] for event in contents
                        if event[:2] == ("start", "a") and event[2].get("aria-current") == "page"]
            self.assertEqual(current, "/patterns")
            heading, = doc.start_tags("h1")
            self.assertEqual(doc.events[doc.events.index(("start", "h1", heading)) + 1],
                             ("text", "Inventory patterns"))
            logout = [form for form in doc.start_tags("form") if form.get("action") == "/logout"]
            self.assertEqual(bool(logout), authenticated)
            self.assertEqual(("text", "example operator") in doc.events, authenticated)

    def test_each_pattern_record_has_a_full_evidence_link_with_a_distinct_name(self):
        doc = Markup(patterns())
        full_links = {link["href"]: link["aria-label"] for link in doc.start_tags("a")
                      if link.get("aria-label", "").startswith("Full evidence for ")}
        names = ("paper-moon", "tin-piano", "painted-sun")
        self.assertEqual(full_links, {f"#{name}-evidence": f"Full evidence for {name}" for name in names})
        for heading in doc.start_tags("h3"):
            if f'#{heading.get("id")}' in full_links:
                label = doc.events[doc.events.index(("start", "h3", heading)) + 1][1]
                self.assertEqual(full_links[f'#{heading["id"]}'], f"Full evidence for {label}")

    def test_patterns_label_collection_and_presence_states_in_text(self):
        doc = Markup(patterns())
        labels = {event[1] for event in doc.events if event[0] == "text"}
        expected = {
            "Complete", "Partial", "Stale", "Disconnected", "Error",
            "Observed", "Absent", "Unknown",
        }
        self.assertEqual(expected - labels, set())

    def test_patterns_fragment_links_land_on_focusable_targets(self):
        doc = Markup(patterns("example operator", "token"))
        target_attrs = [event[2] for event in doc.events if event[0] == "start" and "id" in event[2]]
        targets = {attrs["id"]: attrs for attrs in target_attrs}
        self.assertEqual(len(targets), len(target_attrs), "IDs must be unique across responsive layouts")
        fragments = {
            link["href"][1:] for link in doc.start_tags("a") if link["href"].startswith("#")
        }
        self.assertTrue({"collection-states", "inventory-evidence", "evidence-detail", "paged-results"} <= fragments)
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                self.assertEqual(targets[fragment].get("tabindex"), "-1")

    def test_patterns_name_the_filter_and_preserve_table_context(self):
        doc = Markup(patterns())
        scope_filter, = doc.start_tags("select")
        self.assertIn(scope_filter["id"], [label.get("for") for label in doc.start_tags("label")])

        caption, = doc.start_tags("caption")
        scroll_region, = (region for region in doc.start_tags("div") if region.get("role") == "region")
        self.assertEqual(scroll_region["aria-labelledby"], caption["id"])
        self.assertEqual(scroll_region["tabindex"], "0")
        head_start = doc.events.index(("start", "thead", {}))
        head_end = doc.events.index(("end", "thead"))
        columns = [event[2].get("scope") for event in doc.events[head_start:head_end] if event[:2] == ("start", "th")]
        self.assertTrue(columns)
        self.assertEqual(set(columns), {"col"})

        body_start = doc.events.index(("start", "tbody", {}))
        body_end = doc.events.index(("end", "tbody"))
        body = doc.events[body_start:body_end]
        for index, event in enumerate(body):
            if event[:2] == ("start", "tr"):
                self.assertEqual(body[index + 1][:2], ("start", "th"))
                self.assertEqual(body[index + 1][2].get("scope"), "row")

    def test_pagination_marks_the_current_page_and_omits_out_of_range_links(self):
        doc = Markup(patterns())
        pagers = []
        for index, event in enumerate(doc.events):
            if event[:2] == ("start", "nav") and "pagination" in event[2].get("class", "").split():
                end = doc.events.index(("end", "nav"), index)
                pagers.append([item[2] for item in doc.events[index:end] if item[:2] == ("start", "a")])

        first, last = pagers
        for links, current, direction, neighbor in ((first, "1", "next", "2"), (last, "2", "prev", "1")):
            with self.subTest(page=current):
                self.assertEqual(
                    [link["aria-label"] for link in links if link.get("aria-current") == "page"],
                    [f"Page {current}"],
                )
                rels = {link["rel"]: link["href"] for link in links if "rel" in link}
                self.assertEqual(rels, {direction: f"#pattern-page-{neighbor}"})
