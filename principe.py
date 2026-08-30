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
import re
import sys
import time
import urllib.error
import urllib.parse
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

PROMPT = """Tu rediges la pilule quotidienne d'un cadre dirigeant de banque de financement et d'investissement. Il connait la finance : pas de definition scolaire, pas de ton pedagogique, pas de flatterie. Son propre style est telegraphique et precis, sans placage theorique : le tien doit l'etre aussi.

CONCEPT : {titre}
DOMAINE : {domaine}
STATUT : {statut}
ANCRAGE FACTUEL (seule source autorisee) : {ancrage}
ANGLE SUGGERE : {angle}

REGLE ABSOLUE SUR LES FAITS : tu ne peux citer aucun chiffre, aucune date, aucun nom propre et aucune citation qui ne figure pas dans l'ancrage factuel ci-dessus. Si tu ne disposes pas d'un fait, tu n'en inventes pas : tu raisonnes sans lui. Aucune statistique, aucun pourcentage, aucun montant invente.

REGLE ABSOLUE SUR LE STYLE : phrases courtes, verbes concrets, une idee par phrase. Interdiction des nominalisations abstraites (une transformation qui produit, constitue une ressource, revet un caractere) et des formules litteraires (inevitablement, particulierement, au sein de, abandonner au debat). Si une phrase peut se dire en deux fois moins de mots, elle doit l'etre. Registre d'un cadre qui parle a un autre cadre, jamais celui d'un manuel.

STRUCTURE OBLIGATOIRE — chaque section commence exactement par l'en-tete indique, en majuscules suivies de deux points, sans tiret ni numero devant l'en-tete :

MECANISME : 3 a 4 phrases. Ce qui se passe, et pourquoi. Pas de mise en contexte, tu entres directement dans le mecanisme.

CAS 1 : une situation precise, puis sa consequence pratique. 2 phrases. Priorite a un cas en banque de financement, credit ou direction generale.

CAS 2 : une deuxieme situation, dans un registre different du premier (un autre metier, un autre contexte). 2 phrases.

LIMITE : une phrase. La condition de validite, le contre-exemple ou la limite du concept.
{consigne_statut}

FORME : texte brut uniquement. Aucun caractere de mise en forme markdown, pas d'asterisque, pas de diese, pas de tiret bas, pas de puce. Chaque section est un paragraphe qui commence par son en-tete sur la meme ligne que le texte. Longueur totale visee : 1500 a 1800 caracteres."""

CONSIGNE_INVALIDE = "\nCe concept est classe comme non soutenu par les donnees. MECANISME doit expliquer pourquoi il est faux ou fragile, et LIMITE doit indiquer ce qui le remplace utilement."
CONSIGNE_DEBAT = "\nCe concept est conteste dans sa discipline. La controverse doit apparaitre explicitement dans MECANISME, pas en aparte."

SECTIONS_REQUISES = ("MECANISME", "CAS 1", "CAS 2", "LIMITE")


def valider_structure(texte):
    """Verifie la presence des quatre en-tetes attendus. Ne verifie pas le contenu :
    seule la structure, seul point mecaniquement controlable sans jugement de fond."""
    haut = texte.upper()
    return all(s in haut for s in SECTIONS_REQUISES)


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


def _appeler_gemini(corps, cle):
    """Un appel complet avec backoff sur erreurs transitoires. Retourne le texte ou None."""
    charge = {
        "contents": [{"parts": [{"text": corps}]}],
        # maxOutputTokens genereux : sur les modeles a raisonnement, les jetons de
        # reflexion sont decomptes de cette enveloppe. Trop bas, la reponse revient vide.
        # temperature volontairement absente : ignoree par les modeles Gemini 3.x.
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


def developper(entree):
    """Appel Gemini, avec une reprise si la structure imposee n'est pas respectee.
    Retourne None seulement si aucune des deux tentatives n'a produit de texte."""
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        return None

    consigne = ""
    if entree["statut"] == "invalide":
        consigne = CONSIGNE_INVALIDE
    elif entree["statut"] == "debat_academique":
        consigne = CONSIGNE_DEBAT

    corps_prompt = PROMPT.format(
        titre=entree["titre"],
        domaine=LIBELLES[entree["categorie"]],
        statut=entree["statut"],
        ancrage=entree["ancrage"],
        angle=entree["angle"],
        consigne_statut=consigne,
    )

    texte = _appeler_gemini(corps_prompt, cle)
    if texte is None:
        return None
    if valider_structure(texte):
        return texte

    print("[Gemini] structure incomplete, reprise ciblee", file=sys.stderr)
    rappel = (
        corps_prompt
        + "\n\nTa reponse precedente n'a pas respecte la structure imposee. "
        "Reponds a nouveau en faisant apparaitre litteralement les quatre en-tetes "
        "MECANISME, CAS 1, CAS 2, LIMITE, chacun suivi de deux points."
    )
    reprise = _appeler_gemini(rappel, cle)
    if reprise and valider_structure(reprise):
        return reprise
    # Aucune des deux tentatives n'est parfaitement structuree : on garde la meilleure
    # matiere disponible plutot que de la jeter, mais on le signale dans les logs.
    print("[Gemini] structure toujours incomplete apres reprise, contenu conserve", file=sys.stderr)
    return reprise or texte


def texte_statique(entree):
    """Repli hors ligne : les champs du corpus, sans generation, meme habillage que le texte genere."""
    return f"MECANISME : {entree['ancrage']}\n\nAPPLICATION : {entree['angle']}"


def mettre_en_forme(corps):
    """Aere les sections avant l'envoi : une ligne vide devant chaque en-tete
    (sauf le tout premier), lisible sur un ecran de telephone."""
    texte = corps.strip()
    for marqueur in SECTIONS_REQUISES:
        texte = re.sub(rf"\s*\b{re.escape(marqueur)}\s*:", f"\n\n{marqueur} :", texte)
    return texte.strip()


def lien(entree):
    """Lien verifie au moment de l'envoi, jamais devine par le modele ni laisse mort.
    Tente l'URL directe deduite du titre ; si elle ne repond pas 200, retombe sur la
    recherche Wikipedia, qui ne peut pas etre cassee. Le champ 'url' du corpus,
    s'il existe, prime sur tout et n'est pas revérifié (a la charge de qui le renseigne)."""
    if entree.get("url"):
        return entree["url"]

    slug = urllib.parse.quote(entree["titre"].replace(" ", "_"))
    directe = f"https://fr.wikipedia.org/wiki/{slug}"
    requete = urllib.parse.urlencode({"search": entree["titre"]})
    recherche = f"https://fr.wikipedia.org/w/index.php?{requete}"

    try:
        r = urllib.request.Request(
            directe, method="HEAD", headers={"User-Agent": "principe-du-jour/1.0"}
        )
        with urllib.request.urlopen(r, timeout=6) as reponse:
            if reponse.status == 200:
                return directe
    except Exception:
        pass
    return recherche


def composer(entree, domaine_affiche, repli, jour, corps):
    entete = f"{LIBELLES[domaine_affiche]} — {jour.strftime('%d/%m/%Y')}"
    if repli:
        entete += " (domaine du jour epuise)"
    corps = mettre_en_forme(corps)
    reference = f"\n\nReference : {entree['ancrage']}\nPour approfondir : {lien(entree)}"
    plafond = MAX_TELEGRAM - len(reference)
    corps_titre = f"{entete}\n\n{entree['titre'].upper()}{MENTIONS_STATUT[entree['statut']]}\n\n{corps}"
    if len(corps_titre) > plafond:
        corps_titre = corps_titre[: plafond - 3] + "..."
    return corps_titre + reference


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
