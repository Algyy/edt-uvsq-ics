# socal — abonnement iCalendar pour CELCAT

L'emploi du temps CELCAT de l'UVSQ (`edt.uvsq.fr`) n'offre pas d'export `.ics`.
Ce projet en génère un, le publie à une URL stable, et le tient à jour.

## Comment ça marche

CELCAT affiche son planning en appelant un endpoint JSON interne :

```
POST https://edt.uvsq.fr/Home/GetCalendarData
     start=2026-09-01&end=2027-08-31&resType=103&calView=month
     &federationIds[]=HISL2TD1&colourScheme=3
```

Cet endpoint ne demande ni cookie ni authentification. `celcat_ics.py` l'interroge,
traduit chaque événement en `VEVENT`, et écrit un fichier `.ics` dans `docs/`.
GitHub Actions relance le script trois fois par jour et commite le résultat.
GitHub Pages sert le fichier. Ton agenda s'y abonne.

## Utilisation locale

Aucune dépendance. Python 3.9 ou plus récent suffit.

```bash
python3 celcat_ics.py --fid HISL2TD1 --out docs/hisl2td1.ics
```

Options utiles :

| Option | Rôle |
| --- | --- |
| `--fid ID` | Identifiant CELCAT du groupe. Répétable pour fusionner plusieurs groupes. |
| `--out CHEMIN` | Fichier `.ics` à écrire. |
| `--name TEXTE` | Nom du calendrier. Par défaut, CELCAT le fournit. |
| `--url URL` | URL publique du feed. Ajoute `SOURCE` et `REFRESH-INTERVAL`. |
| `--res-type N` | `103` groupe, `100` enseignant, `104` étudiant. |
| `--days-back N` | Profondeur du passé, 120 jours par défaut. |
| `--days-ahead N` | Profondeur du futur, 400 jours par défaut. |
| `--min-events N` | Refuse d'écrire en dessous de ce seuil. |

Le script ne réécrit pas le fichier si le planning n'a pas bougé. Il compare un
hash du contenu stocké dans l'en-tête `X-CELCAT-CONTENT-HASH`.

`--min-events` est un garde-fou. Si CELCAT renvoie une liste vide pendant une
maintenance, le script sort en erreur au lieu de vider ton agenda.

## Trouver son identifiant de groupe

Ouvre ton planning sur `edt.uvsq.fr`. L'identifiant est dans l'URL, après `fid0=` :

```
https://edt.uvsq.fr/cal?vt=month&dt=2026-09-12&et=group&fid0=HISL2TD1
                                                             ^^^^^^^^
```

## Le feed en ligne

```
https://umesiyah.github.io/edt-uvsq-ics/hisl2td1.ics
```

Page d'abonnement : <https://umesiyah.github.io/edt-uvsq-ics/>

Pour ajouter un groupe, édite le tableau `CALENDARS` dans
`.github/workflows/update-calendars.yml` :

```bash
CALENDARS=(
  "hisl2td1 HISL2TD1"
  "hisl2td2 HISL2TD2"          # un second calendrier
  "histoire-l2 HISL2TD1 HISL2TD2"  # les deux fusionnés en un seul
)
```

### Refaire cette installation ailleurs

1. Settings → Pages → Source : `Deploy from a branch`, branche `main`, dossier `/docs`.
2. Settings → Actions → General → Workflow permissions : `Read and write permissions`.
3. Actions → `Update calendars` → `Run workflow`.

Vérifie ensuite le type MIME, que certains clients exigent :

```bash
curl -sI https://umesiyah.github.io/edt-uvsq-ics/hisl2td1.ics | grep -i content-type
# content-type: text/calendar
```

## S'abonner

Il faut **coller l'URL**, jamais télécharger puis ouvrir le fichier. Ouvrir un
`.ics` téléchargé produit un import : une copie figée, sans lien avec la source.

**Google Agenda** — Paramètres → Ajouter un agenda → À partir de l'URL.

**Apple Calendar (macOS)** — Fichier → Nouvel abonnement au calendrier.

**Apple Calendar (iOS)** — Réglages → Apps → Calendrier → Comptes → Ajouter un
compte → Autre → Ajouter un calendrier avec abonnement.

**Proton Calendar** — Paramètres → Mes agendas → Ajouter un agenda → S'abonner
à un agenda externe.

**Thunderbird** — Nouvel agenda → Sur le réseau → iCalendar (ICS).

Pour vérifier qu'un abonnement a bien été créé : le calendrier doit apparaître
dans une section à part, et ses réglages doivent afficher l'URL et un intervalle
d'actualisation. Si l'un des deux manque, c'est un import.

### Fréquence de rafraîchissement

Le point faible est côté client, pas côté script.

- Google Agenda relit un `.ics` externe quand il veut. En pratique entre 8 et 24
  heures. Ce délai n'est pas réglable.
- Proton Calendar et Thunderbird rafraîchissent plus souvent.

Si tu as besoin de voir un changement de salle dans l'heure, un feed `.ics` ne
suffit pas. Il faut alors écrire dans Google Agenda via son API, avec un compte
de service et un `sync token`. C'est plus lourd : OAuth, quotas, et un état à
conserver entre les exécutions.

## Ce que contient chaque événement

`SUMMARY` reprend le type et la matière, par exemple `TD - Histoire ancienne 2`.
Les cours annulés gardent la mention de CELCAT (`CM annulé - ...`) et passent en
`TRANSP:TRANSPARENT`, ce qui les rend non bloquants pour la disponibilité.

`LOCATION` combine la salle et le site, par exemple `C337, D'ALEMBERT`.

`DESCRIPTION` liste le type, la matière, la salle, le site, la note libre de
CELCAT (nom d'enseignant, numéro de groupe, report de cours) et les groupes
concernés.

Les `UID` sont dérivés de l'identifiant CELCAT de l'événement. Un cours déplacé
est donc mis à jour, pas dupliqué.

## Limites connues

- L'appli CELCAT borne les requêtes à son année universitaire courante. Au-delà,
  elle renvoie simplement moins d'événements.
- Le champ note de CELCAT est du texte libre. Son contenu varie selon le
  département qui saisit le planning.
- Si l'université ferme l'endpoint ou ajoute une authentification, le script
  casse. Il n'y a pas de contournement prévu.
- GitHub désactive un workflow planifié après 60 jours sans activité sur le
  dépôt. Pendant l'année les commits du bot suffisent à le garder actif, mais
  l'été peut dépasser ce seuil. GitHub prévient par mail, un clic réactive.
