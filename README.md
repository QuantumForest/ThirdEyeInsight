# ThirdEyeInsight

**Analyseur de decks Yu-Gi-Oh! — probabilités, cohérence et simulation de main d'ouverture.**

ThirdEyeInsight est une application de bureau qui aide à construire et affiner un deck Yu-Gi-Oh! en donnant une vision chiffrée de sa cohérence : quelle est la probabilité réelle d'ouvrir avec un starter ? De survivre à une main morte ? De tenir en Second grâce à tes handtraps ? 
L'outil simule des centaines de milliers de mains et calcule les probabilités exactes.

## Fonctionnalités

- **Construction de deck** — recherche de cartes via l'API YGOPRODeck, gestion Main/Side Deck, filtres par rôle (Starter, Extender, Handtrap, Anti-Handtrap, Boardbreaker, Brick), import/export au format `.ydk`, import direct depuis une URL ou un lien `ydke://`
- **Analyse de probabilité** — calcul par loi hypergéométrique exacte (pas seulement une simulation) pour chaque rôle, avec la redondance (exactement 1 vs exactement 2 exemplaires) affichée séparément
- **Simulation Monte-Carlo** — des centaines de milliers de mains tirées pour évaluer le taux de main jouable, en Premier comme en Second, avec ou sans les combos personnalisés
- **Combos personnalisés** — définis comme des paires de cartes, avec une portée par scénario ou globale
- **Conditions de victoire personnalisées** — au-delà du calcul standard, définis tes propres règles ("au moins 1 Starter ET au moins 2 Handtraps", etc.) avec des opérateurs ET/OU combinables
- **Scénarios post-side** — simule un side deck nommé (ex. "Face à un deck de contrôle") en échangeant des cartes entre Main et Side, sans jamais toucher au deck sauvegardé
- **Comparaison de scénarios** — deux configurations côte à côte, pour trancher entre deux side decks possibles
- **Cartes Pioche** — prend en compte les cartes qui piochent en main d'ouverture (Pot of Greed & co.) dans les calculs
- **Concentration des starters** — identifie si le deck dépend de quelques cartes précises ou si l'ouverture est bien répartie entre plusieurs
- **6 langues** — français, anglais, espagnol, allemand, italien, portugais
- **100% hors ligne après le premier lancement** — la base de cartes et les images sont mises en cache localement

## Aperçu

*(Ajoute ici une ou deux captures d'écran de l'application — page Construction du Deck et page Analyse sont les plus parlantes.)*

## Installation

### Télécharger l'exécutable (le plus simple)

Pas besoin d'installer Python : télécharge directement le `.exe` depuis la page [**Releases**](../../releases) du dépôt, dans un dossier dédié, et lance-le.

> Place l'exécutable dans son propre dossier avant de le lancer pour la première fois — l'application y créera automatiquement `decks/`, `exports/`, `imports/` et `.cache/` à l'intérieur.

### Depuis les sources (pour développer)

#### Prérequis

- Python 3.10 ou supérieur
- pip

```bash
git clone https://github.com/<ton-compte>/ThirdEyeInsight.git
cd ThirdEyeInsight

python3 -m venv env
source env/bin/activate          # Windows : env\Scripts\activate.bat

pip install -r requirements.txt
python main.py
```

Au premier lancement, l'application télécharge et met en cache la base de cartes Yu-Gi-Oh! (via l'API [YGOPRODeck](https://ygoprodeck.com/)) ainsi que les images des cartes utilisées — une connexion internet est donc nécessaire la première fois. Les lancements suivants fonctionnent hors ligne.

## Utilisation

L'application s'articule autour de 4 pages :

1. **Construction du Deck** — ajouter/retirer des cartes, leur assigner un rôle
2. **Combos Starters** — définir des paires de cartes formant un combo d'ouverture
3. **Scénarios** — construire des variantes post-side du deck actif
4. **Analyse** — lancer les calculs de probabilité, comparer des scénarios, ajuster les conditions de victoire

Tous les decks, combos, conditions et scénarios sont sauvegardés localement dans le dossier `decks/`, organisés par deck. Les exports `.ydk` atterrissent dans `exports/`.

## Compiler un exécutable autonome

*Cette section concerne la préparation d'une nouvelle [Release](../../releases) — si tu veux juste utiliser l'application, télécharge directement le `.exe` déjà compilé (voir [Installation](#installation) ci-dessus).*

L'exécutable est généré via [PyInstaller](https://pyinstaller.org/).

### Sur Linux (Debian et dérivés)

```bash
source env/bin/activate
pip install pyinstaller

pyinstaller --onefile --windowed --name ThirdEyeInsight \
  --add-data "$(python3 -c 'import customtkinter, os; print(os.path.dirname(customtkinter.__file__))'):customtkinter/" \
  --hidden-import="PIL._tkinter_finder" \
  main.py
```

L'exécutable final se trouve dans `dist/ThirdEyeInsight`.

### Sur Windows (cmd)

```bat
env\Scripts\activate.bat
pip install pyinstaller

for /f "delims=" %i in ('python -c "import customtkinter, os; print(os.path.dirname(customtkinter.__file__))"') do set CTK_PATH=%i

pyinstaller --onefile --windowed --name ThirdEyeInsight ^
  --add-data "%CTK_PATH%;customtkinter/" ^
  main.py
```

L'exécutable final se trouve dans `dist\ThirdEyeInsight.exe`.

> **Portable par conception** : où que tu déplaces l'exécutable compilé, les dossiers `decks/`, `exports/`, `imports/` et `.cache/` (base de cartes, images, logs) se créent automatiquement à côté de lui — jamais dans un dossier temporaire système.

## Structure du projet

```
ThirdEyeInsight/
├── main.py              # Interface (customtkinter)
├── calculs.py            # Simulation Monte-Carlo et calculs hypergéométriques
├── translations.py       # Traductions (FR/EN/ES/DE/IT/PT)
├── requirements.txt
├── backend/               # Persistance, sécurité, appels API, import/export YDK
├── tests/                 # Suite de tests (pytest)
├── decks/                 # Créé au premier lancement — tes decks
├── exports/ , imports/    # Créés au premier lancement — échanges .ydk
└── .cache/                 # Créé au premier lancement — base de cartes, images, logs
```

## Tech stack

- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — interface graphique
- [pandas](https://pandas.pydata.org/) / [numpy](https://numpy.org/) — manipulation des données de deck et simulation vectorisée
- [scipy](https://scipy.org/) — loi hypergéométrique
- [Pillow](https://python-pillow.org/) — traitement des images de cartes
- [requests](https://requests.readthedocs.io/) — appels à l'API YGOPRODeck

## Tests

```bash
pip install pytest
pytest tests/ -v
```

## Remerciements

- [YGOPRODeck](https://ygoprodeck.com/) pour son API publique de base de cartes et d'images, utilisée conformément à ses [règles d'usage](https://ygoprodeck.com/api-guide/).

---

Yu-Gi-Oh! est une marque déposée de Konami. Ce projet est un outil non officiel créé par un fan, sans affiliation avec Konami ou Studio Dice.
