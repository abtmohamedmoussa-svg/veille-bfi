#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Principe du jour — tirage quotidien sans remise, un domaine par jour de semaine.
Developpe l'entree tiree via l'API Anthropic, puis pousse le resultat sur Telegram.
Etat persiste dans state_principe.json (commite par le workflow).
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

CORPUS_FILE = "corpus.json"
STATE_FILE = "state_principe.json"

TZ_TUNIS = timezone(timedelta(hours=1))  # Tunisie : UTC+1 toute l'annee

# weekday() : lundi = 0
ROTATION = {
    0: "macro",
    1: "marche_credit",
    2: "entreprise_strategie",
    3: "management_organisation",
    4: "influence_negociation",
    5: "decision_biais_idees_recues",
    6: "communication_leadership",
}

LIBELLES = {
    "macro": "MACRO & POLITIQUE ECONOMIQUE",
    "marche_credit": "FINANCE DE MARCHE & RISQUE DE CREDIT",
    "entreprise_strategie": "STRATEGIE D'ENTREPRISE",
    "management_organisation": "MANAGEMENT & ORGANISATION",
    "influence_negociation": "INFLUENCE & NEGOCIATION",
    "decision_biais_idees_recues": "DECISION, BIAIS & IDEES RECUES",
    "communication_leadership": "COMMUNICATION & LEADERSHIP",
}

MENTIONS_STATUT = {
    "etabli": "",
    "debat_academique": "\n[Statut : theorie contestee dans sa discipline]",
    "praticien": "\n[Statut : heuristique de praticien, non validee experimentalement]",
    "invalide": "\n[Statut : enseigne massivement, non soutenu par les donnees]",
}

# Free tier Gemini : seuls les modeles Flash / Flash-Lite y sont eligibles.
# Les identifiants evoluent : verifier le model ID exact dans Google AI Studio.
MODELE = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
MAX_TELEGRAM = 3900  # marge sous la limite de 4096 caracteres

PROMPT = """Tu rediges la pilule quotidienne d'un cadre dirigeant de banque de financement et d'investissement. Il connait la finance : pas de definition scolaire, pas de ton pedagogique, pas de flatterie.

CONCEPT : {titre}
DOMAINE : {domaine}
STATUT : {statut}
ANCRAGE FACTUEL (seule source autorisee) : {ancrage}
ANGLE SUGGERE : {angle}

REGLE ABSOLUE : tu ne peux citer aucun chiffre, aucune date, aucun nom propre et aucune citation qui ne figure pas dans l'ancrage factuel ci-dessus. Si tu ne disposes pas d'un fait, tu n'en inventes pas : tu raisonnes sans lui. Aucune statistique, aucun pourcentage, aucun montant.

STRUCTURE, en francais, 1600 a 1900 caracteres au total :
1. Le mecanisme en 3 a 4 phrases. Ce qui se passe, et pourquoi.
2. Deux ou trois cas d'usage concrets, dont au moins un en banque de financement ou en direction generale. Chacun en 2 phrases, situation puis consequence pratique.
3. Une limite, un contre-exemple ou une condition de validite. Une phrase.
{consigne_statut}

FORME : texte brut uniquement. Aucun caractere de mise en forme markdown, pas d'asterisque, pas de diese, pas de tiret bas. Les listes sont introduites par un tiret simple. Pas de titre, pas d'introduction, pas de conclusion generale. Tu commences directement par le mecanisme."""

CONSIGNE_INVALIDE = "\nCe concept est classe comme non soutenu par les donnees. Le point 1 doit expliquer pourquoi il est faux ou fragile, et le point 3 doit indiquer ce qui le remplace utilement."
CONSIGNE_DEBAT = "\nCe concept est conteste dans sa discipline. La controverse doit apparaitre explicitement, pas en note de bas de page."


def poster_json(url, charge, entetes=None, timeout=90):
    """POST JSON via la bibliotheque standard. Retourne (code_http, corps_decode)."""
    donnees = json.dumps(charge).encode("utf-8")
    requete = urllib.request.Request(url, data=donnees, method="POST")
    requete.add_header("content-type", "application/json")
    for cle, valeur in (entetes or {}).items():
        requete.add_header(cle, valeur)
    try:
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            return reponse.status, json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        return err.code, detail


def charger_etat():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            etat = json.load(f)
            etat.setdefault("utilises", [])
            etat.setdefault("cycle", 1)
            etat.setdefault("historique", [])
            return etat
    except (FileNotFoundError, json.JSONDecodeError):
        return {"utilises": [], "cycle": 1, "historique": []}


