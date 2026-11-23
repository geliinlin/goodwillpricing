# Goodwill unified navigation spacing V1
"""V2 CSS architecture regression checks (stdlib only; no browser or MySQL)."""
import ast
from pathlib import Path
import re
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
STYLES = ROOT / "styles"
CSS_NAMES = {"global.css", "navigation.css", "dashboard.css",
             "login.css", "guidelines.css", "pricing.css", "reports.css", "users.css", "configuration.css"}


class CSSArchitectureTests(unittest.TestCase):
    def source(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_entrypoint_and_page_modules_parse(self):
        for name in ("app.py", "style_loader.py", "dashboard_router.py",
                     "dashboard_page.py", "guideline_page.py", "item_search_page.py"):
            with self.subTest(name=name):
                ast.parse(self.source(name), filename=name)

    def test_eight_shared_stylesheets_are_present(self):
        self.assertEqual({p.name for p in STYLES.glob("*.css")}, CSS_NAMES)

    def test_one_owner_of_application_page_width(self):
        global_css = self.source("styles/global.css")
        self.assertIn("--gw-page-max: 1480px", global_css)
        self.assertIn("max-width: var(--gw-page-max)", global_css)
        for name in CSS_NAMES - {"global.css", "login.css"}:
            with self.subTest(name=name):
                css = self.source("styles/" + name)
                self.assertNotIn("stMainBlockContainer", css)
                self.assertNotRegex(css, r"(?:max-width\s*:\s*1480px|--gw-page-max\s*:)")

    def test_streamlit_chrome_hidden_only_in_global_style(self):
        global_css = self.source("styles/global.css")
        for selector in ('stHeader', 'stToolbar', 'stSidebar', '#MainMenu'):
            self.assertIn(selector, global_css)
        for name in CSS_NAMES - {"global.css"}:
            self.assertNotIn('stToolbar', self.source("styles/" + name), name)
            self.assertNotIn('#MainMenu', self.source("styles/" + name), name)

    def test_loader_whitelists_bundled_styles(self):
        tree = ast.parse(self.source("style_loader.py"))
        self.assertTrue(any(isinstance(x, ast.FunctionDef) and x.name == "load_styles" for x in tree.body))
        for name in CSS_NAMES:
            self.assertIn('"' + name + '"', self.source("style_loader.py"))
        self.assertIn("raise ValueError", self.source("style_loader.py"))
        self.assertIn("st.html(path)", self.source("style_loader.py"))

    def test_business_pages_load_only_their_own_css(self):
        dash = self.source("dashboard_page.py")
        pricing = self.source("item_search_page.py")
        self.assertIn('load_styles("navigation.css", "dashboard.css")', dash)
        self.assertIn('load_styles("pricing.css")', pricing)
        self.assertIn('load_styles("guidelines.css")', self.source("guideline_page.py"))
        self.assertIn("load_styles('reports.css')", self.source("reports_page.py"))
        self.assertIn("load_styles('users.css')", self.source("users_page.py"))
        self.assertIn('load_styles("login.css")', self.source("app.py"))
        self.assertIn('load_styles("global.css")', self.source("app.py"))
        self.assertNotIn("DASHBOARD_CSS", dash)
        self.assertNotRegex(pricing, re.compile(r"^\s*CSS\s*=", flags=re.M))

    def test_admin_navigation_order_and_equal_widths(self):
        dash = self.source("dashboard_page.py")
        start = dash.index('("Admin Dashboard", "admin_dashboard"')
        end = dash.index('] if admin else [', start)
        menu = dash[start:end]
        labels = ('Admin Dashboard', 'Guidelines', 'Reports', 'Users', 'Configuration')
        indices = [menu.index('"' + x + '"') for x in labels]
        self.assertEqual(indices, sorted(indices))
        self.assertIn('shell = st.columns([2.4, 5.9, 1.9, .78]', dash)
        self.assertIn('st.columns(len(nav_tabs), gap="small"', dash)
        self.assertIn('destination == current_page', dash)
        self.assertIn('.st-key-gw_nav_selected', self.source('styles/navigation.css'))

    def test_single_authenticated_navigation_and_hidden_subheaders(self):
        router = self.source('dashboard_router.py')
        self.assertEqual(router.count('render_nav_header(user, _navigate, _logout, page)'), 1)
        self.assertIn('show_guideline_management(user, show_header=False)', router)
        self.assertIn('render_item_search_page(show_header=False, on_logout=_logout)', router)
        self.assertIn('if show_header:', self.source('guideline_page.py'))
        self.assertIn('if not show_header:', self.source('item_search_page.py'))

    def test_current_business_routes_are_preserved(self):
        router = self.source('dashboard_router.py')
        for route in ('show_admin_dashboard', 'show_staff_dashboard', 'show_pricing_history',
                      'show_reprint_tag', 'show_guideline_management', 'render_item_search_page'):
            self.assertIn(route, router)
        price = self.source('item_search_page.py')
        for service in ('get_active_price_guideline', 'save_pricing_decision', 'printable_label_pdf'):
            self.assertIn(service, price)

    def test_legacy_sidebar_and_injected_styles_removed(self):
        app = self.source('app.py')
        self.assertNotIn('EMPLOYEE_LAYOUT_CSS', app)
        self.assertNotIn('st.sidebar.radio', app)
        self.assertIn('render_authenticated_app(user)', app)
        self.assertEqual(app.count('st.set_page_config('), 1)

    def test_page_scoped_style_names_avoid_cross_page_brand_footer_conflicts(self):
        self.assertIn('.st-key-module3_topbar .gw-logo', self.source('styles/pricing.css'))
        self.assertIn('.st-key-gw_nav .gw-brand', self.source('styles/guidelines.css'))
        self.assertIn('.gw-login-footer', self.source('styles/login.css'))
        self.assertIn('class="gw-pricing-footer"', self.source('item_search_page.py'))

    def test_streamlit_config_toml_is_parseable(self):
        configuration = tomllib.loads(self.source('.streamlit/config.toml'))
        self.assertEqual(configuration['client']['toolbarMode'], 'minimal')
        self.assertTrue(configuration['ui']['hideTopBar'])


if __name__ == '__main__':
    unittest.main()
