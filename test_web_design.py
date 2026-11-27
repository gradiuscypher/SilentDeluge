from html.parser import HTMLParser
import unittest

from web_design import page, patterns


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

    def test_skip_link_targets_focusable_main(self):
        doc = Markup(page("Inventory", "<h1>Inventory</h1>"))
        main, = doc.start_tags("main")
        self.assertIn(f"#{main['id']}", [link.get("href") for link in doc.start_tags("a")])
        self.assertEqual(main.get("tabindex"), "-1")

    def test_workspace_caller_owns_the_patterns_link(self):
        doc = Markup(page("Inventory", """
            <nav aria-label="Workspace"><a href="/patterns">Patterns</a></nav>
            <h1>Inventory</h1>
        """))
        links = [link for link in doc.start_tags("a") if link.get("href") == "/patterns"]
        self.assertEqual(len(links), 1)

    def test_patterns_label_collection_and_presence_states_in_text(self):
        doc = Markup(patterns())
        labels = {event[1] for event in doc.events if event[0] == "text"}
        expected = {
            "Complete", "Partial", "Stale", "Disconnected", "Error",
            "Observed", "Absent", "Unknown",
        }
        self.assertEqual(expected - labels, set())

    def test_patterns_fragment_links_land_on_focusable_targets(self):
        doc = Markup(patterns())
        targets = {
            event[2]["id"]: event[2]
            for event in doc.events
            if event[0] == "start" and "id" in event[2]
        }
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
