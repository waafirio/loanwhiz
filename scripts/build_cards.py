"""Render the demo's title/section cards to 1920x1080 PNGs via headless Chromium.

Self-contained: no prodemo involvement. Output → web/public/card-*.png, served
same-origin and passed to prodemo as the card `image` field. LIGHT theme to
match the deck and so the Waafir logo (green wordmark) reads correctly.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
import base64
_logo_svg = (Path(__file__).resolve().parent.parent / "web/public/waafir-logo.svg").read_bytes()
LOGO = "data:image/svg+xml;base64," + base64.b64encode(_logo_svg).decode()  # inline; no network needed
OUT = ROOT / "web/public"

# Waafir brand palette — dark green + gold ACCENTS on a white background
BG="#FFFFFF"; INK="#1C2B23"; SLATE="#5A6B61"; GREEN="#00512F"; GOLD="#C28A12"

BASE_CSS = f"""
  *{{margin:0;box-sizing:border-box}}
  html,body{{width:1920px;height:1080px}}
  body{{
    background:{BG}; color:{INK};
    font-family:'DejaVu Sans','Liberation Sans',system-ui,sans-serif;
    position:relative; overflow:hidden;
  }}
  .rail{{position:absolute;left:0;top:0;bottom:0;width:14px;background:{GREEN}}}
  .logo{{position:absolute;top:64px;left:96px;height:48px}}
  .wrap{{position:absolute;left:96px;right:120px;top:50%;transform:translateY(-50%)}}
  .eyebrow{{color:{GOLD};font-weight:700;font-size:30px;letter-spacing:3px;margin-bottom:34px}}
  h1{{font-size:128px;font-weight:800;letter-spacing:-2px;line-height:1;color:{GREEN}}}
  .sub{{font-size:48px;color:{SLATE};margin-top:26px;font-weight:600}}
  .tiny{{font-size:26px;color:#94A09A;margin-top:40px;letter-spacing:1px}}
  ul{{list-style:none;margin-top:6px}}
  li{{font-size:50px;line-height:1.6;font-weight:600;display:flex;align-items:baseline;gap:24px;color:{INK}}}
  li .b{{color:{GOLD};font-weight:800}}
  .big{{font-size:92px;font-weight:800;letter-spacing:-1px;line-height:1.05;color:{GREEN}}}
  .mid{{font-size:42px;color:{SLATE};margin-top:34px;font-weight:500;line-height:1.4;max-width:1300px}}
  .email{{font-size:64px;color:{GREEN};font-weight:800;margin-top:46px;letter-spacing:.5px}}
  /* two-column framing card */
  .cols{{display:flex;gap:84px;align-items:flex-start}}
  .col{{flex:1}}
  .col.right{{border-left:2px solid #E4E8EE;padding-left:78px}}
  .cols li{{font-size:38px;line-height:1.5}}
  /* primitives grid card */
  .subtle{{color:{SLATE};font-size:24px;margin:-18px 0 30px;font-weight:500}}
  .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:40px 64px}}
  .nm{{font-family:'DejaVu Sans Mono','Liberation Mono',monospace;font-size:29px;font-weight:700;color:{GREEN}}}
  .nm.gold{{color:{GOLD}}}
  .ds{{color:{SLATE};font-size:22px;margin-top:8px}}
"""

def card(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS}</style></head><body>" \
           f"<div class='rail'></div><img class='logo' src='{LOGO}'/>{body}</body></html>"

def bullets(items):
    return "<ul>" + "".join(f"<li><span class='b'>{m[0]}</span><span>{m[1]}</span></li>" for m in items) + "</ul>"

CARDS = {
  "card-intro": card(
    "<div class='wrap'>"
    "<h1>LoanWhiz</h1>"
    "<div class='sub'>A Structured Finance Agent Framework</div>"
    "<div class='tiny'>BARCELONA AI TINKERERS · STRUCTURED FINANCE HACKATHON 2026</div>"
    "</div>"),
  "card-challenge": card(
    "<div class='wrap'>"
    "<div class='eyebrow'>THE CHALLENGE</div>" +
    bullets([("▸","An open, structured-finance-native agent framework"),
             ("▸","A library of domain primitives"),
             ("▸","+ a dynamic orchestration layer"),
             ("→","reliable, auditable agents for any question")]) +
    "</div>"),
  "card-answer": card(
    "<div class='wrap'>"
    "<div class='eyebrow'>THE PRINCIPLE</div>" +
    bullets([("▸","Deterministic primitives do the computation"),
             ("▸","The LLM orchestrates — it never does arithmetic"),
             ("▸","Compile the prospectus into a real, working model"),
             ("→","…so we're certain we've understood the deal")]) +
    "</div>"),
  "card-framing": card(
    "<div class='wrap'><div class='cols'>"
    "<div class='col'>"
    "<div class='eyebrow'>THE CHALLENGE</div>" +
    bullets([("▸", "Open, structured-finance-native agent framework"),
             ("▸", "A library of domain primitives + orchestration"),
             ("▸", "Reliable, auditable agents for any question")]) +
    "</div>"
    "<div class='col right'>"
    "<div class='eyebrow'>WHAT WE BUILT</div>" +
    bullets([("▸", "Deterministic primitives do the maths"),
             ("▸", "The LLM orchestrates — never the arithmetic"),
             ("▸", "Compile each prospectus into a real, working model")]) +
    "</div>"
    "</div></div>"),
  "card-primitives": card(
    "<div class='wrap'>"
    "<div class='eyebrow'>THE LIBRARY</div>"
    "<div class='subtle'>Nine typed, governed primitives — each returns output + confidence + citations + audit</div>"
    "<div class='grid'>"
    "<div class='cell'><div class='nm gold'>prospectus_extractor</div><div class='ds'>prospectus → typed model</div></div>"
    "<div class='cell'><div class='nm'>esma_tape_normaliser</div><div class='ds'>loan tapes → pool analytics</div></div>"
    "<div class='cell'><div class='nm'>collections_aggregator</div><div class='ds'>per-period cashflows</div></div>"
    "<div class='cell'><div class='nm'>waterfall_runner</div><div class='ds'>execute the priority of payments</div></div>"
    "<div class='cell'><div class='nm'>covenant_monitor</div><div class='ds'>track trigger breaches</div></div>"
    "<div class='cell'><div class='nm'>report_verifier</div><div class='ds'>reconcile vs investor reports</div></div>"
    "<div class='cell'><div class='nm'>cashflow_projector</div><div class='ds'>base / stress projections</div></div>"
    "<div class='cell'><div class='nm'>audit_logger</div><div class='ds'>FINOS provenance per call</div></div>"
    "<div class='cell'><div class='nm'>multi_period_runner</div><div class='ds'>multi-period state</div></div>"
    "</div></div>"),
  "card-outro": card(
    "<div class='wrap'>"
    "<div class='big'>Let's build on it together.</div>"
    "<div class='mid'>We'd love to meet others in structured finance.</div>"
    "<div class='email'>hello@waafir.io</div>"
    "</div>"),
}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1920,"height":1080}, device_scale_factor=2)
    for name, html in CARDS.items():
        pg.set_content(html, wait_until="load")
        out = OUT / f"{name}.png"
        pg.screenshot(path=str(out), clip={"x":0,"y":0,"width":1920,"height":1080})
        print("wrote", out, out.stat().st_size, "bytes")
    b.close()
