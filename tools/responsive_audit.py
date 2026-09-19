"""Responsive audit — loads pages at a matrix of viewports and measures.

Not a test and not wired into CI: it is a measuring instrument for a human,
kept in tools/ beside the other one-off developer utilities. Run it against the
local stack (`make up` plus `make frontend-build`).

    uv run --no-project python tools/responsive_audit.py
    uv run --no-project python tools/responsive_audit.py --shots   # + screenshots

WHAT IT CHECKS, and why each one is worth automating rather than eyeballing:

  overflow   documentElement.scrollWidth > viewport width. The single most
             common responsive defect and the least visible on a desktop: it
             shows as a horizontal scrollbar nobody notices until a phone.
             The offending elements are named, since "the page is 40px too
             wide" on its own is not actionable.
  tap        Interactive elements smaller than 44x44 CSS px on touch-sized
             viewports (Apple HIG; Android asks 48). Measured only where a
             finger is the pointer.
  text       Computed font-size below 12px on visible text.
  clipped    Text wider than its own container, i.e. cut off or overlapping.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
SHOTS_DIR = Path(__file__).resolve().parent.parent / "responsive-shots"

# Device classes as the request framed them. Widths are CSS pixels, which is
# what a layout actually sees — a "14-inch laptop" at 1920 physical with 150%
# Windows scaling presents 1280, so the physical diagonal is a poor guide and
# the effective width is the thing to design against.
VIEWPORTS = [
    # (class, label, width, height, touch)
    ("big screen", "27in QHD", 2560, 1440, False),
    ("big screen", "24in FHD", 1920, 1080, False),
    ("laptop 16-14in", "16in scaled", 1536, 960, False),
    ("laptop 16-14in", "15in", 1440, 900, False),
    ("laptop 16-14in", "14in scaled", 1280, 800, False),
    ("tablet 12-8in", "12.9in portrait", 1024, 1366, True),
    ("tablet 12-8in", "11in portrait", 834, 1194, True),
    ("tablet 12-8in", "10.9in portrait", 820, 1180, True),
    ("tablet 12-8in", "8.3in portrait", 744, 1133, True),
    ("tablet 12-8in", "11in landscape", 1194, 834, True),
    ("phone big", "iPhone 16 Pro Max", 430, 932, True),
    ("phone big", "Pixel 8 Pro", 412, 915, True),
    ("phone small", "iPhone SE", 375, 667, True),
    ("phone small", "Android small", 360, 640, True),
    ("phone small", "iPhone 5/SE1", 320, 568, True),
]

PUBLIC_PAGES = [
    ("home", "/"),
    ("quienes-somos", "/quienes-somos"),
    ("metodologia", "/nuestra-metodologia"),
    ("ods", "/ods"),
    ("sobre-la-academia", "/sobre-la-academia"),
    ("faq", "/faq"),
    ("aviso-legal", "/aviso-legal"),
]

APP_PAGES = [
    ("app-login", "/app/login/"),
    ("app-home", "/app/"),
    ("app-students", "/app/students/"),
    ("app-payments", "/app/payments/"),
    ("app-schedule", "/app/schedule/"),
    ("app-management", "/app/management/"),
    ("app-expenses", "/app/expenses/"),
    ("app-reports", "/app/reports/"),
    ("app-apps", "/app/apps/"),
    ("app-database", "/app/database/"),
    ("app-student-create", "/app/students/create/"),
    ("app-waiting", "/app/students/waiting/"),
]

# Runs in the page. Kept as one evaluate() so a page is measured in a single
# round trip rather than a dozen.
PROBE = """
() => {
  const vw = document.documentElement.clientWidth;
  const out = { vw, scrollWidth: document.documentElement.scrollWidth, overflow: [], tap: [], text: [], clipped: [] };

  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const describe = (el) => {
    const cls = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '';
    const txt = (el.textContent || '').trim().slice(0, 30);
    return el.tagName.toLowerCase() + cls + (txt ? ` "${txt}"` : '');
  };

  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();

    // Overflow: only the OUTERMOST offender is interesting — a wide parent
    // makes every child look wide too, which buries the cause.
    if (r.right > vw + 1 || r.left < -1) {
      const parent = el.parentElement;
      const pr = parent ? parent.getBoundingClientRect() : null;
      const parentAlreadyWide = pr && (pr.right > vw + 1 || pr.left < -1);
      if (!parentAlreadyWide) {
        out.overflow.push({ el: describe(el), left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) });
      }
    }

    const style = getComputedStyle(el);
    const leaf = !el.querySelector('*');
    if (leaf && (el.textContent || '').trim()) {
      const size = parseFloat(style.fontSize);
      if (size && size < 12) out.text.push({ el: describe(el), size: Math.round(size * 10) / 10 });
      if (el.scrollWidth > el.clientWidth + 2 && style.overflow !== 'auto' && style.overflow !== 'scroll'
          && style.textOverflow !== 'ellipsis' && style.overflowX !== 'auto') {
        out.clipped.push({ el: describe(el), content: el.scrollWidth, box: el.clientWidth });
      }
    }
  }

  for (const el of document.querySelectorAll('a, button, input, select, textarea, [role=button]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 44 || r.height < 44) {
      out.tap.push({ el: describe(el), w: Math.round(r.width), h: Math.round(r.height) });
    }
  }
  return out;
}
"""


def audit(pages, cookies=None, shots=False):
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for cls, label, w, h, touch in VIEWPORTS:
            ctx = browser.new_context(
                viewport={"width": w, "height": h},
                has_touch=touch,
                is_mobile=touch,
                device_scale_factor=1,
            )
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            for name, path in pages:
                try:
                    resp = page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=30000)
                    status = resp.status if resp else 0
                except Exception as exc:  # noqa: BLE001 - a failed load is a result, not a crash
                    results.append({"class": cls, "label": label, "w": w, "page": name, "error": str(exc)[:80]})
                    continue
                page.wait_for_timeout(250)
                data = page.evaluate(PROBE)
                data.update({"class": cls, "label": label, "w": w, "h": h, "page": name, "status": status, "touch": touch})
                results.append(data)
                if shots:
                    SHOTS_DIR.mkdir(exist_ok=True)
                    page.screenshot(path=str(SHOTS_DIR / f"{name}__{w}x{h}.png"), full_page=False)
            ctx.close()
        browser.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", action="store_true", help="audit the Django app instead of the public site")
    ap.add_argument("--shots", action="store_true", help="also write screenshots to responsive-shots/")
    ap.add_argument("--json", help="write raw results here")
    args = ap.parse_args()

    cookies = None
    pages = PUBLIC_PAGES
    if args.app:
        pages = APP_PAGES
        session = Path(__file__).with_name(".session_cookie")
        if not session.exists():
            print("No session cookie. Run tools/make_session.py first.", file=sys.stderr)
            return 2
        cookies = [{
            "name": "sessionid", "value": session.read_text().strip(),
            "domain": "localhost", "path": "/",
        }]

    results = audit(pages, cookies=cookies, shots=args.shots)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
