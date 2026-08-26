#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Brief Quotidien / Veille Stratégique -> Telegram + Page HTML fusionnée + Archive.
RSS feeds -> Gemini -> HTML dynamique + briefs.json + archive.json
Aucune dépendance externe (stdlib only).

Usage : python brief.py daily        (Brief quotidien 06:00 Tunis)
        python brief.py strategy     (Veille stratégique 06:00 Tunis)
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

MODE = (sys.argv[1] if len(sys.argv) > 1 else "daily").strip().lower()
if MODE not in ["daily", "strategy"]:
    MODE = "daily"

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "https://abtmohamedmoussa-svg.github.io/veille-bfi/")

TODAY = datetime.date.today().strftime("%d/%m/%Y")
TODAY_ISO = datetime.date.today().isoformat()

UA = "Mozilla/5.0 (compatible; VeilleBot/1.0)"
BRIEFS_FILE = Path("briefs.json")
ARCHIVE_FILE = Path("archive.json")
INDEX_HTML = Path("index.html")

# --------------------------------------------------------------------------
# SOURCES (flux RSS)
# --------------------------------------------------------------------------

DAILY_FEEDS = [
    ("Webmanagercenter", "https://www.webmanagercenter.com/feed/"),
    ("African Manager", "https://africanmanager.com/feed/"),
    ("Tunisie Numerique", "https://www.tunisienumerique.com/feed/"),
    ("Business News", "https://www.businessnews.com.tn/feed"),
    ("Le Monde Economie", "https://www.lemonde.fr/economie/rss_full.xml"),
    ("France24 Eco", "https://www.france24.com/fr/économie/rss"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
]

STRATEGY_FEEDS = [
    ("Finextra", "https://www.finextra.com/rss/headlines.aspx"),
    ("TechCabal", "https://techcabal.com/feed/"),
    ("Wamda", "https://www.wamda.com/feed"),
    ("Rest of World", "https://restofworld.org/feed/latest/"),
    ("Sifted", "https://sifted.eu/feed"),
    ("Le Monde Economie", "https://www.lemonde.fr/economie/rss_full.xml"),
]

FEEDS = DAILY_FEEDS if MODE == "daily" else STRATEGY_FEEDS
PER_FEED = 6

# --------------------------------------------------------------------------
# COLLECTE RSS
# --------------------------------------------------------------------------

def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_feed(name, url, retries=2):
    """Fetch feed with retry logic per source."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            root = ET.fromstring(raw)
            break
        except Exception as e:
            if attempt < retries:
                print(f"[feed] {name} retry {attempt}/{retries} ({type(e).__name__})")
                time.sleep(3)
                continue
            print(f"[feed] {name} : ignore ({type(e).__name__})")
            return []

    items = []
    # RSS 2.0
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = strip_html(it.findtext("description") or "")
        date = (it.findtext("pubDate") or "").strip()
        link = (it.findtext("link") or "").strip()
        if title:
            items.append((name, title, desc[:220], date, link))
        if len(items) >= PER_FEED:
            break
    # Atom
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.iter(ns + "entry"):
            title = (e.findtext(ns + "title") or "").strip()
            desc = strip_html(e.findtext(ns + "summary") or e.findtext(ns + "content") or "")
            date = (e.findtext(ns + "updated") or e.findtext(ns + "published") or "").strip()
            # Atom uses <link href="..."> attribute
            link_elem = e.find(ns + "link")
            link = (link_elem.get("href") or "") if link_elem is not None else ""
            if title:
                items.append((name, title, desc[:220], date, link))
            if len(items) >= PER_FEED:
                break
    print(f"[feed] {name} : {len(items)} items")
    return items


def gather():
    all_items = []
    for name, url in FEEDS:
        all_items.extend(fetch_feed(name, url))
    return all_items


def build_context(items):
    """Build context with URLs for Gemini."""
    lines = []
    for name, title, desc, date, link in items:
        line = f"- [{name}] {title}"
        if date:
            line += f" ({date})"
        if link:
            line += f" | Source: {link}"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return "\n".join(lines)

# --------------------------------------------------------------------------
# PROMPTS
# --------------------------------------------------------------------------

DAILY_INSTRUCTIONS = f"""Tu es l'assistant de veille d'un responsable de la Banque de financement et d'investissement (Attijari Bank Tunisie). Nous sommes le {TODAY}.

A partir UNIQUEMENT des titres reels ci-dessous, redige un brief presse du jour. Priorite : economie, finance, marches, banque ; plus les grands titres politiques/macro (Tunisie + international) utiles a un banquier d'affaires. Ignore people, faits divers, sport, communiques marketing.

REGLES CRUCIALES :
- n'invente AUCUN chiffre ni fait qui ne soit pas dans les titres fournis
- Reste factuel et neutre
- Chaque puce DOIT contenir un verbe d'action et un chiffre/% si dispo
- Pas de puce vide ou de remplissage
- CITE LA SOURCE ET L'URL après chaque puce (format: [Source](URL))

SORTIE : produis UNIQUEMENT le digest ci-dessous (pas d'intro, pas de liens). Chaque puce = 1 phrase claire avec SOURCE ET URL. Format EXACT :

🗞️ Brief du {TODAY}

🇹🇳 TUNISIE
• [titre court] : [1 phrase] — [Source](URL)

🌍 INTERNATIONAL
• [titre court] : [1 phrase] — [Source](URL)

📈 MARCHÉS & TAUX
• [mouvement] : [1 phrase] — [Source](URL)
"""

STRATEGY_INSTRUCTIONS = f"""Tu es l'assistant de veille strategique d'un responsable de la Banque de financement et d'investissement (Attijari Bank Tunisie). Nous sommes le {TODAY}.

A partir UNIQUEMENT des titres reels ci-dessous (24 dernieres heures), redige une "Veille Disruption & Strategie". Un sujet n'entre QUE s'il modifie : la chaine de valeur (un maillon disparait/se deplace), qui capte la marge, la structure de couts (ordre de grandeur), ou la regle du jeu (reglementaire/techno/acces marche). EXCLUS : levees sans structure, exits, prix, nominations, classements, partenariats vides, communiques, cours de bourse.

REGLES CRUCIALES :
- n'invente AUCUN chiffre absent des titres
- Qualite > quantite ; un bloc peut rester vide
- Plafond 5 sujets par bloc
- Chaque puce DOIT contenir un mecanisme clair (pas juste "X leve Y€")
- CITE LA SOURCE ET L'URL après chaque puce (format: [Source](URL))

SORTIE : produis UNIQUEMENT le digest ci-dessous (pas de liens externes). Chaque puce = 1 a 2 phrases avec SOURCE ET URL. Format EXACT :

📊 Veille — {TODAY}

A · BANQUE
• [Acteur] : [1-2 phrases avec mecanisme] — [Source](URL)

B · SECTEURS
• [Acteur] : [1-2 phrases avec mecanisme] — [Source](URL)

C · MACRO
• [Etat] : [1-2 phrases avec mecanisme] — [Source](URL)
"""

INSTRUCTIONS = DAILY_INSTRUCTIONS if MODE == "daily" else STRATEGY_INSTRUCTIONS

# --------------------------------------------------------------------------
# GEMINI
# --------------------------------------------------------------------------

def call_gemini(prompt, max_retries=4):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                out = json.load(resp)
            cand = out["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise RuntimeError("texte vide (finishReason="
                                   + str(cand.get("finishReason")) + ")")
            return text
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} : " + e.read().decode('utf-8', 'ignore')[:200]
            if e.code in (429, 500, 503) and attempt < max_retries:
                print(f"[gemini] {e.code}, pause 20s (essai {attempt}/{max_retries})")
                time.sleep(20)
                continue
            raise
        except RuntimeError as e:
            last = str(e)
            if attempt < max_retries:
                print(f"[gemini] {e}, nouvelle tentative dans 8s")
                time.sleep(8)
                continue
            raise
    raise RuntimeError("Gemini a echoue : " + str(last))


def send_telegram(text, page_url=None):
    """Send message to Telegram with optional page link. Returns (ok, error_msg)."""
    if page_url:
        text += f"\n\n🔗 Page complète : {page_url}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text[:4090],
               "disable_web_page_preview": True}
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)
        return (res.get("ok", False), res.get("description", ""))
    except Exception as e:
        return (False, str(e))

# --------------------------------------------------------------------------
# BRIEFS MANAGER
# --------------------------------------------------------------------------

def load_briefs():
    """Load briefs from JSON, or return empty dict."""
    if BRIEFS_FILE.exists():
        try:
            with open(BRIEFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_briefs(briefs):
    """Save briefs to JSON."""
    try:
        with open(BRIEFS_FILE, "w", encoding="utf-8") as f:
            json.dump(briefs, f, ensure_ascii=False, indent=2)
        print(f"[briefs] sauvegarde OK")
    except Exception as e:
        print(f"[briefs] erreur : {e}")


def generate_html(briefs):
    """Generate HTML page with both daily and strategy briefs (with links)."""
    daily_digest = briefs.get("daily", {}).get("digest", "(Pas de brief quotidien aujourd'hui)")
    strategy_digest = briefs.get("strategy", {}).get("digest", "(Pas de veille stratégique aujourd'hui)")

    # Convert markdown links [text](url) to HTML links in digest
    daily_digest = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank">\1</a>', daily_digest)
    strategy_digest = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank">\1</a>', strategy_digest)

    html = f"""<title>Brief Quotidien & Veille Stratégique</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Zilla+Slab:wght@400;500;600;700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root{{--bg:#EEF0F2;--surface:#FBFCFD;--surface-2:#F1F4F6;--ink:#141A1F;--muted:#54606A;--faint:#8894A0;--line:#DCE2E7;--line-strong:#C4CDD4;--accent:#15607A;--accent-ink:#0E4B60;--accent-soft:rgba(21,96,122,.10);--hot:#A6371F;--shadow:0 1px 2px rgba(20,26,31,.04),0 8px 22px rgba(20,26,31,.06)}}
  @media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0F1417;--surface:#161C20;--surface-2:#1C242A;--ink:#E9EEF1;--muted:#9DAAB4;--faint:#6E7B85;--line:#262F35;--line-strong:#36424A;--accent:#4FB2CE;--accent-ink:#68C2DB;--accent-soft:rgba(79,178,206,.13);--hot:#E0765C;--shadow:0 1px 2px rgba(0,0,0,.35),0 10px 28px rgba(0,0,0,.4)}}}}
  :root[data-theme="dark"]{{--bg:#0F1417;--surface:#161C20;--surface-2:#1C242A;--ink:#E9EEF1;--muted:#9DAAB4;--faint:#6E7B85;--line:#262F35;--line-strong:#36424A;--accent:#4FB2CE;--accent-ink:#68C2DB;--accent-soft:rgba(79,178,206,.13);--hot:#E0765C;--shadow:0 1px 2px rgba(0,0,0,.35),0 10px 28px rgba(0,0,0,.4)}}
  *{{box-sizing:border-box}}html{{-webkit-text-size-adjust:100%}}
  body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;font-size:17px;line-height:1.58;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:740px;margin:0 auto;padding:clamp(18px,4vw,48px) clamp(16px,4.5vw,36px) 68px}}
  .masthead{{border-bottom:3px double var(--ink);padding-bottom:16px}}
  .kicker{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent-ink)}}
  .kicker .edition{{color:var(--faint)}}
  h1{{font-family:"Zilla Slab",Georgia,serif;font-weight:700;font-size:clamp(38px,9vw,62px);line-height:.98;letter-spacing:-.01em;margin:.14em 0 .06em}}
  .sub{{font-size:15px;color:var(--muted);margin-top:8px}}
  .sub b{{color:var(--ink);font-weight:600}}
  section.sec{{margin-top:34px}}
  .sec-head{{display:flex;align-items:center;gap:12px;margin-bottom:6px}}
  .sec-head h2{{font-family:"Zilla Slab",Georgia,serif;font-weight:600;font-size:20px;margin:0}}
  .sec-head .flag{{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:#fff;background:var(--accent);padding:3px 8px;border-radius:5px}}
  .sec-head .flag.alt{{background:var(--hot)}}
  .sec-rule{{height:1px;background:var(--line-strong);border:0;margin:0 0 16px}}
  .digest-raw{{background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:15px 17px;margin-bottom:12px;box-shadow:var(--shadow);font-family:"IBM Plex Mono",monospace;font-size:13px;white-space:pre-wrap;word-break:break-word;color:var(--muted)}}
  .digest-raw a{{color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent-soft)}}
  .digest-raw a:hover{{border-bottom-color:var(--accent)}}
  .foot{{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--faint);display:flex;flex-wrap:wrap;gap:6px 16px;justify-content:space-between}}
  a:focus-visible{{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}}
</style>

<div class="wrap">
  <header class="masthead">
    <div class="kicker"><span>Éco · Finance · Marchés</span><span class="edition">Édition du jour</span></div>
    <h1>Brief & Veille</h1>
    <div class="sub"><b>{TODAY}</b> — revue de presse & veille stratégique pour la Banque de financement et d'investissement.</div>
  </header>

  <section class="sec">
    <div class="sec-head"><span class="flag">Matin</span><h2>Brief Quotidien</h2></div>
    <hr class="sec-rule">
    <div class="digest-raw">{daily_digest}</div>
  </section>

  <section class="sec">
    <div class="sec-head"><span class="flag alt">Stratégique</span><h2>Veille Disruption & Stratégie</h2></div>
    <hr class="sec-rule">
    <div class="digest-raw">{strategy_digest}</div>
  </section>

  <div class="foot">
    <span>Brief interne · usage BFI</span>
    <span>Généré le {TODAY}</span>
  </div>
</div>
"""
    return html

# --------------------------------------------------------------------------
# ARCHIVE
# --------------------------------------------------------------------------

def load_archive():
    """Load archive from file, or return empty list."""
    if ARCHIVE_FILE.exists():
        try:
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []


def save_archive(archive):
    """Save archive to file."""
    try:
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
        print(f"[archive] sauvegarde OK ({len(archive)} entrees)")
    except Exception as e:
        print(f"[archive] erreur : {e}")


def append_archive(digest, mode, items_count):
    """Append new brief to archive."""
    archive = load_archive()
    entry = {
        "date": TODAY_ISO,
        "mode": mode,
        "digest": digest[:500],
        "sources": items_count,
    }
    archive.append(entry)
    archive = archive[-90:]
    save_archive(archive)

# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    print(f"[brief] mode={MODE} modele={GEMINI_MODEL} date={TODAY}")

    items = gather()
    print(f"[brief] total items collectes : {len(items)}")

    if len(items) < 10:
        error_msg = f"⚠️ ALERTE : Seulement {len(items)} articles collectés (< 10). Qualité compromise."
        print(f"[brief] {error_msg}")
        send_telegram(error_msg)
        if not items:
            raise RuntimeError("Aucun flux RSS lisible : impossible de rediger.")

    prompt = INSTRUCTIONS + "\n\nTITRES REELS COLLECTES :\n" + build_context(items)

    try:
        digest = call_gemini(prompt)
    except Exception as e:
        error_msg = f"❌ ERREUR Gemini : {str(e)[:100]}. Impossible de générer le brief."
        print(f"[brief] {error_msg}")
        send_telegram(error_msg)
        raise

    print("----- DIGEST -----")
    print(digest)
    print("------------------")

    # Load briefs, update current mode, save
    briefs = load_briefs()
    briefs[MODE] = {
        "digest": digest,
        "date": TODAY_ISO,
        "sources": len(items)
    }
    save_briefs(briefs)

    # Generate HTML
    html = generate_html(briefs)
    try:
        with open(INDEX_HTML, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[html] sauvegarde OK : {INDEX_HTML}")
    except Exception as e:
        print(f"[html] erreur : {e}")

    # Archive
    append_archive(digest, MODE, len(items))

    # Send to Telegram with link
    ok, err = send_telegram(digest, GITHUB_PAGES_URL)
    if not ok:
        error_msg = f"❌ ERREUR Telegram : {err}"
        print(f"[brief] {error_msg}")
        raise RuntimeError(error_msg)
    print("[brief] Telegram : envoye OK (avec lien page)")


if __name__ == "__main__":
    main()
