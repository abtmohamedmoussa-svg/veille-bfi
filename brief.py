#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Brief Quotidien / Veille hebdo -> Telegram. RSS + Gemini (stdlib only)."""

import os
import re
import sys
import json
import time
import datetime
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

MODE = (sys.argv[1] if len(sys.argv) > 1 else "daily").strip().lower()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

TODAY = datetime.date.today().strftime("%d/%m/%Y")
UA = "Mozilla/5.0 (compatible; VeilleBot/1.0)"

DAILY_FEEDS = [
    ("Webmanagercenter", "https://www.webmanagercenter.com/feed/"),
    ("African Manager", "https://africanmanager.com/feed/"),
    ("Tunisie Numerique", "https://www.tunisienumerique.com/feed/"),
    ("Business News", "https://www.businessnews.com.tn/feed"),
    ("Le Monde Economie", "https://www.lemonde.fr/economie/rss_full.xml"),
    ("France24 Eco", "https://www.france24.com/fr/économie/rss"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
]
WEEKLY_FEEDS = [
    ("Finextra", "https://www.finextra.com/rss/headlines.aspx"),
    ("TechCabal", "https://techcabal.com/feed/"),
    ("Wamda", "https://www.wamda.com/feed"),
    ("Rest of World", "https://restofworld.org/feed/latest/"),
    ("Sifted", "https://sifted.eu/feed"),
    ("Le Monde Economie", "https://www.lemonde.fr/economie/rss_full.xml"),
]
FEEDS = DAILY_FEEDS if MODE == "daily" else WEEKLY_FEEDS
PER_FEED = 6


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_feed(name, url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[feed] {name} : ignore ({e})")
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = strip_html(it.findtext("description") or "")
        date = (it.findtext("pubDate") or "").strip()
        if title:
            items.append((name, title, desc[:220], date))
        if len(items) >= PER_FEED:
            break
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.iter(ns + "entry"):
            title = (e.findtext(ns + "title") or "").strip()
            desc = strip_html(e.findtext(ns + "summary") or e.findtext(ns + "content") or "")
            date = (e.findtext(ns + "updated") or e.findtext(ns + "published") or "").strip()
            if title:
                items.append((name, title, desc[:220], date))
            if len(items) >= PER_FEED:
                break
    print(f"[feed] {name} : {len(items)} items")
    return items


def gather():
    out = []
    for name, url in FEEDS:
        out.extend(fetch_feed(name, url))
    return out


def build_context(items):
    lines = []
    for name, title, desc, date in items:
        line = f"- [{name}] {title}"
        if date:
            line += f" ({date})"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return "\n".join(lines)


DAILY_INSTRUCTIONS = f"""Tu es l'assistant de veille d'un responsable de la Banque de financement et d'investissement (Attijari Bank Tunisie). Nous sommes le {TODAY}.

A partir UNIQUEMENT des titres reels ci-dessous, redige un brief presse du jour. Priorite : economie, finance, marches, banque ; plus les grands titres politiques/macro (Tunisie + international) utiles a un banquier d'affaires. Ignore people, faits divers, sport, communiques marketing.

REGLES : n'invente AUCUN chiffre ni fait absent des titres fournis. Factuel et neutre. Pas de remplissage.

SORTIE : produis UNIQUEMENT le digest ci-dessous (pas d'intro, pas de liens). Chaque puce = 1 phrase claire. Sous 3500 caracteres. Format EXACT :

🗞️ Brief du {TODAY}

🇹🇳 TUNISIE
- [titre court] : [1 phrase]

🌍 INTERNATIONAL
- [titre court] : [1 phrase]

📈 MARCHÉS & TAUX
- [mouvement] : [1 phrase]
"""

WEEKLY_INSTRUCTIONS = f"""Tu es l'assistant de veille strategique d'un responsable de la Banque de financement et d'investissement (Attijari Bank Tunisie). Nous sommes le {TODAY}.

A partir UNIQUEMENT des titres reels ci-dessous (7 derniers jours), redige une "Veille Disruption & Strategie". Un sujet n'entre QUE s'il modifie : la chaine de valeur, qui capte la marge, la structure de couts (ordre de grandeur), ou la regle du jeu. EXCLUS : levees sans structure, exits, prix, nominations, classements, partenariats vides, cours de bourse.

REGLES : n'invente AUCUN chiffre absent des titres. Qualite > quantite ; un bloc peut rester vide. Plafond 5 sujets par bloc.

SORTIE : produis UNIQUEMENT le digest ci-dessous (pas de liens). Chaque puce = 1 a 2 phrases. Sous 3500 caracteres. Format EXACT :

📊 Veille — semaine du {TODAY}

A · BANQUE
- [Acteur] : [1-2 phrases]

B · SECTEURS
- [Acteur] : [1-2 phrases]

C · MACRO
- [Etat] : [1-2 phrases]
"""

INSTRUCTIONS = DAILY_INSTRUCTIONS if MODE == "daily" else WEEKLY_INSTRUCTIONS


def call_gemini(prompt, max_retries=4):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192,
                             "thinkingConfig": {"thinkingLevel": "MINIMAL"}},
    }
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                out = json.load(resp)
            cand = out["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise RuntimeError("texte vide (finishReason=" + str(cand.get("finishReason")) + ")")
            return text
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} : " + e.read().decode('utf-8', 'ignore')[:200]
            if e.code in (429, 500, 503) and attempt < max_retries:
                print(f"[gemini] {e.code}, pause 20s (essai {attempt}/{max_retries})")
                time.sleep(20); continue
            raise
        except RuntimeError as e:
            last = str(e)
            if attempt < max_retries:
                print(f"[gemini] {e}, retry 8s"); time.sleep(8); continue
            raise
    raise RuntimeError("Gemini a echoue : " + str(last))


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text[:4090], "disable_web_page_preview": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    print(f"[brief] mode={MODE} modele={GEMINI_MODEL} date={TODAY}")
    items = gather()
    print(f"[brief] total items collectes : {len(items)}")
    if not items:
        raise RuntimeError("Aucun flux RSS lisible.")
    prompt = INSTRUCTIONS + "\n\nTITRES REELS COLLECTES :\n" + build_context(items)
    digest = call_gemini(prompt)
    print("----- DIGEST -----"); print(digest); print("------------------")
    res = send_telegram(digest)
    if not res.get("ok"):
        raise RuntimeError("Echec Telegram : " + json.dumps(res)[:400])
    print("[brief] Telegram : envoye OK")


if __name__ == "__main__":
    main()
