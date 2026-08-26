#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Brief Quotidien / Veille hebdo -> Telegram, via l'API Gemini (palier gratuit).
Aucune dependance externe : uniquement la bibliotheque standard Python.

Usage : python brief.py daily
        python brief.py weekly

Variables d'environnement attendues (secrets GitHub) :
    GEMINI_API_KEY       cle API Google AI Studio (gratuite)
    TELEGRAM_BOT_TOKEN   token du bot @BotFather
    TELEGRAM_CHAT_ID     identifiant du chat/canal Telegram
    GEMINI_MODEL         (optionnel) defaut : gemini-2.5-flash
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.error

MODE = (sys.argv[1] if len(sys.argv) > 1 else "daily").strip().lower()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

TODAY = datetime.date.today().strftime("%d/%m/%Y")

# --------------------------------------------------------------------------
# PROMPTS
# --------------------------------------------------------------------------

DAILY_PROMPT = f"""Tu es l'assistant de veille d'un responsable de la Banque de financement et d'investissement (Attijari Bank Tunisie). Nous sommes le {TODAY}. Utilise la recherche Google pour l'actualite reelle des dernieres 24 h (ou du dernier jour ouvre pour les marches).

ANGLE : priorite economie, finance, marches, banque ; plus les grands titres politiques/macro (Tunisie + international) utiles a cette fonction. Pas un journal generaliste.

SOURCES a privilegier : cote Tunisie (Business News, Managers, L'Economiste Maghrebin, Leaders, Webmanagercenter, Tunisie Numerique, TAP, Ilboursa) ; cote international (Reuters, AFP, Bloomberg, Financial Times, Les Echos, Jeune Afrique, BCE/Fed/FMI).

REGLES : n'invente AUCUN chiffre ; si un chiffre n'est pas sourcable, ecris "non chiffre". Reste factuel et neutre, sans jugement politique. Pas de remplissage : s'il y a peu de matiere reelle dans une section, mets-en moins.

SORTIE : produis UNIQUEMENT le texte du digest ci-dessous, rien d'autre (pas d'introduction, pas de conclusion, pas de liens). Chaque puce = 1 phrase claire et autoportante (20-30 mots) avec le chiffre cle s'il existe. Reste sous 3500 caracteres. Format EXACT :

🗞️ Brief du {TODAY}

🇹🇳 TUNISIE
• [titre court] : [1 phrase]

🌍 INTERNATIONAL
• [titre court] : [1 phrase]

📈 MARCHÉS & TAUX
• [mouvement] : [1 phrase, chiffre sourcé si dispo]
"""

WEEKLY_PROMPT = f"""Tu es l'assistant de veille strategique d'un responsable de la Banque de financement et d'investissement (Attijari Bank Tunisie). Nous sommes le {TODAY}. Utilise la recherche Google pour l'actualite des 7 derniers jours.

OBJET : "Veille Disruption & Strategie". Un sujet n'entre QUE s'il modifie au moins un de : la structure de la chaine de valeur (un maillon disparait/se deplace), qui capte la marge, la structure de couts (ordre de grandeur), ou la regle du jeu (reglementaire/techno/acces marche). EXCLUS : levees de fonds sans structure, exits, prix, nominations, classements, partenariats vides, communiques marketing, cours de bourse, recits de fondateurs sans chiffres.

Sources : Finextra, Sifted, Ledger Insights, PYMNTS, The Paypers, Global Custodian, TechCabal, Wamda, Rest of World, Reuters Africa, Stratechery, The Generalist, BIS, FMI, Banque mondiale, Reuters, FT, The Economist.

Blocs : A = Banque & finance (max 5) ; B = Autres secteurs (max 5) ; C = Macro/politique publique (max 5, sans jugement politique). Les quotas sont des plafonds, jamais des objectifs : un bloc peut rester vide. Qualite > quantite. N'invente AUCUN chiffre ; sinon "non chiffre".

SORTIE : produis UNIQUEMENT le texte du digest ci-dessous, rien d'autre (pas de liens). Chaque puce = 1 a 2 phrases completes et autoportantes (20-35 mots) : quoi + mecanisme + chiffre cle. Reste sous 3500 caracteres. Format EXACT :

📊 Veille — semaine du {TODAY}

A · BANQUE
• [Acteur] : [1-2 phrases]

B · SECTEURS
• [Acteur] : [1-2 phrases]

C · MACRO
• [Etat] : [1-2 phrases]
"""

PROMPT = DAILY_PROMPT if MODE == "daily" else WEEKLY_PROMPT


# --------------------------------------------------------------------------
# APPELS RESEAU
# --------------------------------------------------------------------------

def _post_json(url, payload, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def call_gemini(prompt, use_search=True):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    if use_search:
        body["tools"] = [{"google_search": {}}]

    data = _post_json(url, body)
    cand = data["candidates"][0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Reponse Gemini vide : " + json.dumps(data)[:500])
    return text


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4090],
        "disable_web_page_preview": True,
    }
    return _post_json(url, payload, timeout=60)


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    print(f"[brief] mode={MODE} modele={GEMINI_MODEL} date={TODAY}")

    # 1) Generation (avec recherche Google ; repli sans recherche si refus)
    try:
        digest = call_gemini(PROMPT, use_search=True)
        print("[brief] Gemini OK (avec recherche Google)")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        print(f"[brief] recherche Google refusee ({e.code}) : {detail}")
        print("[brief] nouvelle tentative SANS recherche...")
        digest = call_gemini(PROMPT, use_search=False)
        print("[brief] Gemini OK (sans recherche)")

    print("----- DIGEST -----")
    print(digest)
    print("------------------")

    # 2) Envoi Telegram
    res = send_telegram(digest)
    if not res.get("ok"):
        raise RuntimeError("Echec Telegram : " + json.dumps(res)[:400])
    print("[brief] Telegram : envoye OK")


if __name__ == "__main__":
    main()