def choisir_entree(corpus, etat, jour):
    """Domaine du jour ; si epuise, on bascule sur le domaine le mieux pourvu."""
    utilises = set(etat["utilises"])
    domaine = ROTATION[jour.weekday()]

    dispo = [e for e in corpus if e["categorie"] == domaine and e["id"] not in utilises]
    if dispo:
        return random.choice(dispo), domaine, False

    # Domaine epuise : repli sur le domaine ayant le plus de reserve
    restants = {}
    for e in corpus:
        if e["id"] not in utilises:
            restants.setdefault(e["categorie"], []).append(e)

    if not restants:
        # Corpus entierement parcouru : nouveau cycle
        etat["utilises"] = []
        etat["cycle"] += 1
        dispo = [e for e in corpus if e["categorie"] == domaine]
        return random.choice(dispo), domaine, False

    domaine_repli = max(restants, key=lambda c: len(restants[c]))
    return random.choice(restants[domaine_repli]), domaine_repli, True


def developper(entree):
    """Appel Gemini. Retourne None en cas d'echec : le script bascule sur le texte statique."""
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        return None

    consigne = ""
    if entree["statut"] == "invalide":
        consigne = CONSIGNE_INVALIDE
    elif entree["statut"] == "debat_academique":
        consigne = CONSIGNE_DEBAT

    corps = PROMPT.format(
        titre=entree["titre"],
        domaine=LIBELLES[entree["categorie"]],
        statut=entree["statut"],
        ancrage=entree["ancrage"],
        angle=entree["angle"],
        consigne_statut=consigne,
    )

    # maxOutputTokens genereux : sur les modeles a raisonnement, les jetons de
    # reflexion sont decomptes de cette enveloppe. Trop bas, la reponse revient vide.
    # temperature volontairement absente : ignoree par les modeles Gemini 3.x.
    charge = {
        "contents": [{"parts": [{"text": corps}]}],
        "generationConfig": {"maxOutputTokens": 4000},
    }

    # Le free tier renvoie 429 quand une limite de debit est atteinte : backoff exponentiel.
    for tentative, attente in enumerate((0, 5, 15, 45), start=1):
        if attente:
            time.sleep(attente)
        try:
            code, reponse = poster_json(
                ENDPOINT.format(m=MODELE), charge, {"x-goog-api-key": cle}
            )
        except Exception as exc:
            print(f"[Gemini] echec tentative {tentative} : {exc}", file=sys.stderr)
            continue

        if code in (429, 500, 503):
            print(f"[Gemini] {code}, tentative {tentative}", file=sys.stderr)
            continue
        if code != 200:
            print(f"[Gemini] HTTP {code} : {reponse}", file=sys.stderr)
            return None

        candidats = reponse.get("candidates", [])
        if not candidats:  # reponse filtree ou vide
            print("[Gemini] aucune reponse exploitable", file=sys.stderr)
            return None
        motif = candidats[0].get("finishReason", "?")
        parts = candidats[0].get("content", {}).get("parts", [])
        texte = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not texte:
            print(f"[Gemini] texte vide, finishReason={motif}", file=sys.stderr)
            return None
        return texte
    return None


def texte_statique(entree):
    """Repli hors ligne : les champs du corpus, sans generation."""
    return f"{entree['ancrage']}\n\nApplication : {entree['angle']}"


def composer(entree, domaine_affiche, repli, jour, corps):
    entete = f"{LIBELLES[domaine_affiche]} — {jour.strftime('%d/%m/%Y')}"
    if repli:
        entete += " (domaine du jour epuise)"
    message = f"{entete}\n\n{entree['titre'].upper()}{MENTIONS_STATUT[entree['statut']]}\n\n{corps}"
    if len(message) > MAX_TELEGRAM:
        message = message[: MAX_TELEGRAM - 3] + "..."
    return message


def envoyer(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    code, reponse = poster_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=30,
    )
    if code != 200:
        raise RuntimeError(f"Telegram HTTP {code} : {reponse}")


def main():
    jour = datetime.now(TZ_TUNIS)
    with open(CORPUS_FILE, encoding="utf-8") as f:
        corpus = json.load(f)["entrees"]

    etat = charger_etat()
    entree, domaine_affiche, repli = choisir_entree(corpus, etat, jour)

    corps = developper(entree)
    genere = corps is not None
    if not genere:
        corps = texte_statique(entree)

    message = composer(entree, domaine_affiche, repli, jour, corps)
    envoyer(message)

    # Si la generation a echoue, l'entree n'est pas consommee : elle repassera
    # dans le tirage plutot que d'etre brulee sur un message degrade.
    if genere:
        etat["utilises"].append(entree["id"])
    etat["historique"].append(
        {
            "date": jour.strftime("%Y-%m-%d"),
            "id": entree["id"],
            "domaine": domaine_affiche,
            "genere": genere,
        }
    )
    etat["historique"] = etat["historique"][-60:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=1)

    restant = len(corpus) - len(etat["utilises"])
    print(f"{entree['id']} — {entree['titre']} | genere={genere} | reste {restant}")


if __name__ == "__main__":
    main()
