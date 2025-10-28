import os
from datetime import date
from typing import Optional, List, Dict
from pydantic import BaseModel
from enum import Enum

# Import types from main without circular import by redefining minimal contract here
class BinType(str, Enum):
    blue = "blue"
    green = "green"
    black = "black"


class ScraperResult(BaseModel):
    postcode: str
    next_collection_date: date
    bins: list[BinType]


async def fetch_rbwm_schedule(postcode: str) -> ScraperResult:
    """
    Fetch schedule from RBWM's public site using Playwright.
    Notes:
      - Requires 'playwright' and installed browsers: `python -m playwright install`.
      - Selectors may need adjustment depending on site changes.
    """
    from playwright.async_api import async_playwright

    base_url = os.getenv(
        "RBWM_BIN_URL",
        # Default guess; adjust if RBWM changes structure
        "https://www.rbwm.gov.uk/home/bins-and-recycling/find-your-bin-day",
    )

    normalized = postcode.strip().upper()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

            # Try common patterns to locate the postcode input
            input_loc = page.get_by_label("Postcode", exact=False)
            if not await input_loc.count():
                input_loc = page.locator('input[name="postcode"], input[placeholder*="post" i]')

            await input_loc.first.fill(normalized)

            # Click a search or submit button
            btn = page.get_by_role("button", name=lambda n: n and ("find" in n.lower() or "search" in n.lower() or "lookup" in n.lower()))
            if not await btn.count():
                btn = page.locator('button, input[type="submit"]')
            await btn.first.click()

            # Wait for results area; try a few heuristics
            # Look for any text containing 'collection'
            await page.wait_for_timeout(500)  # brief settle
            results = page.locator("text=/collection/i")
            await results.first.wait_for(timeout=60000)

            # Extract the page text and do heuristic parsing
            text = await page.inner_text("body")
            # Very rough parsing: look for a date-like pattern. This is a placeholder
            # for a site-specific parser. Adjust to actual RBWM markup.
            import re
            # Match formats like 'Monday 14 October 2024' or '14/10/2024'
            m = re.search(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+([0-3]?\d)\s+([A-Za-z]+)\s+(20\d{2})", text)
            if m:
                # Map month name to number
                from datetime import datetime
                dt = datetime.strptime(" ".join(m.groups()), "%A %d %B %Y").date()
                date_idx = m.start()
            else:
                m2 = re.search(r"([0-3]?\d)/(0?\d|1[0-2])/(20\d{2})", text)
                if not m2:
                    raise RuntimeError("Unable to locate next collection date in page text. Adjust selectors/parsing.")
                from datetime import datetime
                day, month, year = m2.groups()
                dt = date(int(year), int(month), int(day))
                date_idx = m2.start()

            # Determine bins: enforce rule blue + (green|black). Prefer keywords near the date.
            bins: list[BinType] = [BinType.blue]
            lowered = text.lower()
            window_radius = 300
            start = max(0, date_idx - window_radius)
            end = min(len(lowered), date_idx + window_radius)
            window = lowered[start:end]

            # helper to find nearest occurrence index of any term
            def nearest_pos(text_: str, terms: list[str]):
                positions = [text_.find(t) for t in terms]
                positions = [p for p in positions if p != -1]
                return min(positions) if positions else None

            green_pos = nearest_pos(window, ["garden", "green bin", "garden waste"])  # garden
            black_pos = nearest_pos(window, ["rubbish", "refuse", "black bin"])       # rubbish

            if green_pos is not None and black_pos is not None:
                chosen = BinType.green if green_pos <= black_pos else BinType.black
            elif green_pos is not None:
                chosen = BinType.green
            elif black_pos is not None:
                chosen = BinType.black
            else:
                # broaden search across page
                green_any = any(tok in lowered for tok in ["garden", "green bin", "garden waste"])
                black_any = any(tok in lowered for tok in ["rubbish", "refuse", "black bin"])
                if green_any and not black_any:
                    chosen = BinType.green
                elif black_any and not green_any:
                    chosen = BinType.black
                else:
                    chosen = BinType.black

            bins.append(chosen)

            # Post-process to enforce RBWM rule: always blue + exactly one of (green|black)
            try:
                # Build regexes to locate the chosen date within the text to get context
                wd = dt.strftime("%A")
                day_num = dt.day
                month_name = dt.strftime("%B")
                year_num = dt.year
                import re as _re
                patterns = [
                    _re.compile(fr"{wd}\\s+0?{day_num}\\s+{month_name}\\s+{year_num}", _re.I),
                    _re.compile(fr"0?{day_num}/0?{dt.month}/{year_num}"),
                ]
                idx = None
                for pat in patterns:
                    m = pat.search(text)
                    if m:
                        idx = m.start()
                        break

                # Choose the companion bin using context near the date if available
                companion = None
                lowered = text.lower()
                if idx is not None:
                    radius = 300
                    s = max(0, idx - radius)
                    e = min(len(lowered), idx + radius)
                    win = lowered[s:e]
                    green_hits = any(t in win for t in ["garden", "green bin", "garden waste"])
                    black_hits = any(t in win for t in ["rubbish", "refuse", "black bin"])
                    if green_hits and not black_hits:
                        companion = BinType.green
                    elif black_hits and not green_hits:
                        companion = BinType.black
                    elif green_hits and black_hits:
                        # Pick whichever term appears first in the window
                        def first_pos(txt, terms):
                            ps = [txt.find(t) for t in terms]
                            ps = [p for p in ps if p != -1]
                            return min(ps) if ps else None
                        gp = first_pos(win, ["garden", "green bin", "garden waste"]) or 10**9
                        bp = first_pos(win, ["rubbish", "refuse", "black bin"]) or 10**9
                        companion = BinType.green if gp <= bp else BinType.black

                if companion is None:
                    # Fall back to whole page signal
                    green_any = any(t in lowered for t in ["garden", "green bin", "garden waste"])
                    black_any = any(t in lowered for t in ["rubbish", "refuse", "black bin"])
                    if green_any and not black_any:
                        companion = BinType.green
                    elif black_any and not green_any:
                        companion = BinType.black
                    else:
                        companion = BinType.black

                bins = [BinType.blue, companion]
            except Exception:
                # If any issue, still enforce a safe two-bin structure
                bins = [BinType.blue, (BinType.black if BinType.black in bins else BinType.green)]

            return ScraperResult(postcode=normalized, next_collection_date=dt, bins=bins)

        finally:
            await ctx.close()
            await browser.close()


class RBWMAddress(BaseModel):
    uprn: str
    address: str


async def fetch_rbwm_addresses(postcode: str) -> List[RBWMAddress]:
    from playwright.async_api import async_playwright
    normalized = postcode.strip().upper()
    url = f"https://forms.rbwm.gov.uk/bincollections?postcode={normalized.replace(' ', '+')}&submit=Search+for+address"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Wait briefly for content to render
            try:
                await page.get_by_text("Bin collections", exact=False).first.wait_for(timeout=5000)
            except Exception:
                pass

            # Find all "Select this address" links using multiple strategies
            links = page.locator("a", has_text="Select this address")
            count = await links.count()
            if count == 0:
                links = page.get_by_role("link", name="Select this address")
                count = await links.count()
            if count == 0:
                links = page.locator("a:has-text('Select this address')")
                count = await links.count()
            results: List[RBWMAddress] = []
            for i in range(count):
                a = links.nth(i)
                href = await a.get_attribute("href")
                if not href:
                    continue
                # Extract uprn from query (format: ?uprn=123456)
                import urllib.parse as _up
                parsed = _up.urlparse(href)
                qs = _up.parse_qs(parsed.query)
                uprn = qs.get("uprn", [None])[0]
                if not uprn:
                    # Sometimes href might be absolute without query; try splitting
                    if "uprn=" in href:
                        uprn = href.split("uprn=")[-1].split("&")[0]
                if not uprn:
                    continue

                # Try to capture the address from the same table row's first cell
                addr_text = ""
                try:
                    row = a.locator("xpath=ancestor::tr[1]")
                    if await row.count():
                        first_td = row.locator("td").first
                        if await first_td.count():
                            addr_text = (await first_td.inner_text()).strip()
                except Exception:
                    addr_text = ""
                # Fallbacks: parent container text without the link label
                if not addr_text:
                    addr_text = await a.evaluate(
                        "el => (el.parentElement && el.parentElement.innerText) || ''"
                    )
                    if addr_text:
                        addr_text = addr_text.replace("Select this address", "").strip()
                results.append(RBWMAddress(uprn=uprn, address=addr_text or uprn))

            return results
        finally:
            await ctx.close()
            await browser.close()


async def fetch_rbwm_schedule_by_uprn(uprn: str) -> ScraperResult:
    from playwright.async_api import async_playwright

    url = f"https://forms.rbwm.gov.uk/bincollections?uprn={uprn}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Find the bin collections widget
            container = page.locator(".widget-bin-collections")
            await container.first.wait_for(timeout=60000)

            # Extract table rows: Service | Date
            rows = container.locator("table tbody tr")
            rc = await rows.count()
            services_by_date: Dict[date, List[str]] = {}

            # Helper: strip ordinal suffixes
            def _strip_ordinal(d: str) -> str:
                import re as _re
                return _re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", d)

            from datetime import datetime as _dt

            for i in range(rc):
                r = rows.nth(i)
                cols = r.locator("td")
                if await cols.count() < 2:
                    continue
                service = (await cols.nth(0).inner_text()).strip()
                date_text = (await cols.nth(1).inner_text()).strip()
                date_text = _strip_ordinal(date_text)
                try:
                    d = _dt.strptime(date_text, "%d %B %Y").date()
                except Exception:
                    continue
                services_by_date.setdefault(d, []).append(service)

            if not services_by_date:
                raise RuntimeError("No service dates found on UPRN page")

            today = date.today()
            future_dates = sorted([d for d in services_by_date.keys() if d >= today])
            target = future_dates[0] if future_dates else sorted(services_by_date.keys())[0]
            services = services_by_date[target]

            # Map services to bins: always blue + (black if Refuse present else green if Garden present)
            has_refuse = any("refuse" in s.lower() for s in services)
            has_garden = any("garden" in s.lower() for s in services)
            bins = [BinType.blue, (BinType.black if has_refuse else (BinType.green if has_garden else BinType.black))]

            # Extract postcode from the Address line near the widget header if present
            full_text = await container.first.inner_text()
            import re as _re
            pc_match = _re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*(\d[ABD-HJLN-UW-Z]{2})\b", full_text.replace("\n", " "))
            postcode = (pc_match.group(0) if pc_match else "").upper()
            return ScraperResult(postcode=postcode, next_collection_date=target, bins=bins)
        finally:
            await ctx.close()
            await browser.close()


# --- HTTP-based fallbacks (no browser) ---

def fetch_rbwm_addresses_http(postcode: str) -> List[RBWMAddress]:
    import httpx
    from bs4 import BeautifulSoup

    normalized = postcode.strip().upper().replace(" ", "+")
    url = f"https://forms.rbwm.gov.uk/bincollections?postcode={normalized}&submit=Search+for+address"
    headers = {"User-Agent": "Bindicator/0.1 (+https://github.com/)"}
    with httpx.Client(follow_redirects=True, timeout=20.0, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[RBWMAddress] = []
        for a in soup.find_all("a"):
            if not a.get_text(strip=True).lower().startswith("select this address"):
                continue
            href = a.get("href") or ""
            if "uprn=" not in href:
                continue
            uprn = href.split("uprn=")[-1].split("&")[0]
            # Address text is typically in the same table row's first cell
            address_text = ""
            tr = a.find_parent("tr")
            if tr:
                tds = tr.find_all("td")
                if tds:
                    address_text = tds[0].get_text(" ", strip=True)
            if not address_text and a.parent:
                address_text = a.parent.get_text(" ", strip=True)
                address_text = address_text.replace("Select this address", "").strip()
            results.append(RBWMAddress(uprn=uprn, address=address_text or uprn))
        return results


def fetch_rbwm_schedule_by_uprn_http(uprn: str) -> ScraperResult:
    import httpx
    from bs4 import BeautifulSoup
    from datetime import datetime as _dt
    import re as _re

    url = f"https://forms.rbwm.gov.uk/bincollections?uprn={uprn}"
    headers = {"User-Agent": "Bindicator/0.1 (+https://github.com/)"}
    with httpx.Client(follow_redirects=True, timeout=20.0, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        widget = soup.select_one(".widget-bin-collections")
        if not widget:
            raise RuntimeError("RBWM schedule widget not found")

        # Parse the table rows
        services_by_date: Dict[date, List[str]] = {}
        for row in widget.select("table tbody tr"):
            tds = row.find_all("td")
            if len(tds) < 2:
                continue
            service = tds[0].get_text(strip=True)
            date_text = tds[1].get_text(strip=True)
            date_text = _re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", date_text)
            try:
                d = _dt.strptime(date_text, "%d %B %Y").date()
            except Exception:
                continue
            services_by_date.setdefault(d, []).append(service)

        if not services_by_date:
            raise RuntimeError("No service dates found in RBWM table")

        today = date.today()
        future_dates = sorted([d for d in services_by_date.keys() if d >= today])
        target = future_dates[0] if future_dates else sorted(services_by_date.keys())[0]
        services = services_by_date[target]

        has_refuse = any("refuse" in s.lower() for s in services)
        has_garden = any("garden" in s.lower() for s in services)
        bins = [BinType.blue, (BinType.black if has_refuse else (BinType.green if has_garden else BinType.black))]
        # Extract postcode from Address text around widget
        text = widget.get_text(" ", strip=True)
        import re as _re
        m = _re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*(\d[ABD-HJLN-UW-Z]{2})\b", text)
        postcode = (m.group(0) if m else "").upper()
        return ScraperResult(postcode=postcode, next_collection_date=target, bins=bins)
