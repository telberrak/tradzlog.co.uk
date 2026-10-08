"""Pages anyone can see without signing in: the landing page, product pages, legal pages, and errors.

The legal texts are drafts for the private beta. They describe what the code does today (one session
cookie, data in AWS London, AI coaching through Anthropic only when asked), so keep them in step with
the code: a new tracker, processor or data use means a change here first.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from tradzlog_api.config import settings
from tradzlog_web.context import CURRENT_USER_ID
from tradzlog_web.ui import RISK_NOTICE, public_page

router = APIRouter()

LEGAL_UPDATED = "8 October 2026"
BROKERS = ("Interactive Brokers", "tastytrade", "NinjaTrader", "Tradovate", "MetaTrader 5", "Any CSV")


def signed_in() -> bool:
    return CURRENT_USER_ID.get() is not None


def support_link() -> str:
    email = escape(settings.support_email)
    return f'<a href="mailto:{email}">{email}</a>'


# --------------------------------------------------------------------- feature switches


def feature_disabled(path: str) -> bool:
    """True for pages of features that are switched off (see Settings.feature_*)."""
    community = path.startswith(("/community", "/share/")) or (path.startswith("/trades/") and path.endswith("/share"))
    if community and not settings.feature_community:
        return True
    return path.startswith("/settings/billing") and not settings.feature_billing


async def feature_gate(request: Request) -> None:
    """App-wide dependency: switched-off features answer 404, as if they did not exist."""
    if feature_disabled(request.url.path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


# --------------------------------------------------------------------- landing and product pages


def landing_page() -> str:
    brokers = "".join(f"<span>{escape(name)}</span>" for name in BROKERS)
    body = f"""
    <section class="hero">
      <p class="eyebrow">Trading journal &amp; analytics</p>
      <h1>See what actually makes you money.</h1>
      <p class="lede">TradzLog imports your broker history, rebuilds every trade, and shows you which setups,
        instruments and hours carry your P&amp;L, and which quietly drain it.</p>
      <div class="cta">
        <a class="btn btn-primary" href="/signup">Start your journal</a>
        <a class="btn" href="/features">See the features</a>
      </div>
    </section>
    <section class="pub-section">
      <p class="eyebrow">Import</p>
      <h2>Your trades, rebuilt from your broker's own files</h2>
      <p>Upload an export and TradzLog pairs the fills into trades, including scale-ins, partial exits,
        reversals and expired options. Imports never create duplicates, and the latest import can be undone.</p>
      <div class="broker-list">{brokers}</div>
    </section>
    <section class="pub-section">
      <p class="eyebrow">Understand</p>
      <h2>Everything a trader actually checks</h2>
      <div class="feature-grid">
        <article class="feature"><h3>Dashboard</h3><p>Net P&amp;L, win rate, profit factor, expectancy and drawdown, with an equity curve and P&amp;L calendar.</p></article>
        <article class="feature"><h3>Analytics</h3><p>Results by setup, instrument, weekday and hour, plus risk and streak analysis in R multiples.</p></article>
        <article class="feature"><h3>Journal</h3><p>Notes, emotions, mistakes and screenshots on every trade, and daily reviews you can search later.</p></article>
        <article class="feature"><h3>AI coaching</h3><p>On request, a written review of your recent trading: what is working, what is not, and what to try next.</p></article>
      </div>
    </section>
    <section class="pub-section">
      <h2>Built for one job, and private by default</h2>
      <p>Your journal is visible to you alone. Data is stored in the UK, screenshots sit in private encrypted
        storage, and you can export or delete everything yourself at any time.</p>
      <div class="cta"><a class="btn btn-primary" href="/signup">Create your account</a> <a class="btn" href="/security">How we protect your data</a></div>
    </section>"""
    return public_page(
        "Trading journal & analytics",
        body,
        signed_in=signed_in(),
        description="TradzLog imports your broker history and shows which setups, instruments and hours make or lose you money.",
    )


@router.get("/features", response_class=HTMLResponse)
def features() -> str:
    body = f"""
    <section class="hero"><p class="eyebrow">Features</p><h1>From raw fills to clear answers</h1>
      <p class="lede">Everything below works today in the private beta.</p></section>
    <section class="pub-section">
      <div class="feature-grid">
        <article class="feature"><h3>Broker import</h3><ul>
          <li>{", ".join(escape(name) for name in BROKERS[:-1])}, or any CSV with common column names</li>
          <li>Preview before anything is saved, with P&amp;L checked against the broker's own figures</li>
          <li>Stocks, options (×100), futures with point values, and options on futures</li>
          <li>Duplicate-safe re-imports and one-click undo</li></ul></article>
        <article class="feature"><h3>Trades</h3><ul>
          <li>Log trades by hand with entries, exits, stop and target</li>
          <li>Automatic P&amp;L, fees, R multiple and holding time</li>
          <li>Execution timeline and price ladder for every trade</li></ul></article>
        <article class="feature"><h3>Dashboard &amp; analytics</h3><ul>
          <li>Equity curve, daily P&amp;L calendar and drawdown</li>
          <li>Performance by setup, instrument, weekday and hour</li>
          <li>Win and loss streaks, risk and R distribution</li>
          <li>Per account or across all accounts, including prop-firm limits</li></ul></article>
        <article class="feature"><h3>Journal</h3><ul>
          <li>Trade notes, emotions, mistakes and lessons</li>
          <li>Screenshots on private storage</li>
          <li>Daily and weekly reviews</li></ul></article>
        <article class="feature"><h3>AI coaching</h3><ul>
          <li>Written reviews of your recent trades on request</li>
          <li>Follow-up questions in a chat</li>
          <li>Never run on your data unless you ask</li></ul></article>
        <article class="feature"><h3>Reports</h3><ul>
          <li>Performance report for any period</li>
          <li>Closed-trade CSV for your accountant</li>
          <li>Prop-firm rule tracking</li></ul></article>
      </div>
    </section>"""
    return public_page("Features", body, active="features", signed_in=signed_in())


@router.get("/pricing", response_class=HTMLResponse)
def pricing() -> str:
    body = """
    <section class="hero"><p class="eyebrow">Pricing</p><h1>Free during the private beta</h1>
      <p class="lede">Every feature is included while TradzLog is in beta. Paid plans will be announced
        before they start, and nobody is charged without choosing a plan first.</p></section>
    <section class="pub-section">
      <article class="price-card">
        <div class="label">Private beta</div>
        <div class="amount">£0</div>
        <div class="muted">No card required</div>
        <ul>
          <li>Unlimited trades and accounts</li>
          <li>All broker imports</li>
          <li>Full dashboard, analytics and journal</li>
          <li>AI coaching</li>
          <li>Export or delete your data at any time</li>
        </ul>
        <a class="btn btn-primary" href="/signup">Join the beta</a>
      </article>
    </section>"""
    return public_page("Pricing", body, active="pricing", signed_in=signed_in())


@router.get("/security", response_class=HTMLResponse)
def security() -> str:
    body = f"""
    <article class="prose">
      <h1>Security</h1>
      <p class="updated">How TradzLog protects your trading data.</p>
      <h2>Your data stays yours</h2>
      <p>Every page and every query is limited to your own data, and an automated test that tries to read one
        user's trades from another user's account runs on every release.</p>
      <h2>Where it is stored</h2>
      <p>The application and its database run on Amazon Web Services in London (eu-west-2). Screenshots are
        kept in private, encrypted storage and are only ever shown through links that expire after an hour.</p>
      <h2>Sign-in</h2>
      <p>Passwords are stored as bcrypt hashes, never in readable form. Sign-in sessions use a secure,
        HTTP-only cookie and are stored only as a one-way hash. Every form is protected against cross-site
        request forgery, and you can sign out every other device from <em>Settings → Security</em>.</p>
      <h2>In transit</h2>
      <p>All traffic uses HTTPS. Pages are sent with headers that prevent framing and content-type sniffing.</p>
      <h2>Backups</h2>
      <p>The database is backed up every night, and only the operator can reach the backups.</p>
      <h2>AI coaching</h2>
      <p>Your trades are only sent to our AI provider, Anthropic, when you ask for coaching, and by default Anthropic does
        not use data sent through its API to train its models.</p>
      <h2>Reporting a problem</h2>
      <p>If you think you have found a security issue, email {support_link()}. Please do not test against other
        people's accounts.</p>
    </article>"""
    return public_page("Security", body, active="security", signed_in=signed_in())


# --------------------------------------------------------------------- legal


def legal(title: str, sections: str) -> str:
    body = f"""<article class="prose"><h1>{escape(title)}</h1>
      <p class="updated">Last updated {LEGAL_UPDATED}. This version applies during the private beta.</p>{sections}</article>"""
    return public_page(title, body, signed_in=signed_in())


@router.get("/legal/terms", response_class=HTMLResponse)
def terms() -> str:
    return legal("Terms of service", f"""
      <h2>1. About these terms</h2>
      <p>These terms apply when you use TradzLog ("the service", "we", "us"). By creating an account you agree to
        them. If you do not agree, do not use the service.</p>
      <h2>2. The private beta</h2>
      <p>TradzLog is in a private beta. Features may change or be removed, and the service is provided as it is,
        without guarantees of availability. Keep your own records of anything you cannot afford to lose; you can
        export your data at any time.</p>
      <h2>3. Not financial advice</h2>
      <p>TradzLog records and analyses trades you have already made. Nothing in the service, including AI
        coaching, is financial, investment or tax advice, or a recommendation to buy or sell anything. You are
        solely responsible for your trading decisions. See the <a href="/legal/risk">risk disclaimer</a>.</p>
      <h2>4. Your account</h2>
      <p>You must be 18 or older. Keep your password secret and tell us at {support_link()} if you think someone
        else has used your account. You are responsible for activity under your account.</p>
      <h2>5. Your content</h2>
      <p>You own the trades, notes and screenshots you add. You allow us to store and process them only to run the
        service for you, as described in the <a href="/legal/privacy">privacy notice</a>. Only upload files you
        have the right to use.</p>
      <h2>6. Acceptable use</h2>
      <p>Do not try to access other people's data, disrupt or overload the service, reverse-engineer it beyond
        what the law allows, or use it for anything unlawful.</p>
      <h2>7. Accuracy</h2>
      <p>Imports and calculations are checked carefully, but broker files vary and mistakes can happen. Check
        figures that matter, such as tax figures, against your broker's statements.</p>
      <h2>8. Liability</h2>
      <p>Nothing in these terms limits liability for death or personal injury caused by negligence, for fraud, or
        for anything else that cannot be limited by law. Otherwise, we are not liable for trading losses or for
        indirect or consequential loss. During the free beta our total liability is limited to £100.</p>
      <h2>9. Ending your account</h2>
      <p>You can delete your account at any time from <em>Settings → Your data</em>. We may suspend accounts that
        break these terms, and will give notice where we reasonably can.</p>
      <h2>10. Changes and law</h2>
      <p>We will give notice of material changes before they apply. These terms are governed by the law of England
        and Wales, and its courts have jurisdiction.</p>
      <h2>11. Contact</h2>
      <p>{support_link()}</p>""")


@router.get("/legal/privacy", response_class=HTMLResponse)
def privacy() -> str:
    return legal("Privacy notice", f"""
      <h2>Who we are</h2>
      <p>TradzLog is the controller of the personal data described here. Contact us about privacy at
        {support_link()}.</p>
      <h2>What we collect</h2>
      <table>
        <tr><th>Data</th><th>Why</th><th>Lawful basis</th></tr>
        <tr><td>Name, email address, password (stored as a hash), timezone</td><td>Your account and sign-in</td><td>Contract</td></tr>
        <tr><td>Trading accounts, trades, executions, imported broker files, journal notes, screenshots</td><td>The journal and analytics you use the service for</td><td>Contract</td></tr>
        <tr><td>IP address and browser of signed-in sessions, request logs</td><td>Security, abuse prevention and fault finding</td><td>Legitimate interests</td></tr>
      </table>
      <p>We do not use advertising or analytics trackers, and we do not sell your data.</p>
      <h2>Who processes it for us</h2>
      <table>
        <tr><th>Provider</th><th>Purpose</th><th>Location</th></tr>
        <tr><td>Amazon Web Services</td><td>Hosting, database, screenshot storage, backups</td><td>London, UK</td></tr>
        <tr><td>Anthropic</td><td>AI coaching, only when you request it: your trade statistics and recent journal notes are sent to generate the review</td><td>United States, under contractual data-transfer safeguards</td></tr>
        <tr><td>Google Fonts</td><td>Delivering the fonts the pages use; your browser requests them directly from Google</td><td>United States / global</td></tr>
      </table>
      <h2>How long we keep it</h2>
      <p>For as long as you keep your account. When you delete your account, your data and screenshots are deleted
        at once and disappear from rolling backups within 30 days.</p>
      <h2>Your rights</h2>
      <p>You can access, correct, export and delete your data. Export and deletion are self-service in
        <em>Settings → Your data</em>; for anything else, email {support_link()}. You can also object to processing
        based on legitimate interests. If you are unhappy with how we handle your data you can complain to the
        Information Commissioner's Office at <a href="https://ico.org.uk/make-a-complaint/">ico.org.uk</a>.</p>
      <h2>Cookies</h2>
      <p>See the <a href="/legal/cookies">cookie notice</a>.</p>""")


@router.get("/legal/cookies", response_class=HTMLResponse)
def cookies() -> str:
    return legal("Cookie notice", """
      <p>TradzLog uses one cookie, and only because signing in needs it. Strictly necessary cookies do not need
        consent, so there is no cookie banner.</p>
      <table>
        <tr><th>Name</th><th>Purpose</th><th>Lasts</th></tr>
        <tr><td><code>__Host-tz_session</code></td><td>Keeps you signed in. It holds a random token; it does not track you elsewhere.</td><td>30 days from your last visit, or until you sign out</td></tr>
      </table>
      <p>Your light or dark theme choice is saved in your browser's local storage, not in a cookie, and never
        leaves your device. We use no analytics, advertising or social media cookies.</p>""")


@router.get("/legal/risk", response_class=HTMLResponse)
def risk() -> str:
    return legal("Risk disclaimer", f"""
      <p class="notice">{escape(RISK_NOTICE)}</p>
      <h2>No advice</h2>
      <p>TradzLog is a record-keeping and analysis tool. Its statistics, reports and AI coaching describe trades
        you have already made. They are not financial, investment or tax advice, and not a recommendation to buy,
        sell or hold any instrument. TradzLog is not authorised or regulated by the Financial Conduct Authority to
        give investment advice.</p>
      <h2>Past performance</h2>
      <p>Historical results, including your own, do not guarantee future returns. Patterns found in a small number
        of trades may be chance.</p>
      <h2>Leveraged products</h2>
      <p>Futures, options, CFDs and other leveraged products can lose money quickly, and some can lose more than
        you deposit. Only trade with money you can afford to lose, and seek independent advice if you are unsure.</p>
      <h2>Accuracy</h2>
      <p>Figures are calculated from the data you provide or import. Always check important figures against your
        broker's official statements.</p>""")


# --------------------------------------------------------------------- errors

ERROR_COPY = {
    403: ("Not allowed", "You don't have access to this page."),
    404: ("Page not found", "This page doesn't exist, or it isn't yours to see."),
    405: ("Not allowed", "That action isn't available here."),
    429: ("Slow down", "Too many attempts in a short time. Wait a minute, then try again."),
    500: ("Something went wrong", "An unexpected error stopped this page. It has been logged; please try again."),
}


def error_page(status_code: int, detail: str | None = None) -> str:
    title, text = ERROR_COPY.get(status_code, ("Something went wrong", "That request couldn't be completed."))
    if detail and status_code not in {404, 500}:
        text = detail
    home = "/dashboard" if signed_in() else "/"
    body = f"""<section class="error-page"><p class="code">Error {status_code}</p><h1>{escape(title)}</h1>
      <p>{escape(text)}</p><a class="btn btn-primary" href="{home}">Go to {"your dashboard" if signed_in() else "the home page"}</a></section>"""
    return public_page(title, body, signed_in=signed_in())
